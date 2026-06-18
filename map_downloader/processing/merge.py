"""Merge building data sources and augment with height information."""

from pathlib import Path
from typing import Optional, Dict, List
import warnings
import logging

import geopandas as gpd
import pandas as pd
from shapely.geometry import shape, box
from shapely.ops import unary_union
import numpy as np


logger = logging.getLogger(__name__)


def merge_building_sources(
    osm_path: Optional[Path],
    microsoft_path: Optional[Path],
    output_path: Path,
    merge_strategy: str = "osm_primary"
) -> bool:
    """
    Intelligently merge OSM and Microsoft building footprints.
    
    Merge strategies:
    - 'osm_primary': Use OSM geometry where available, fill with Microsoft
    - 'microsoft_primary': Use Microsoft geometry where available, fill with OSM
    - 'union': Take union of both (may result in overlaps)
    
    Args:
        osm_path: Path to OSM buildings GeoJSON
        microsoft_path: Path to Microsoft buildings GeoJSON
        output_path: Output path for merged GeoJSON
        merge_strategy: Strategy for merging
    
    Returns:
        bool: True if successful
    """
    try:
        gdfs = []
        
        if osm_path and osm_path.exists():
            osm_gdf = gpd.read_file(osm_path)
            osm_gdf['source'] = 'OSM'
            gdfs.append(osm_gdf)
        
        if microsoft_path and microsoft_path.exists():
            ms_gdf = gpd.read_file(microsoft_path)
            ms_gdf['source'] = 'Microsoft'
            gdfs.append(ms_gdf)
        
        if not gdfs:
            print("No building sources provided")
            return False
        
        if len(gdfs) == 1:
            merged = gdfs[0]
        elif merge_strategy == "osm_primary":
            merged = _merge_osm_primary(gdfs[0], gdfs[1] if len(gdfs) > 1 else None)
        elif merge_strategy == "microsoft_primary":
            merged = _merge_microsoft_primary(gdfs[0], gdfs[1] if len(gdfs) > 1 else None)
        else:  # union
            merged = pd.concat(gdfs, ignore_index=True)
        
        # Remove exact duplicates
        merged = merged.drop_duplicates(subset=['geometry'], keep='first')
        
        # Ensure valid geometries
        merged = merged[merged.geometry.is_valid]
        
        # Write output
        merged.to_file(output_path, driver="GeoJSON")
        
        return True
        
    except Exception as e:
        print(f"Error merging building sources: {e}")
        return False


def _merge_osm_primary(osm_gdf: gpd.GeoDataFrame, ms_gdf: Optional[gpd.GeoDataFrame]) -> gpd.GeoDataFrame:
    """Merge with OSM as primary source."""
    if ms_gdf is None or len(ms_gdf) == 0:
        return osm_gdf
    
    # Ensure same CRS
    ms_gdf = ms_gdf.to_crs(osm_gdf.crs)
    
    # Spatial join: keep MS buildings not covered by OSM
    osm_dissolved = osm_gdf.geometry.unary_union
    ms_outside = ms_gdf[~ms_gdf.geometry.within(osm_dissolved)]
    
    # Combine
    merged = pd.concat([osm_gdf, ms_outside], ignore_index=True)
    return merged


def _merge_microsoft_primary(ms_gdf: gpd.GeoDataFrame, osm_gdf: Optional[gpd.GeoDataFrame]) -> gpd.GeoDataFrame:
    """Merge with Microsoft as primary source."""
    if osm_gdf is None or len(osm_gdf) == 0:
        return ms_gdf
    
    osm_gdf = osm_gdf.to_crs(ms_gdf.crs)
    
    ms_dissolved = ms_gdf.geometry.unary_union
    osm_outside = osm_gdf[~osm_gdf.geometry.within(ms_dissolved)]
    
    merged = pd.concat([ms_gdf, osm_outside], ignore_index=True)
    return merged


