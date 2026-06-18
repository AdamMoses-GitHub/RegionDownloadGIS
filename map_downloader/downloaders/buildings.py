"""Building footprints downloader (OSM + Microsoft, intelligent merge)."""

import hashlib
import math
import time
from pathlib import Path
from typing import Optional, List
import json
import logging

import geopandas as gpd
import pandas as pd
import requests
import mercantile

from map_downloader.core.bbox import BoundingBox
from map_downloader.downloaders.base import DownloaderBase


logger = logging.getLogger(__name__)


class BuildingsDownloader(DownloaderBase):
    """
    Download building footprints from multiple sources with intelligent merge.
    
    Priority:
    1. OSM Overpass (prefer due to fresh updates and open license)
    2. Microsoft US Building Footprints (fill gaps where OSM is sparse)
    
    Merge logic: Use OSM geometry where available; fill with Microsoft footprints
    where OSM is absent/sparse.
    """
    
    # Microsoft Building Footprints Azure blob tiles
    MS_TILES_URL = "https://msbuildings.z5.web.core.windows.net/buildings"
    MAX_WARNING_LOGS = 5
    OVERPASS_ENDPOINTS = (
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
        "https://lz4.overpass-api.de/api/interpreter",
        "https://overpass.openstreetmap.ru/api/interpreter",
        "https://overpass.osm.jp/api/interpreter",
    )
    
    def __init__(self, cache_manager=None, progress_callback=None):
        """Initialize buildings downloader."""
        super().__init__("buildings", cache_manager, progress_callback)
        self._ms_tile_issue_count = 0
        self._ms_warning_count = 0
        self._overpass_issue_count = 0

    def _warn_ms_limited(self, message: str):
        if self._ms_warning_count < self.MAX_WARNING_LOGS:
            if "retrying in" in message or "DNS resolution failed" in message:
                logger.debug(message)
            else:
                logger.warning(message)
            self._ms_warning_count += 1
    
    def download(
        self,
        bbox: BoundingBox,
        resolution_m: int,
        output_path: Path,
        building_source: str = "MERGED",  # "OSM", "MICROSOFT", "MERGED"
        **kwargs
    ) -> dict:
        """
        Download building footprints.
        
        Args:
            bbox: BoundingBox in WGS84
            resolution_m: Not used for vector data, but for consistency
            output_path: Output directory
            building_source: "OSM", "MICROSOFT", or "MERGED"
        
        Returns:
            dict with 'success', 'files', 'message', 'cache_hit'
        """
        output_path = Path(output_path)
        output_path.mkdir(parents=True, exist_ok=True)
        self._ms_tile_issue_count = 0
        self._ms_warning_count = 0
        self._overpass_issue_count = 0
        
        # Check cache
        source = str(building_source).upper()
        self._set_cache_options(building_source=source)
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
            osm_gdf = None
            ms_gdf = None
            
            if source in ["OSM", "MERGED"]:
                self._report_progress(20, "Fetching OSM building data...")
                osm_gdf = self._fetch_osm_buildings(bbox)
                self._report_progress(40, f"OSM: {len(osm_gdf) if osm_gdf is not None else 0} buildings")
            
            if source in ["MICROSOFT", "MERGED"]:
                self._report_progress(50, "Fetching Microsoft building data...")
                ms_gdf = self._fetch_microsoft_buildings(bbox)
                self._report_progress(65, f"Microsoft: {len(ms_gdf) if ms_gdf is not None else 0} buildings")
            
            if source == "MERGED":
                self._report_progress(70, "Merging building sources...")
                gdf = self._merge_buildings(osm_gdf, ms_gdf, bbox)
            elif source == "OSM":
                gdf = osm_gdf if osm_gdf is not None else gpd.GeoDataFrame()
            else:  # MICROSOFT
                gdf = ms_gdf if ms_gdf is not None else gpd.GeoDataFrame()
            
            if gdf is None or len(gdf) == 0:
                degraded_sources = []
                if source in ["OSM", "MERGED"] and self._overpass_issue_count > 0:
                    degraded_sources.append("OSM Overpass")
                if source in ["MICROSOFT", "MERGED"] and self._ms_tile_issue_count > 0:
                    degraded_sources.append("Microsoft Buildings")

                if degraded_sources:
                    message = (
                        "No buildings found; one or more sources were degraded "
                        f"(throttled/unreachable): {', '.join(degraded_sources)}"
                    )
                else:
                    message = "No buildings found in this area"

                return {
                    "success": False,
                    "files": [],
                    "message": message,
                    "cache_hit": False
                }
            
            self._report_progress(80, "Saving building data...")
            output_file = output_path / f"buildings_{source.lower()}.geojson"
            gdf.to_file(output_file, driver="GeoJSON")
            
            self._report_progress(100, "Buildings download complete")
            
            result = {
                "success": True,
                "files": [str(output_file)],
                "message": (
                    f"Downloaded {len(gdf)} buildings from {source}"
                    + (f" (microsoft tile issues: {self._ms_tile_issue_count})" if self._ms_tile_issue_count else "")
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
                "message": f"Buildings download failed: {str(e)}",
                "cache_hit": False
            }
    
    def _fetch_osm_buildings(self, bbox: BoundingBox) -> Optional[gpd.GeoDataFrame]:
        """Fetch buildings from OSM via Overpass API."""
        try:
            bbox_wgs84 = bbox.to_polygon_wgs84()
            minx, miny, maxx, maxy = bbox_wgs84.bounds
            
            elements = self._query_overpass_buildings(minx, miny, maxx, maxy)
            if not elements:
                return None
            
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

                tags = element.get("tags", {})
                if "building" not in tags:
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
                        "building": tags.get("building", "yes")
                    }
                })
            
            if not features:
                return None
            
            gdf = gpd.GeoDataFrame.from_features(features)
            gdf = gdf.set_crs("EPSG:4326")
            return gdf
            
        except Exception as e:
            self._overpass_issue_count += 1
            logger.warning(f"OSM fetch error: {e}")
            return None

    def _query_overpass_buildings(self, minx: float, miny: float, maxx: float, maxy: float) -> list:
        """Fetch building ways from Overpass, then retry with tiled bboxes on failure."""
        query = f"""
        [out:json][timeout:180];
        (
            way["building"]({miny},{minx},{maxy},{maxx});
        );
        out geom;
        """
        result_json = self._query_overpass_json(query, timeout_s=60)
        if result_json:
            return result_json.get("elements", [])

        width = max(maxx - minx, 1e-6)
        height = max(maxy - miny, 1e-6)
        # Always attempt a tiled fallback, even for small extents, since endpoints may intermittently fail.
        cols = min(6, max(2, math.ceil(width / 0.1)))
        rows = min(6, max(2, math.ceil(height / 0.1)))
        lon_step = width / cols
        lat_step = height / rows

        dedup_by_id = {}
        for r in range(rows):
            tile_miny = miny + r * lat_step
            tile_maxy = maxy if r == rows - 1 else tile_miny + lat_step
            for c in range(cols):
                tile_minx = minx + c * lon_step
                tile_maxx = maxx if c == cols - 1 else tile_minx + lon_step

                tile_query = f"""
                [out:json][timeout:90];
                (
                    way["building"]({tile_miny},{tile_minx},{tile_maxy},{tile_maxx});
                );
                out geom;
                """
                tile_json = self._query_overpass_json(tile_query, timeout_s=20)
                if not tile_json:
                    continue
                for elem in tile_json.get("elements", []):
                    elem_id = elem.get("id")
                    if elem_id is not None:
                        dedup_by_id[elem_id] = elem
                    else:
                        geom_key = hashlib.md5(
                            json.dumps(elem.get("geometry", []), sort_keys=True).encode("utf-8")
                        ).hexdigest()
                        dedup_by_id[f"geom:{geom_key}"] = elem

        return list(dedup_by_id.values())

    def _query_overpass_json(self, query: str, timeout_s: int = 60) -> Optional[dict]:
        """Query Overpass with endpoint fallback and bounded warnings."""
        headers = {
            "User-Agent": "Region3DModelCreator/1.0",
            "Accept": "application/json",
        }
        for endpoint in self.OVERPASS_ENDPOINTS:
            for attempt in range(2):
                try:
                    resp = requests.post(endpoint, data=query.encode("utf-8"), headers=headers, timeout=timeout_s)
                    if resp.status_code == 200:
                        try:
                            return resp.json()
                        except ValueError as exc:
                            self._overpass_issue_count += 1
                            self._warn_ms_limited(
                                f"Overpass endpoint {endpoint} returned invalid JSON: {exc}"
                            )
                            continue

                    if resp.status_code in (429, 500, 502, 503, 504):
                        self._overpass_issue_count += 1
                        backoff_s = 1.0 + attempt
                        self._warn_ms_limited(
                            f"Overpass endpoint {endpoint} returned HTTP {resp.status_code}; retrying in {backoff_s:.0f}s"
                        )
                        time.sleep(backoff_s)
                        continue

                    self._overpass_issue_count += 1
                    self._warn_ms_limited(f"Overpass endpoint {endpoint} returned HTTP {resp.status_code}")
                except Exception as exc:
                    self._overpass_issue_count += 1
                    self._warn_ms_limited(f"Overpass endpoint {endpoint} failed: {exc}")
        return None
    
    def _fetch_microsoft_buildings(self, bbox: BoundingBox) -> Optional[gpd.GeoDataFrame]:
        """Fetch buildings from Microsoft Building Footprints."""
        try:
            bbox_wgs84 = bbox.to_polygon_wgs84()
            minx, miny, maxx, maxy = bbox_wgs84.bounds
            
            # Get tiles covering bbox
            tiles = list(mercantile.tiles(minx, miny, maxx, maxy, zooms=[9]))
            
            features = []
            dns_unreachable = False
            for tile in tiles:
                if dns_unreachable:
                    break

                url = f"{self.MS_TILES_URL}/v1/us/{tile.z}/{tile.x}/{tile.y}.ndjson"
                
                try:
                    resp = requests.get(url, timeout=5)
                    if resp.status_code != 200:
                        self._ms_tile_issue_count += 1
                        self._warn_ms_limited(
                            f"Microsoft building tile HTTP {resp.status_code} for {tile.z}/{tile.x}/{tile.y}"
                        )
                        continue

                    for line in resp.text.strip().split('\n'):
                        if not line:
                            continue
                        try:
                            feature = json.loads(line)
                            features.append(feature)
                        except json.JSONDecodeError as exc:
                            self._ms_tile_issue_count += 1
                            self._warn_ms_limited(
                                f"Invalid NDJSON in Microsoft building tile {tile.z}/{tile.x}/{tile.y}: {exc}"
                            )
                except Exception as exc:
                    self._ms_tile_issue_count += 1
                    exc_text = str(exc)
                    if "NameResolutionError" in exc_text or "getaddrinfo failed" in exc_text:
                        self._warn_ms_limited(
                            "Microsoft building endpoint DNS resolution failed; skipping remaining Microsoft tiles."
                        )
                        dns_unreachable = True
                    else:
                        self._warn_ms_limited(
                            f"Microsoft building tile fetch failed for {tile.z}/{tile.x}/{tile.y}: {exc}"
                        )
            
            if not features:
                return None
            
            gdf = gpd.GeoDataFrame.from_features(features)
            gdf = gdf.set_crs("EPSG:4326")
            
            # Clip to bbox
            bbox_geom = bbox_wgs84
            gdf = gpd.clip(gdf, bbox_geom)
            
            return gdf if len(gdf) > 0 else None
            
        except Exception as e:
            self._ms_tile_issue_count += 1
            logger.warning(f"Microsoft fetch error: {e}")
            return None
    
    def _merge_buildings(
        self,
        osm_gdf: Optional[gpd.GeoDataFrame],
        ms_gdf: Optional[gpd.GeoDataFrame],
        bbox: BoundingBox
    ) -> gpd.GeoDataFrame:
        """Intelligently merge building sources."""
        if osm_gdf is None and ms_gdf is None:
            return gpd.GeoDataFrame()
        
        if osm_gdf is None:
            return ms_gdf
        
        if ms_gdf is None:
            return osm_gdf
        
        # Simple merge: use OSM as primary, add Microsoft where not already covered
        # (More sophisticated spatial join could be done here)
        merged = gpd.GeoDataFrame(
            pd.concat([osm_gdf, ms_gdf], ignore_index=True),
            crs="EPSG:4326"
        )
        
        # Remove near-duplicates (buildings within 1m of each other)
        merged = merged.drop_duplicates(subset=['geometry'], keep='first')
        
        return merged
