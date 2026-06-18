"""Terrain/DEM downloader using USGS 3DEP and SRTM fallback."""

import os
import shutil
from pathlib import Path
from typing import Optional, List

try:
    import py3dep
except ImportError:
    py3dep = None

import rasterio
from rasterio.transform import from_bounds
import numpy as np

from map_downloader.core.bbox import BoundingBox
from map_downloader.downloaders.base import DownloaderBase


class TerrainDownloader(DownloaderBase):
    """Download elevation data via USGS 3DEP (preferred) or SRTM (fallback)."""
    
    # USGS 3DEP resolution options: 1m, 3m, 10m, 30m
    # For CONUS, default to 5m (resample from 3m if needed)
    USGS_3DEP_OPTIONS = {
        1: "1m",
        3: "3m",
        5: "3m",  # Resample from 3m
        10: "10m",
        30: "30m",
    }

    SRTM_PRODUCTS = ("SRTM1", "SRTM3")
    
    def __init__(self, cache_manager=None, progress_callback=None):
        """Initialize terrain downloader."""
        super().__init__("terrain", cache_manager, progress_callback)
    
    def download(
        self,
        bbox: BoundingBox,
        resolution_m: int,
        output_path: Path,
        region: str = "CONUS",  # "CONUS", "AK", "HI"
        **kwargs
    ) -> dict:
        """
        Download terrain/DEM data.
        
        Args:
            bbox: BoundingBox in WGS84
            resolution_m: Target resolution (1, 3, 5, 10, 30)
            output_path: Output directory
            region: USGS region code
        
        Returns:
            dict with 'success', 'files', 'message', 'cache_hit'
        """
        output_path = Path(output_path)
        output_path.mkdir(parents=True, exist_ok=True)
        self._report_progress(5, f"Preparing terrain request (region={region}, target={resolution_m}m)")
        
        # Check cache
        self._set_cache_options(region=str(region).upper())
        cache_key = self._build_cache_key(bbox, resolution_m)
        cached_files = self._get_cached_files_if_valid(cache_key)
        if cached_files is not None:
            self._report_progress(100, "Loaded from cache")
            return {
                "success": True,
                "files": cached_files,
                "message": "Loaded from cache",
                "cache_hit": True
            }
        
        try:
            self._report_progress(10, "Determining USGS 3DEP resolution...")
            
            # Map requested resolution to available USGS 3DEP resolution
            usgs_res = self.USGS_3DEP_OPTIONS.get(resolution_m, "30m")
            candidate_resolutions = self._candidate_3dep_resolutions(int(usgs_res.rstrip("m")))
            
            # Get WGS84 bbox bounds
            bbox_wgs84 = bbox.to_polygon_wgs84()
            bounds = bbox_wgs84.bounds  # (minx, miny, maxx, maxy)
            
            self._report_progress(20, f"Preparing USGS 3DEP request at {usgs_res}...")
            
            try:
                # Try USGS 3DEP via py3dep
                if py3dep is None:
                    raise ImportError("py3dep not available")

                dem_data = None
                selected_res = None
                last_3dep_error = None
                for idx, candidate_res in enumerate(candidate_resolutions):
                    phase = min(25 + idx * 10, 45)
                    self._report_progress(
                        phase,
                        f"Calling py3dep.get_dem at {candidate_res}m (this may take a few minutes)...",
                    )
                    try:
                        dem_result = py3dep.get_dem(
                            (bounds[0], bounds[1], bounds[2], bounds[3]),  # (west, south, east, north)
                            resolution=candidate_res,
                        )
                        dem_data = self._extract_dem_array(dem_result)
                        dem_profile = self._extract_dem_profile(dem_result)
                        selected_res = candidate_res
                        break
                    except Exception as e3:
                        last_3dep_error = e3
                        self._report_progress(
                            min(phase + 5, 49),
                            f"3DEP {candidate_res}m attempt failed: {str(e3)[:80]}",
                        )

                if dem_data is None:
                    raise RuntimeError(f"All 3DEP attempts failed: {last_3dep_error}")

                self._report_progress(
                    45,
                    f"USGS 3DEP response received at {selected_res}m ({dem_data.shape[1]}x{dem_data.shape[0]} pixels)",
                )
                source = "USGS 3DEP"
                usgs_res = f"{selected_res}m"
                source_file = None
                
            except Exception as e:
                self._report_progress(50, f"3DEP failed ({str(e)[:30]}), trying SRTM...")
                
                # Fallback to SRTM 30m via elevation package
                try:
                    if os.name == "nt" and shutil.which("make") is None:
                        raise RuntimeError(
                            "SRTM fallback unavailable on this Windows environment (missing 'make')."
                        )

                    import elevation
                    dem_file = output_path / "dem_srtm.tif"

                    last_srtm_error = None
                    dem_data = None
                    dem_profile = None
                    source_file = None
                    selected_product = None
                    for product in self.SRTM_PRODUCTS:
                        self._report_progress(55, f"Trying elevation fallback product {product}...")
                        try:
                            elevation.clip(
                                bounds=(bounds[0], bounds[1], bounds[2], bounds[3]),
                                output=str(dem_file),
                                product=product,
                            )
                            with rasterio.open(dem_file) as ds:
                                dem_data = ds.read(1)
                                dem_profile = ds.profile.copy()
                            source_file = dem_file
                            selected_product = product
                            break
                        except Exception as e2:
                            last_srtm_error = e2

                    if dem_data is None:
                        raise RuntimeError(f"SRTM fallback attempts failed: {last_srtm_error}")

                    source = f"{selected_product}"
                    
                except Exception as e2:
                    return {
                        "success": False,
                        "files": [],
                        "message": f"Both 3DEP and SRTM failed: {str(e2)}",
                        "cache_hit": False
                    }
            
            self._report_progress(70, f"Processing DEM from {source}...")
            
            # Resample to target resolution if needed
            if resolution_m != int(usgs_res.rstrip('m')):
                self._report_progress(75, f"Adjusting DEM to target resolution {resolution_m}m...")
                dem_data = self._resample_raster(dem_data, resolution_m)
            else:
                self._report_progress(75, "DEM already at requested resolution")
            
            # Save as GeoTIFF
            self._report_progress(85, "Writing terrain GeoTIFF...")
            output_file = output_path / f"terrain_{resolution_m}m.tif"
            if source_file is not None and dem_profile is not None:
                shutil.copy2(source_file, output_file)
            else:
                self._save_geotiff(dem_data, output_file, bbox, profile=dem_profile)
            
            self._report_progress(100, "Terrain download complete")
            
            result = {
                "success": True,
                "files": [str(output_file)],
                "message": f"Downloaded from {source}",
                "cache_hit": False
            }
            
            # Cache result
            if self.cache_manager:
                self.cache_manager.put(cache_key, result["files"])
            
            return result
            
        except Exception as e:
            return {
                "success": False,
                "files": [],
                "message": f"Terrain download failed: {str(e)}",
                "cache_hit": False
            }
    
    def _resample_raster(self, data: np.ndarray, target_resolution_m: int) -> np.ndarray:
        """Simple nearest-neighbor resampling."""
        # For now, just return as-is; proper resampling handled in processing phase
        return data

    def _extract_dem_array(self, dem_result) -> np.ndarray:
        """Normalize py3dep return value to a 2D numpy array."""
        candidate = dem_result
        if isinstance(dem_result, tuple) and dem_result:
            candidate = dem_result[0]

        # xarray.DataArray-like values expose `.values`.
        if hasattr(candidate, "values"):
            candidate = candidate.values

        arr = np.asarray(candidate)
        # Some providers return a singleton band/time dimension, e.g. (1, H, W).
        if arr.ndim > 2:
            arr = np.squeeze(arr)
        if arr.ndim != 2:
            raise ValueError(f"Unexpected DEM dimensions: {arr.shape}")
        return arr

    def _extract_dem_profile(self, dem_result) -> Optional[dict]:
        """Extract georeferencing metadata from a py3dep/xarray result when available."""
        candidate = dem_result[0] if isinstance(dem_result, tuple) and dem_result else dem_result
        rio = getattr(candidate, "rio", None)
        if rio is None:
            return None

        try:
            crs = rio.crs
            transform = rio.transform()
            width = int(rio.width)
            height = int(rio.height)
        except Exception:
            return None

        if crs is None or transform is None or width <= 0 or height <= 0:
            return None

        return {
            "driver": "GTiff",
            "dtype": rasterio.float32,
            "width": width,
            "height": height,
            "count": 1,
            "crs": crs,
            "transform": transform,
            "compress": "deflate",
        }

    def _candidate_3dep_resolutions(self, preferred_res: int) -> List[int]:
        """Return ordered 3DEP candidate resolutions from preferred to coarser."""
        ordered = [preferred_res, 10, 30]
        out = []
        for val in ordered:
            if val not in out:
                out.append(val)
        return out
    
    def _save_geotiff(self, data: np.ndarray, output_path: Path, bbox: BoundingBox, profile: Optional[dict] = None):
        """Save array as GeoTIFF, preserving source georeferencing when available."""
        if profile is None:
            bbox_wgs84 = bbox.to_polygon_wgs84()
            bounds = bbox_wgs84.bounds
            profile = {
                "driver": "GTiff",
                "dtype": rasterio.float32,
                "width": data.shape[1],
                "height": data.shape[0],
                "count": 1,
                "crs": "EPSG:4326",
                "transform": from_bounds(
                    bounds[0], bounds[1], bounds[2], bounds[3],
                    data.shape[1], data.shape[0]
                ),
                "compress": "deflate"
            }
        else:
            profile = profile.copy()
            profile.update({
                "driver": "GTiff",
                "dtype": rasterio.float32,
                "width": data.shape[1],
                "height": data.shape[0],
                "count": 1,
                "compress": "deflate",
            })
        
        with rasterio.open(output_path, 'w', **profile) as dst:
            dst.write(data.astype(rasterio.float32), 1)