def augment_with_height(
    buildings_path: Path,
    terrain_path: Path,
    output_path: Path,
    height_mode: str = "estimate"
) -> bool:
    """
    Augment building footprints with height data from terrain.
    
    Height modes:
    - 'flat': Set all heights to 0
    - 'estimate': Estimate from footprint area (simple heuristic)
    - 'mean': Use mean terrain elevation under building
    - 'max': Use max terrain elevation under building
    
    Args:
        buildings_path: Path to buildings GeoJSON/Shapefile
        terrain_path: Path to terrain GeoTIFF
        output_path: Output path for augmented GeoJSON
        height_mode: How to set building heights
    
    Returns:
        bool: True if successful
    """
    if height_mode not in {"flat", "estimate", "mean", "max"}:
        logger.warning(f"Unknown height_mode '{height_mode}'")
        return False

    try:
        import rasterio
        from rasterio.mask import mask
        
        # Read buildings
        buildings_gdf = gpd.read_file(buildings_path)
        
        # Add height column if not present
        if 'height' not in buildings_gdf.columns:
            buildings_gdf['height'] = 0.0
        
        # Open terrain raster
        with rasterio.open(terrain_path) as src:
            terrain_crs = src.crs
            
            # Reproject buildings to terrain CRS if needed
            if buildings_gdf.crs != terrain_crs:
                buildings_gdf = buildings_gdf.to_crs(terrain_crs)
            
            # Process each building
            mask_failures = 0
            for idx, building in buildings_gdf.iterrows():
                geom = building.geometry
                if geom is None or geom.is_empty:
                    buildings_gdf.at[idx, 'height'] = 0.0
                    continue
                
                if height_mode == "flat":
                    buildings_gdf.at[idx, 'height'] = 0.0
                
                elif height_mode == "estimate":
                    # Heuristic: larger buildings tend to be taller
                    area_m2 = geom.area
                    # Simple formula: height ~ sqrt(area) * factor
                    estimated_height = np.sqrt(area_m2) * 0.01  # adjust factor as needed
                    buildings_gdf.at[idx, 'height'] = min(estimated_height, 200)  # cap at 200m
                
                else:  # mean or max from terrain
                    try:
                        # Crop terrain to building bounds
                        terrain_data, _ = mask(src, [geom], crop=True, nodata=np.nan)
                        
                        if terrain_data.size > 0:
                            if height_mode == "mean":
                                height = np.nanmean(terrain_data)
                            else:  # max
                                height = np.nanmax(terrain_data)
                            
                            buildings_gdf.at[idx, 'height'] = float(height) if not np.isnan(height) else 0.0
                    except Exception as exc:
                        mask_failures += 1
                        buildings_gdf.at[idx, 'height'] = 0.0
                        if mask_failures <= 5:
                            logger.warning(f"Height mask failed for building index {idx}: {exc}")
        
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Write output
        buildings_gdf.to_file(output_path, driver="GeoJSON")
        
        return True
        
    except Exception as e:
        logger.warning(f"Error augmenting with height: {e}")
        return False


def compute_building_footprint_stats(buildings_path: Path) -> dict:
    """
    Compute statistics about building footprints.
    
    Args:
        buildings_path: Path to buildings GeoJSON
    
    Returns:
        dict with statistics
    """
    try:
        gdf = gpd.read_file(buildings_path)
        
        # Convert areas to square meters using equal-area projection if needed
        if gdf.crs and gdf.crs.is_geographic:
            gdf_proj = gdf.to_crs("EPSG:6933")
        else:
            gdf_proj = gdf
        
        areas_m2 = gdf_proj.geometry.area
        
        return {
            "total_buildings": len(gdf),
            "total_area_km2": areas_m2.sum() / 1e6,
            "mean_area_m2": areas_m2.mean(),
            "median_area_m2": areas_m2.median(),
            "min_area_m2": areas_m2.min(),
            "max_area_m2": areas_m2.max(),
            "buildings_with_height": int(gdf['height'].notna().sum()) if 'height' in gdf.columns else 0,
            "mean_height_m": gdf['height'].mean() if 'height' in gdf.columns else None,
        }
    except Exception as e:
        print(f"Error computing stats: {e}")
        return {}
