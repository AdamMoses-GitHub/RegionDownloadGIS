"""Road network downloader (OSM vector lines for major/minor streets)."""

from pathlib import Path
from typing import Optional
import logging
import time

import geopandas as gpd
import requests

from map_downloader.core.bbox import BoundingBox
from map_downloader.downloaders.base import DownloaderBase


logger = logging.getLogger(__name__)


class RoadsDownloader(DownloaderBase):
    """Download OSM roads split into major and minor categories."""

    OVERPASS_ENDPOINTS = (
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
        "https://lz4.overpass-api.de/api/interpreter",
        "https://overpass.openstreetmap.ru/api/interpreter",
        "https://overpass.osm.jp/api/interpreter",
    )
    MAX_WARNING_LOGS = 5

    MAJOR_HIGHWAYS = (
        "motorway",
        "motorway_link",
        "trunk",
        "trunk_link",
        "primary",
        "primary_link",
        "secondary",
        "secondary_link",
    )

    MINOR_HIGHWAYS = (
        "tertiary",
        "tertiary_link",
        "unclassified",
        "residential",
        "service",
        "living_street",
        "road",
    )

    def __init__(self, cache_manager=None, progress_callback=None):
        super().__init__("roads", cache_manager, progress_callback)
        self._overpass_warning_count = 0
        self._overpass_issue_count = 0

    def _warn_overpass_limited(self, message: str):
        if self._overpass_warning_count < self.MAX_WARNING_LOGS:
            if (
                "retrying in" in message
                or "timed out" in message.lower()
                or "failed:" in message.lower()
            ):
                logger.debug(message)
            else:
                logger.warning(message)
            self._overpass_warning_count += 1

    def download(
        self,
        bbox: BoundingBox,
        resolution_m: int,
        output_path: Path,
        include_major: bool = True,
        include_minor: bool = False,
        **kwargs,
    ) -> dict:
        output_path = Path(output_path)
        output_path.mkdir(parents=True, exist_ok=True)
        self._overpass_issue_count = 0

        self._set_cache_options(include_major=include_major, include_minor=include_minor)
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

        if not include_major and not include_minor:
            return {
                "success": False,
                "files": [],
                "message": "Road layer disabled",
                "cache_hit": False,
            }

        try:
            self._report_progress(20, "Downloading OSM roads...")
            files = []
            major_count = 0
            minor_count = 0

            if include_major:
                major_file = self._download_roads(
                    bbox,
                    output_path,
                    self.MAJOR_HIGHWAYS,
                    "roads_major_osm.geojson",
                )
                if major_file:
                    files.append(str(major_file))
                    try:
                        major_count = len(gpd.read_file(major_file))
                    except Exception:
                        major_count = 0

            if include_minor:
                minor_file = self._download_roads(
                    bbox,
                    output_path,
                    self.MINOR_HIGHWAYS,
                    "roads_minor_osm.geojson",
                )
                if minor_file:
                    files.append(str(minor_file))
                    try:
                        minor_count = len(gpd.read_file(minor_file))
                    except Exception:
                        minor_count = 0

            if not files:
                if self._overpass_issue_count > 0:
                    message = "No road data downloaded; source degraded (OSM Overpass throttled/unreachable)"
                else:
                    message = "No road data downloaded"
                return {
                    "success": False,
                    "files": [],
                    "message": message,
                    "cache_hit": False,
                }

            self._report_progress(100, "Road download complete")
            result = {
                "success": True,
                "files": files,
                "message": f"Downloaded roads (major={major_count}, minor={minor_count})",
                "cache_hit": False,
            }
            if self.cache_manager:
                self.cache_manager.put(cache_key, result["files"])
            return result
        except Exception as exc:
            return {
                "success": False,
                "files": [],
                "message": f"Road download failed: {exc}",
                "cache_hit": False,
            }

    def _download_roads(
        self,
        bbox: BoundingBox,
        output_path: Path,
        highway_values: tuple[str, ...],
        filename: str,
    ) -> Optional[Path]:
        bbox_wgs84 = bbox.to_polygon_wgs84()
        minx, miny, maxx, maxy = bbox_wgs84.bounds

        tag_pattern = "|".join(highway_values)
        query = f"""
        [out:json][timeout:180];
        (
            way["highway"~"^{tag_pattern}$"]({miny},{minx},{maxy},{maxx});
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
            if len(coords) < 2:
                continue
            line = [[pt["lon"], pt["lat"]] for pt in coords if "lon" in pt and "lat" in pt]
            if len(line) < 2:
                continue

            tags = dict(element.get("tags", {}))
            highway = tags.get("highway")
            if highway not in highway_values:
                continue

            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": line},
                    "properties": {
                        "source": "OSM",
                        "osm_id": element.get("id"),
                        "highway": highway,
                        "name": tags.get("name", ""),
                    },
                }
            )

        if not features:
            return None

        gdf = gpd.GeoDataFrame.from_features(features)
        gdf = gdf.set_crs("EPSG:4326")

        output_file = output_path / filename
        gdf.to_file(output_file, driver="GeoJSON")
        return output_file if output_file.exists() else None

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
                                f"Roads Overpass endpoint {endpoint} returned invalid JSON: {exc}"
                            )
                            continue

                    if resp.status_code in (429, 500, 502, 503, 504):
                        self._overpass_issue_count += 1
                        backoff_s = 1.0 + attempt
                        self._warn_overpass_limited(
                            f"Roads Overpass endpoint {endpoint} returned HTTP {resp.status_code}; retrying in {backoff_s:.0f}s"
                        )
                        time.sleep(backoff_s)
                        continue

                    self._overpass_issue_count += 1
                    self._warn_overpass_limited(
                        f"Roads Overpass endpoint {endpoint} returned HTTP {resp.status_code}"
                    )
                except Exception as exc:
                    self._overpass_issue_count += 1
                    self._warn_overpass_limited(
                        f"Roads Overpass endpoint {endpoint} failed: {exc}"
                    )
        return None
