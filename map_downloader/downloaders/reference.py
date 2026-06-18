"""Reference map downloader (OSM XYZ tiles + stitching)."""

from pathlib import Path
from typing import Optional, Tuple
import io
import logging

import requests
from PIL import Image
import rasterio
from rasterio.transform import from_bounds
import numpy as np
import mercantile

from map_downloader.core.bbox import BoundingBox
from map_downloader.downloaders.base import DownloaderBase


logger = logging.getLogger(__name__)


class ReferenceDownloader(DownloaderBase):
    """
    Download reference basemap from OSM XYZ tiles.
    
    Stitches tiles at a zoom level to cover the bounding box,
    then exports as GeoTIFF for visual reference.
    """
    
    # OSM tile server (respect rate limiting)
    OSM_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    TILE_SIZE = 256  # pixels
    MAX_RETRIES = 3
    MAX_WARNING_LOGS = 5
    
    def __init__(self, cache_manager=None, progress_callback=None):
        """Initialize reference downloader."""
        super().__init__("reference", cache_manager, progress_callback)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Region3DModelCreator/1.0"})
        self._tile_error_count = 0
        self._tile_warning_count = 0

    def _warn_limited(self, message: str):
        if self._tile_warning_count < self.MAX_WARNING_LOGS:
            logger.warning(message)
            self._tile_warning_count += 1
    
    def download(
        self,
        bbox: BoundingBox,
        resolution_m: int,
        output_path: Path,
        zoom_level: Optional[int] = None,
        **kwargs
    ) -> dict:
        """
        Download reference basemap tiles.
        
        Args:
            bbox: BoundingBox in WGS84
            resolution_m: Not used (tiles are pre-rendered)
            output_path: Output directory
            zoom_level: OSM zoom level (auto-select if None)
        
        Returns:
            dict with 'success', 'files', 'message', 'cache_hit'
        """
        output_path = Path(output_path)
        output_path.mkdir(parents=True, exist_ok=True)
        self._tile_error_count = 0
        self._tile_warning_count = 0

        effective_zoom = zoom_level if zoom_level is not None else self._select_zoom_level(bbox)
        
        # Check cache
        self._set_cache_options(zoom=effective_zoom)
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
            bbox_wgs84 = bbox.to_polygon_wgs84()
            minx, miny, maxx, maxy = bbox_wgs84.bounds
            
            zoom_level = effective_zoom
            
            self._report_progress(10, f"Fetching tiles at zoom {zoom_level}...")
            
            # Get tiles covering bbox
            tiles = list(mercantile.tiles(minx, miny, maxx, maxy, zooms=[zoom_level]))
            
            if not tiles:
                return {
                    "success": False,
                    "files": [],
                    "message": f"No tiles found at zoom {zoom_level}",
                    "cache_hit": False
                }
            
            self._report_progress(20, f"Downloading {len(tiles)} tiles...")
            
            # Download and stitch tiles
            stitched_image, bounds = self._stitch_tiles(tiles)
            
            if stitched_image is None:
                issue_note = f" (tile issues: {self._tile_error_count})" if self._tile_error_count else ""
                return {
                    "success": False,
                    "files": [],
                    "message": f"Failed to download tiles{issue_note}",
                    "cache_hit": False
                }
            
            self._report_progress(70, "Converting to GeoTIFF...")
            
            # Save as GeoTIFF
            output_file = output_path / f"reference_zoom{zoom_level}.tif"
            self._save_as_geotiff(stitched_image, bounds, output_file)
            
            self._report_progress(100, "Reference map complete")
            
            result = {
                "success": True,
                "files": [str(output_file)],
                "message": (
                    f"Downloaded reference tiles at zoom {zoom_level}"
                    + (f" (tile issues: {self._tile_error_count})" if self._tile_error_count else "")
                ),
                "cache_hit": False
            }
            
            if self.cache_manager:
                self.cache_manager.put(cache_key, result["files"])
            
            return result
            
        except Exception as e:
            return {
                "success": False,
                "files": [],
                "message": f"Reference download failed: {str(e)}",
                "cache_hit": False
            }
    
    def _select_zoom_level(self, bbox: BoundingBox) -> int:
        """
        Auto-select appropriate zoom level based on bbox size.
        
        - Zoom 15 for small areas (< 5 km)
        - Zoom 13 for medium areas (5-50 km)
        - Zoom 11 for large areas (> 50 km)
        """
        width_km = bbox.width_km()
        height_km = bbox.height_km()
        max_dim = max(width_km, height_km)
        
        if max_dim < 5:
            return 15
        elif max_dim < 50:
            return 13
        else:
            return 11
    
    def _stitch_tiles(self, tiles: list) -> Tuple[Optional[Image.Image], dict]:
        """Download and stitch tiles into a single image."""
        if not tiles:
            return None, {}
        
        # Create grid
        xs = sorted(set(t.x for t in tiles))
        ys = sorted(set(t.y for t in tiles))
        
        grid_width = len(xs)
        grid_height = len(ys)
        
        img_width = grid_width * self.TILE_SIZE
        img_height = grid_height * self.TILE_SIZE
        
        # Create blank canvas
        stitched = Image.new('RGB', (img_width, img_height), color='white')
        
        # Download and paste tiles
        failed_count = 0
        for tile in tiles:
            xi = xs.index(tile.x)
            yi = ys.index(tile.y)
            
            x_offset = xi * self.TILE_SIZE
            y_offset = yi * self.TILE_SIZE
            
            tile_image = self._fetch_tile(tile)
            if tile_image:
                stitched.paste(tile_image, (x_offset, y_offset))
            else:
                failed_count += 1
        
        if failed_count > len(tiles) * 0.5:
            return None, {}
        
        # Calculate stitched bounds from tile edges
        z = tiles[0].z
        west = mercantile.bounds(min(xs), min(ys), z).west
        east = mercantile.bounds(max(xs), min(ys), z).east
        north = mercantile.bounds(min(xs), min(ys), z).north
        south = mercantile.bounds(min(xs), max(ys), z).south
        bounds = (west, south, east, north)
        
        return stitched, bounds
    
    def _fetch_tile(self, tile) -> Optional[Image.Image]:
        """Fetch a single tile with retries."""
        url = self.OSM_TILE_URL.format(z=tile.z, x=tile.x, y=tile.y)
        
        for attempt in range(self.MAX_RETRIES):
            try:
                resp = self.session.get(url, timeout=5)
                if resp.status_code == 200:
                    return Image.open(io.BytesIO(resp.content)).convert("RGB")
                self._tile_error_count += 1
                self._warn_limited(
                    f"Reference tile fetch returned HTTP {resp.status_code} for {tile.z}/{tile.x}/{tile.y}"
                )
            except Exception as exc:
                self._tile_error_count += 1
                self._warn_limited(
                    f"Reference tile fetch failed for {tile.z}/{tile.x}/{tile.y} (attempt {attempt + 1}/{self.MAX_RETRIES}): {exc}"
                )
        
        return None
    
    def _save_as_geotiff(
        self,
        image: Image.Image,
        bounds: Tuple[float, float, float, float],
        output_path: Path
    ):
        """Save PIL image as GeoTIFF with bounds."""
        # Convert PIL image to numpy array
        img_array = np.array(image)
        
        # Handle RGBA/RGB
        if len(img_array.shape) == 3:
            if img_array.shape[2] == 4:
                img_array = img_array[:, :, :3]  # Drop alpha
        
        # Create transform from bounds
        transform = from_bounds(bounds[0], bounds[1], bounds[2], bounds[3],
                                 img_array.shape[1], img_array.shape[0])
        
        # Write GeoTIFF
        with rasterio.open(
            output_path,
            'w',
            driver='GTiff',
            height=img_array.shape[0],
            width=img_array.shape[1],
            count=3 if len(img_array.shape) == 3 else 1,
            dtype=img_array.dtype,
            crs='EPSG:4326',
            transform=transform,
            compress='deflate'
        ) as dst:
            if len(img_array.shape) == 3:
                for i in range(3):
                    dst.write(img_array[:, :, i], i + 1)
            else:
                dst.write(img_array, 1)
