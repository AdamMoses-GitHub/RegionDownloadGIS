"""Reproject raster and vector data to target CRS (UTM)."""

from pathlib import Path

import rasterio
from rasterio.warp import calculate_default_transform, reproject, Resampling
import geopandas as gpd

from map_downloader.core.bbox import BoundingBox


def reproject_raster(
    input_path: Path,
    output_path: Path,
    target_crs: str,
    resampling_method=Resampling.bilinear
) -> bool:
    """
    Reproject raster to target CRS.
    
    Args:
        input_path: Input GeoTIFF path
        output_path: Output GeoTIFF path
        target_crs: Target CRS as EPSG code (e.g., 'EPSG:32618' for UTM zone 18N)
        resampling_method: Rasterio resampling method (default: bilinear)
    
    Returns:
        bool: True if successful
    """
    try:
        with rasterio.open(input_path) as src:
            if src.crs is None:
                print("Error reprojecting raster: source CRS is undefined")
                return False

            # Calculate transform for target CRS
            transform, width, height = calculate_default_transform(
                src.crs, target_crs, src.width, src.height, *src.bounds
            )
            if width <= 0 or height <= 0:
                print(f"Error reprojecting raster: invalid output size {width}x{height}")
                return False
            
            # Prepare output metadata
            kwargs = src.meta.copy()
            kwargs.update({
                'crs': target_crs,
                'transform': transform,
                'width': width,
                'height': height,
                'compress': 'deflate'
            })
            
            # Reproject
            with rasterio.open(output_path, 'w', **kwargs) as dst:
                for i in range(1, src.count + 1):
                    reproject(
                        source=rasterio.band(src, i),
                        destination=rasterio.band(dst, i),
                        src_transform=src.transform,
                        src_crs=src.crs,
                        dst_transform=transform,
                        dst_crs=target_crs,
                        resampling=resampling_method
                    )
        
        return True
        
    except Exception as e:
        print(f"Error reprojecting raster: {e}")
        return False


def reproject_vector(
    input_path: Path,
    output_path: Path,
    target_crs: str,
    output_format: str = "GeoJSON",
    clip_bbox: BoundingBox | None = None,
) -> bool:
    """
    Reproject vector data to target CRS.
    
    Args:
        input_path: Input vector path (GeoJSON, Shapefile, etc.)
        output_path: Output vector path
        target_crs: Target CRS as EPSG code
        output_format: Output format (GeoJSON, ESRI Shapefile, GeoPackage)
    
    Returns:
        bool: True if successful
    """
    try:
        # Read vector data
        gdf = gpd.read_file(input_path)
        
        # Reproject
        gdf_reprojected = gdf.to_crs(target_crs)

        if clip_bbox is not None:
            clip_geom = gpd.GeoSeries([clip_bbox.to_polygon_wgs84()], crs="EPSG:4326").to_crs(target_crs).iloc[0]
            gdf_reprojected = gdf_reprojected[gdf_reprojected.geometry.intersects(clip_geom)].copy()
            gdf_reprojected.geometry = gdf_reprojected.geometry.intersection(clip_geom)
            gdf_reprojected = gdf_reprojected[
                gdf_reprojected.geometry.notna() & (~gdf_reprojected.geometry.is_empty)
            ].copy()
        
        # Determine driver from format
        driver_map = {
            "GeoJSON": "GeoJSON",
            "Shapefile": "ESRI Shapefile",
            "GeoPackage": "GPKG",
        }
        driver = driver_map.get(output_format, "GeoJSON")
        
        # Write output
        gdf_reprojected.to_file(output_path, driver=driver)
        
        return True
        
    except Exception as e:
        print(f"Error reprojecting vector: {e}")
        return False


def get_utm_epsg(bbox: BoundingBox) -> str:
    """
    Get EPSG code for UTM zone covering the bounding box centroid.
    
    Args:
        bbox: BoundingBox instance
    
    Returns:
        str: EPSG code like 'EPSG:32618'
    """
    # Get centroid
    centroid_lon = (bbox.min_lon + bbox.max_lon) / 2
    centroid_lat = (bbox.min_lat + bbox.max_lat) / 2
    
    # Calculate UTM zone from centroid longitude
    # UTM zones are 6 degrees wide, starting at -180°
    utm_zone = int((centroid_lon + 180) / 6) + 1
    utm_zone = max(1, min(60, utm_zone))
    
    # Determine hemisphere
    is_southern = centroid_lat < 0
    
    # EPSG offset: Northern zones are 32601-32660, Southern are 32701-32760
    if is_southern:
        epsg_code = 32700 + utm_zone
    else:
        epsg_code = 32600 + utm_zone
    
    return f"EPSG:{epsg_code}"


def batch_reproject_rasters(
    input_dir: Path,
    output_dir: Path,
    target_crs: str,
    pattern: str = "*.tif"
) -> int:
    """
    Reproject all rasters in a directory.
    
    Args:
        input_dir: Input directory with rasters
        output_dir: Output directory
        target_crs: Target CRS
        pattern: File pattern to match
    
    Returns:
        int: Number of successfully reprojected files
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    
    count = 0
    for input_file in input_dir.glob(pattern):
        output_file = output_dir / input_file.name.replace('.tif', f'_reprojected.tif')
        
        if reproject_raster(input_file, output_file, target_crs):
            count += 1
            print(f"✓ Reprojected {input_file.name}")
        else:
            print(f"✗ Failed to reproject {input_file.name}")
    
    return count


def batch_reproject_vectors(
    input_dir: Path,
    output_dir: Path,
    target_crs: str,
    pattern: str = "*.geojson"
) -> int:
    """
    Reproject all vectors in a directory.
    
    Args:
        input_dir: Input directory with vectors
        output_dir: Output directory
        target_crs: Target CRS
        pattern: File pattern to match
    
    Returns:
        int: Number of successfully reprojected files
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    
    count = 0
    for input_file in input_dir.glob(pattern):
        output_file = output_dir / input_file.name.replace('.geojson', f'_reprojected.geojson')
        
        if reproject_vector(input_file, output_file, target_crs, "GeoJSON"):
            count += 1
            print(f"✓ Reprojected {input_file.name}")
        else:
            print(f"✗ Failed to reproject {input_file.name}")
    
    return count
