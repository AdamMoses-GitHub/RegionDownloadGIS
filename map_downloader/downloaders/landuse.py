"""Land use/land cover downloader (NLCD raster + OSM vector)."""

from pathlib import Path
from typing import Optional
import logging
import time

import requests
import geopandas as gpd

from map_downloader.core.bbox import BoundingBox
from map_downloader.downloaders.base import DownloaderBase


logger = logging.getLogger(__name__)


class LanduseDownloader(DownloaderBase):
    """
    Download land use/land cover data from multiple sources.
    
    - NLCD 30m raster (CONUS, 2021 edition via MRLC WCS)
    - OSM vector polygons (land use, natural, leisure)
    """
    
    # MRLC NLCD WCS endpoint
    MRLC_WCS = "https://www.mrlc.gov/geoserver/ows"
    OVERPASS_ENDPOINTS = (
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
        "https://lz4.overpass-api.de/api/interpreter",
        "https://overpass.openstreetmap.ru/api/interpreter",
        "https://overpass.osm.jp/api/interpreter",
    )
    MAX_WARNING_LOGS = 5
    
    def __init__(self, cache_manager=None, progress_callback=None):
        """Initialize landuse downloader."""
        super().__init__("landuse", cache_manager, progress_callback)
        self._overpass_warning_count = 0
        self._overpass_issue_count = 0

    def _warn_overpass_limited(self, message: str):
        if self._overpass_warning_count < self.MAX_WARNING_LOGS:
            if "retrying in" in message:
                logger.debug(message)
            else:
                logger.warning(message)
            self._overpass_warning_count += 1
    
    def download(
        self,
        bbox: BoundingBox,
        resolution_m: int,
        output_path: Path,
        include_raster: bool = True,
        include_vector: bool = True,
        **kwargs
    ) -> dict:
        """
        Download land use data.
        
        Args:
            bbox: BoundingBox in WGS84
            resolution_m: Target resolution (NLCD is 30m native)
            output_path: Output directory
            include_raster: Include NLCD raster
            include_vector: Include OSM vector polygons
        
        Returns:
            dict with 'success', 'files', 'message', 'cache_hit'
        """
        output_path = Path(output_path)
        output_path.mkdir(parents=True, exist_ok=True)
        self._overpass_issue_count = 0
        
        # Check cache
        self._set_cache_options(include_raster=include_raster, include_vector=include_vector)
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
            files = []
            
            if include_raster:
                self._report_progress(20, "Downloading NLCD raster...")
                raster_file = self._download_nlcd_raster(bbox, output_path)
                if raster_file:
                    files.append(str(raster_file))
                    self._report_progress(50, "NLCD raster saved")
            
            if include_vector:
                self._report_progress(60, "Downloading OSM land use polygons...")
                vector_file = self._download_osm_landuse(bbox, output_path)
                if vector_file:
                    files.append(str(vector_file))
                    self._report_progress(85, "OSM polygons saved")
            
            if not files:
                if include_vector and self._overpass_issue_count > 0:
                    message = "No land use data downloaded; source degraded (OSM Overpass throttled/unreachable)"
                else:
                    message = "No land use data downloaded"
                return {
                    "success": False,
                    "files": [],
                    "message": message,
                    "cache_hit": False
                }
            
            self._report_progress(100, "Land use download complete")
            
            result = {
                "success": True,
                "files": files,
                "message": f"Downloaded {len(files)} land use datasets",
                "cache_hit": False
            }
            
            if self.cache_manager:
                self.cache_manager.put(cache_key, result["files"])
            
            return result
            
        except Exception as e:
            return {
                "success": False,
                "files": [],
                "message": f"Land use download failed: {str(e)}",
                "cache_hit": False
            }
    
    def _download_nlcd_raster(self, bbox: BoundingBox, output_path: Path) -> Optional[Path]:
        """Download NLCD via MRLC WCS GetCoverage request."""
        try:
            bbox_wgs84 = bbox.to_polygon_wgs84()
            minx, miny, maxx, maxy = bbox_wgs84.bounds
            
            # WCS GetCoverage request for NLCD 2021
            params = {
                "service": "WCS",
                "version": "2.0.1",
                "request": "GetCoverage",
                "coverageId": "NLCD_2021_Land_Cover_L48_20230630",
                "format": "image/tiff",
                "subset": [
                    f"x({minx},{maxx})",
                    f"y({miny},{maxy})"
                ]
            }
            
            resp = requests.get(self.MRLC_WCS, params=params, timeout=30)
            if resp.status_code != 200:
                return None
            
            output_file = output_path / "nlcd_2021_30m.tif"
            with open(output_file, 'wb') as f:
                f.write(resp.content)
            
            return output_file if output_file.exists() else None
            
        except Exception as e:
            print(f"NLCD download error: {e}")
            return None
    
    def _download_osm_landuse(self, bbox: BoundingBox, output_path: Path) -> Optional[Path]:
        """Download land use polygons from OSM via Overpass."""
        try:
            bbox_wgs84 = bbox.to_polygon_wgs84()
            minx, miny, maxx, maxy = bbox_wgs84.bounds
            
            # Overpass query for land use and leisure (water is handled by WaterDownloader).
            query = f"""
            [out:json][timeout:180];
            (
                way["landuse"]({miny},{minx},{maxy},{maxx});
                way["leisure"]({miny},{minx},{maxy},{maxx});
            );
            out geom;
            """

            result_json = self._query_overpass_json(query)
            if not result_json:
                return None

            elements = result_json.get("elements", [])
            
            features = []
            for element in elements:
                if element.get("type") != "way":
                    continue
                coords = element.get("geometry") or []
                if len(coords) < 3:
                    continue
                ring = [[pt["lon"], pt["lat"]] for pt in coords if "lon" in pt and "lat" in pt]
                if len(ring) < 3:
                    continue
                if ring[0] != ring[-1]:
                    ring.append(ring[0])

                tags = dict(element.get("tags", {}))
                if not any(k in tags for k in ("landuse", "leisure")):
                    continue

                features.append({
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [ring]
                    },
                    "properties": {
                        "source": "OSM",
                        "osm_id": element.get("id"),
                        **tags
                    }
                })
            
            # Relations are intentionally skipped here to avoid malformed geometry output.
            
            if not features:
                return None
            
            gdf = gpd.GeoDataFrame.from_features(features)
            gdf = gdf.set_crs("EPSG:4326")
            
            output_file = output_path / "landuse_osm.geojson"
            gdf.to_file(output_file, driver="GeoJSON")
            
            return output_file if output_file.exists() else None
            
        except Exception as e:
            logger.warning(f"OSM land use error: {e}")
            return None

    def _query_overpass_json(self, query: str) -> Optional[dict]:
        """Query Overpass with endpoint fallback."""
        headers = {
            "User-Agent": "Region3DModelCreator/1.0",
            "Accept": "application/json",
        }
        for endpoint in self.OVERPASS_ENDPOINTS:
            for attempt in range(2):
                try:
                    resp = requests.post(endpoint, data=query.encode("utf-8"), headers=headers, timeout=60)
                    if resp.status_code == 200:
                        try:
                            return resp.json()
                        except ValueError as exc:
                            self._overpass_issue_count += 1
                            self._warn_overpass_limited(
                                f"Landuse Overpass endpoint {endpoint} returned invalid JSON: {exc}"
                            )
                            continue

                    if resp.status_code in (429, 500, 502, 503, 504):
                        self._overpass_issue_count += 1
                        backoff_s = 1.0 + attempt
                        self._warn_overpass_limited(
                            f"Landuse Overpass endpoint {endpoint} returned HTTP {resp.status_code}; retrying in {backoff_s:.0f}s"
                        )
                        time.sleep(backoff_s)
                        continue

                    self._overpass_issue_count += 1
                    self._warn_overpass_limited(
                        f"Landuse Overpass endpoint {endpoint} returned HTTP {resp.status_code}"
                    )
                except Exception as exc:
                    self._overpass_issue_count += 1
                    self._warn_overpass_limited(
                        f"Landuse Overpass endpoint {endpoint} failed: {exc}"
                    )
        return None
