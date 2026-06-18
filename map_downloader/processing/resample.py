"""Crop and resample raster data to target resolution and bounds."""

from pathlib import Path
from typing import Optional

import rasterio
from rasterio.mask import mask
from rasterio.warp import reproject, Resampling
from rasterio.transform import from_bounds
import numpy as np
from shapely.ops import transform as shapely_transform
from pyproj import Transformer

from map_downloader.core.bbox import BoundingBox


def crop_raster(
    input_path: Path,
    output_path: Path,
    bbox: BoundingBox,
    target_crs: Optional[str] = None
) -> bool:
    """
    Crop raster to bounding box.
    
    Args:
        input_path: Input raster path
        output_path: Output raster path
        bbox: BoundingBox to crop to (in raster's native CRS)
        target_crs: If provided, reproject to this CRS before cropping
    
    Returns:
        bool: True if successful
    """
    try:
        with rasterio.open(input_path) as src:
            # Reproject WGS84 bbox polygon to source raster CRS for masking
            bbox_poly_wgs84 = bbox.to_polygon_wgs84()
            if src.crs is not None and str(src.crs) != "EPSG:4326":
                transformer = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
                bbox_poly = shapely_transform(transformer.transform, bbox_poly_wgs84)
            else:
                bbox_poly = bbox_poly_wgs84
            
            # Crop raster
            out_image, out_transform = mask(src, [bbox_poly], crop=True)
            
            # Update metadata
            out_meta = src.meta.copy()
            out_meta.update({
                "driver": "GTiff",
                "height": out_image.shape[1],
                "width": out_image.shape[2],
                "transform": out_transform,
                "compress": "deflate"
            })
            
            # Write output
            with rasterio.open(output_path, "w", **out_meta) as dst:
                dst.write(out_image)
        
        return True
        
    except Exception as e:
        print(f"Error cropping raster: {e}")
        return False


def resample_raster(
    input_path: Path,
    output_path: Path,
    target_resolution_m: float,
    resampling_method=Resampling.bilinear
) -> bool:
    """
    Resample raster to target resolution.
    
    Args:
        input_path: Input raster path
        output_path: Output raster path
        target_resolution_m: Target resolution in meters (UTM units)
        resampling_method: Rasterio resampling method
    
    Returns:
        bool: True if successful
    """
    try:
        if target_resolution_m <= 0:
            print(f"Invalid target resolution: {target_resolution_m}m")
            return False

        with rasterio.open(input_path) as src:
            # Convert target resolution to raster units (meters for projected, degrees for geographic)
            target_res_units = float(target_resolution_m)
            if src.crs is not None and getattr(src.crs, "is_geographic", False):
                target_res_units = target_resolution_m / 111320.0

            bounds = src.bounds
            new_width = max(1, int(round((bounds.right - bounds.left) / target_res_units)))
            new_height = max(1, int(round((bounds.top - bounds.bottom) / target_res_units)))
            
            if new_height < 1 or new_width < 1:
                print(f"Invalid target resolution: {target_resolution_m}m results in {new_width}x{new_height} pixels")
                return False
            
            # New transform and output buffer
            new_transform = from_bounds(bounds.left, bounds.bottom, bounds.right, bounds.top, new_width, new_height)
            resampled_data = np.zeros((src.count, new_height, new_width), dtype=src.dtypes[0])

            # Resample each band
            for i in range(1, src.count + 1):
                reproject(
                    source=rasterio.band(src, i),
                    destination=resampled_data[i - 1],
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=new_transform,
                    dst_crs=src.crs,
                    resampling=resampling_method,
                )
            
            # Update metadata
            out_meta = src.meta.copy()
            out_meta.update({
                "driver": "GTiff",
                "height": new_height,
                "width": new_width,
                "transform": new_transform,
                "compress": "deflate"
            })
            
            # Write output
            with rasterio.open(output_path, "w", **out_meta) as dst:
                dst.write(resampled_data.astype(src.meta['dtype']))
        
        return True
        
    except Exception as e:
        print(f"Error resampling raster: {e}")
        return False


def crop_and_resample_raster(
    input_path: Path,
    output_path: Path,
    bbox: BoundingBox,
    target_resolution_m: float,
    target_crs: Optional[str] = None
) -> bool:
    """
    Crop raster to bbox and resample to target resolution.
    
    Args:
        input_path: Input raster path
        output_path: Output raster path
        bbox: BoundingBox to crop to
        target_resolution_m: Target resolution in meters
        target_crs: Optional target CRS
    
    Returns:
        bool: True if successful
    """
    temp_crop = Path(output_path).parent / f"{Path(output_path).stem}_crop.tif"
    try:
        # First crop
        if not crop_raster(input_path, temp_crop, bbox, target_crs):
            return False
        
        # Then resample
        if not resample_raster(temp_crop, output_path, target_resolution_m):
            return False
        
        return True
        
    except Exception as e:
        print(f"Error in crop_and_resample: {e}")
        return False
    finally:
        if temp_crop.exists():
            temp_crop.unlink()


def get_raster_statistics(input_path: Path) -> dict:
    """
    Get basic statistics about a raster file.
    
    Args:
        input_path: Raster path
    
    Returns:
        dict with metadata and stats
    """
    try:
        with rasterio.open(input_path) as src:
            data = src.read()
            
            return {
                "crs": str(src.crs),
                "bounds": src.bounds,
                "width": src.width,
                "height": src.height,
                "resolution": abs(src.transform.a),
                "count": src.count,
                "dtype": str(src.dtypes[0]) if src.dtypes else "unknown",
                "min": float(np.nanmin(data)) if data.size > 0 else None,
                "max": float(np.nanmax(data)) if data.size > 0 else None,
                "mean": float(np.nanmean(data)) if data.size > 0 else None,
                "area_pixels": src.width * src.height,
                "area_m2": (src.width * abs(src.transform.a)) * (src.height * abs(src.transform.e))
            }
    except Exception as e:
        print(f"Error reading raster stats: {e}")
        return {}
