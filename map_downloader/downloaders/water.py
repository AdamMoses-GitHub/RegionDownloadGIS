"""Water features downloader (OSM vector polygons)."""

from pathlib import Path
from typing import Optional
import logging
import time

import geopandas as gpd
import requests

from map_downloader.core.bbox import BoundingBox
from map_downloader.downloaders.base import DownloaderBase


logger = logging.getLogger(__name__)


class WaterDownloader(DownloaderBase):
    """Download water polygons from OSM via Overpass."""

    OVERPASS_ENDPOINTS = (
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
        "https://lz4.overpass-api.de/api/interpreter",
        "https://overpass.openstreetmap.ru/api/interpreter",
        "https://overpass.osm.jp/api/interpreter",
    )
    MAX_WARNING_LOGS = 5

    def __init__(self, cache_manager=None, progress_callback=None):
        super().__init__("water", cache_manager, progress_callback)
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
        include_vector: bool = True,
        **kwargs,
    ) -> dict:
        output_path = Path(output_path)
        output_path.mkdir(parents=True, exist_ok=True)
        self._overpass_issue_count = 0

        self._set_cache_options(include_vector=include_vector)
        cache_key = self._build_cache_key(bbox, resolution_m)
        cached_files = self._get_cached_files_if_valid(cache_key)
        if cached_files is not None:
            self._report_progress(100, "Loaded from cache")
            return {
                "success": True,
                "files": cached_files,
                "message": "Loaded from cache",
                "cache_hit": True,
            }

        if not include_vector:
            return {
                "success": False,
                "files": [],
                "message": "Water layer disabled",
                "cache_hit": False,
            }

        try:
            self._report_progress(25, "Downloading OSM water polygons...")
            vector_file = self._download_osm_water(bbox, output_path)
            if not vector_file:
                if self._overpass_issue_count > 0:
                    message = "No water data downloaded; source degraded (OSM Overpass throttled/unreachable)"
                else:
                    message = "No water data downloaded"
                return {
                    "success": False,
                    "files": [],
                    "message": message,
                    "cache_hit": False,
                }

            self._report_progress(85, "OSM water polygons saved")
            result = {
                "success": True,
                "files": [str(vector_file)],
                "message": "Downloaded 1 water dataset",
                "cache_hit": False,
            }
            if self.cache_manager:
                self.cache_manager.put(cache_key, result["files"])
            self._report_progress(100, "Water download complete")
            return result
        except Exception as e:
            return {
                "success": False,
                "files": [],
                "message": f"Water download failed: {str(e)}",
                "cache_hit": False,
            }

    def _download_osm_water(self, bbox: BoundingBox, output_path: Path) -> Optional[Path]:
        try:
            bbox_wgs84 = bbox.to_polygon_wgs84()
            minx, miny, maxx, maxy = bbox_wgs84.bounds

            query = f"""
            [out:json][timeout:180];
            (
                way["natural"="water"]({miny},{minx},{maxy},{maxx});
                way["waterway"="riverbank"]({miny},{minx},{maxy},{maxx});
                way["landuse"="reservoir"]({miny},{minx},{maxy},{maxx});
                way["water"]({miny},{minx},{maxy},{maxx});
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
                if not any(
                    [
                        tags.get("natural") == "water",
                        tags.get("waterway") == "riverbank",
                        tags.get("landuse") == "reservoir",
                        "water" in tags,
                    ]
                ):
                    continue

                features.append(
                    {
                        "type": "Feature",
                        "geometry": {"type": "Polygon", "coordinates": [ring]},
                        "properties": {
                            "source": "OSM",
                            "osm_id": element.get("id"),
                            **tags,
                        },
                    }
                )

            if not features:
                return None

            gdf = gpd.GeoDataFrame.from_features(features)
            gdf = gdf.set_crs("EPSG:4326")

            output_file = output_path / "water_osm.geojson"
            gdf.to_file(output_file, driver="GeoJSON")
            return output_file if output_file.exists() else None
        except Exception as e:
            logger.warning(f"OSM water error: {e}")
            return None

    def _query_overpass_json(self, query: str) -> Optional[dict]:
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
                                f"Water Overpass endpoint {endpoint} returned invalid JSON: {exc}"
                            )
                            continue

                    if resp.status_code in (429, 500, 502, 503, 504):
                        self._overpass_issue_count += 1
                        backoff_s = 1.0 + attempt
                        self._warn_overpass_limited(
                            f"Water Overpass endpoint {endpoint} returned HTTP {resp.status_code}; retrying in {backoff_s:.0f}s"
                        )
                        time.sleep(backoff_s)
                        continue

                    self._overpass_issue_count += 1
                    self._warn_overpass_limited(
                        f"Water Overpass endpoint {endpoint} returned HTTP {resp.status_code}"
                    )
                except Exception as exc:
                    self._overpass_issue_count += 1
                    self._warn_overpass_limited(
                        f"Water Overpass endpoint {endpoint} failed: {exc}"
                    )
        return None
