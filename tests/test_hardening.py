import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import numpy as np
import requests
import rasterio
import mercantile
import geopandas as gpd
from rasterio.transform import from_origin
from affine import Affine
from PySide6.QtWidgets import QApplication

from map_downloader.core.bbox import BoundingBox
from map_downloader.core.cache import CacheManager
from map_downloader.core.project import Project, LayerConfig
from map_downloader.downloaders.buildings import BuildingsDownloader
from map_downloader.downloaders.base import DownloaderBase
from map_downloader.downloaders.landuse import LanduseDownloader
from map_downloader.downloaders.reference import ReferenceDownloader
from map_downloader.downloaders.terrain import TerrainDownloader
from map_downloader.downloaders.water import WaterDownloader
from map_downloader.gui.widgets.bbox_widget import (
    CITY_PRESETS,
    BASE_CITY_PRESETS,
    BboxInputWidget,
)
from map_downloader.gui.pages.p3_output import OutputPage
from map_downloader.export import Exporter
from map_downloader.processing.merge import augment_with_height
from map_downloader.processing.reproject import reproject_raster, reproject_vector, get_utm_epsg
from map_downloader.processing.resample import resample_raster, crop_raster, crop_and_resample_raster


class _DummyDownloader(DownloaderBase):
    def download(self, bbox, resolution_m, output_path, **kwargs):
        return {}


class HardeningTests(unittest.TestCase):
    @staticmethod
    def _ensure_qapp():
        app = QApplication.instance()
        if app is None:
            app = QApplication([])
        return app

    def test_cache_manager_supports_both_call_styles(self):
        with tempfile.TemporaryDirectory() as td:
            cache = CacheManager(td)

            key = cache.make_key("terrain", "abc123", 5)
            cache.put(key, ["file_a.tif"])
            self.assertTrue(cache.has(key))
            self.assertEqual(cache.get(key), ["file_a.tif"])

            cache.put("terrain", "def456", 10, ["file_b.tif"])
            self.assertTrue(cache.has("terrain", "def456", 10))
            self.assertEqual(cache.get("terrain", "def456", 10), ["file_b.tif"])

            cache.cleanup()

    def test_cache_manager_default_global_cache_dir(self):
        path = CacheManager.default_global_cache_dir()
        self.assertTrue(str(path))
        self.assertIn("Region3DModelCreator", str(path))

    def test_cache_manager_fallback_promotes_values(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            primary_dir = root / "primary"
            fallback_dir = root / "fallback"
            payload_file = root / "existing.dat"
            payload_file.write_text("ok", encoding="utf-8")

            fallback = CacheManager(str(fallback_dir))
            primary = CacheManager(str(primary_dir), fallback_cache=fallback)

            key = fallback.make_key("terrain", "abc123", 5.0)
            fallback.put(key, [str(payload_file)])

            value = primary.get(key)
            self.assertEqual(value, [str(payload_file)])
            self.assertEqual(primary.get(key), [str(payload_file)])

            primary.cleanup()
            fallback.cleanup()

    def test_bbox_compatibility_aliases(self):
        bbox = BoundingBox(-74.01, 40.70, -74.00, 40.71)

        self.assertIsNotNone(bbox.to_polygon_wgs84())
        self.assertIsNotNone(bbox.to_polygon_utm())
        self.assertGreater(bbox.width_km(), 0)
        self.assertGreater(bbox.height_km(), 0)
        self.assertGreater(bbox.area_km2(), 0)
        self.assertIsInstance(bbox.hash(), str)

    def test_bbox_from_utm_preserves_strict_bounds(self):
        bbox = BoundingBox.from_corners(
            4636800.0,
            422900.0,
            4641300.0,
            427800.0,
            crs="utm",
            utm_zone=16,
        )

        self.assertTrue(bbox.has_strict_utm_bounds())
        min_e, min_n, max_e, max_n = bbox.get_utm_bounds()
        self.assertEqual(min_e, 422900.0)
        self.assertEqual(min_n, 4636800.0)
        self.assertEqual(max_e, 427800.0)
        self.assertEqual(max_n, 4641300.0)

    def test_reproject_and_resample_raster_smoke(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src_path = root / "src.tif"
            reproj_path = root / "reproj.tif"
            resampled_path = root / "resampled.tif"

            arr = np.arange(100, dtype="float32").reshape(10, 10)
            with rasterio.open(
                src_path,
                "w",
                driver="GTiff",
                height=10,
                width=10,
                count=1,
                dtype="float32",
                crs="EPSG:32618",
                transform=from_origin(500000, 4500000, 1, 1),
            ) as dst:
                dst.write(arr, 1)

            self.assertTrue(reproject_raster(src_path, reproj_path, "EPSG:32618"))
            self.assertTrue(reproj_path.exists())

            self.assertTrue(resample_raster(reproj_path, resampled_path, 2.0))
            self.assertTrue(resampled_path.exists())

    def test_reproject_vector_clips_to_bbox(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src_path = root / "src.geojson"
            out_path = root / "clipped.geojson"
            bbox = BoundingBox(-74.01, 40.70, -74.00, 40.71)

            gdf = gpd.GeoDataFrame(
                {"name": ["inside", "outside"]},
                geometry=gpd.GeoSeries.from_wkt(
                    [
                        "POLYGON ((-74.009 40.701, -74.001 40.701, -74.001 40.709, -74.009 40.709, -74.009 40.701))",
                        "POLYGON ((-74.020 40.720, -74.015 40.720, -74.015 40.725, -74.020 40.725, -74.020 40.720))",
                    ]
                ),
                crs="EPSG:4326",
            )
            gdf.to_file(src_path, driver="GeoJSON")

            self.assertTrue(reproject_vector(src_path, out_path, "EPSG:32618", "GeoJSON", clip_bbox=bbox))

            out_gdf = gpd.read_file(out_path)
            self.assertEqual(len(out_gdf), 1)
            self.assertEqual(out_gdf.iloc[0]["name"], "inside")

    def test_reproject_vector_uses_strict_utm_clip_rectangle(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src_path = root / "src_utm.geojson"
            out_path = root / "clipped_utm.geojson"
            bbox = BoundingBox.from_corners(
                4500000.0,
                500000.0,
                4501000.0,
                501000.0,
                crs="utm",
                utm_zone=18,
            )

            gdf = gpd.GeoDataFrame(
                {"name": ["inside", "outside"]},
                geometry=gpd.GeoSeries.from_wkt(
                    [
                        "POLYGON ((500100 4500100, 500900 4500100, 500900 4500900, 500100 4500900, 500100 4500100))",
                        "POLYGON ((501100 4500100, 501900 4500100, 501900 4500900, 501100 4500900, 501100 4500100))",
                    ]
                ),
                crs="EPSG:32618",
            )
            gdf.to_file(src_path, driver="GeoJSON")

            self.assertTrue(reproject_vector(src_path, out_path, "EPSG:32618", "GeoJSON", clip_bbox=bbox))

            out_gdf = gpd.read_file(out_path)
            self.assertEqual(len(out_gdf), 1)
            self.assertEqual(out_gdf.iloc[0]["name"], "inside")

            minx, miny, maxx, maxy = out_gdf.total_bounds
            self.assertGreaterEqual(minx, 500000.0)
            self.assertLessEqual(maxx, 501000.0)
            self.assertGreaterEqual(miny, 4500000.0)
            self.assertLessEqual(maxy, 4501000.0)

    def test_downloader_cache_key_changes_with_options(self):
        with tempfile.TemporaryDirectory() as td:
            cache = CacheManager(td)
            bbox = BoundingBox(-74.01, 40.70, -74.00, 40.71)
            downloader = _DummyDownloader("dummy", cache_manager=cache)

            downloader._set_cache_options(mode="a", enabled=True)
            key_a = downloader._build_cache_key(bbox, 5)

            downloader._set_cache_options(mode="b", enabled=True)
            key_b = downloader._build_cache_key(bbox, 5)

            self.assertNotEqual(key_a, key_b)
            cache.cleanup()

    def test_resample_raster_rejects_non_positive_resolution(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src_path = root / "src.tif"
            out_path = root / "bad_resample.tif"

            arr = np.arange(100, dtype="float32").reshape(10, 10)
            with rasterio.open(
                src_path,
                "w",
                driver="GTiff",
                height=10,
                width=10,
                count=1,
                dtype="float32",
                crs="EPSG:32618",
                transform=from_origin(500000, 4500000, 1, 1),
            ) as dst:
                dst.write(arr, 1)

            self.assertFalse(resample_raster(src_path, out_path, 0))
            self.assertFalse(out_path.exists())

    def test_crop_and_resample_cleans_temp_file_on_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src_path = root / "src.tif"
            out_path = root / "final.tif"
            temp_crop = root / "final_crop.tif"
            bbox = BoundingBox(-74.01, 40.70, -74.00, 40.71)

            arr = np.arange(100, dtype="float32").reshape(10, 10)
            with rasterio.open(
                src_path,
                "w",
                driver="GTiff",
                height=10,
                width=10,
                count=1,
                dtype="float32",
                crs="EPSG:4326",
                transform=from_origin(-74.01, 40.71, 0.001, 0.001),
            ) as dst:
                dst.write(arr, 1)

            self.assertFalse(crop_and_resample_raster(src_path, out_path, bbox, 0))
            self.assertFalse(temp_crop.exists())

    def test_crop_raster_returns_false_when_bbox_outside_raster(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src_path = root / "src.tif"
            out_path = root / "cropped.tif"

            arr = np.arange(100, dtype="float32").reshape(10, 10)
            with rasterio.open(
                src_path,
                "w",
                driver="GTiff",
                height=10,
                width=10,
                count=1,
                dtype="float32",
                crs="EPSG:4326",
                transform=from_origin(-74.01, 40.71, 0.001, 0.001),
            ) as dst:
                dst.write(arr, 1)

            # Deliberately outside source raster extent.
            far_bbox = BoundingBox(-120.01, 35.70, -120.00, 35.71)
            self.assertFalse(crop_raster(src_path, out_path, far_bbox))
            self.assertFalse(out_path.exists())

    def test_reference_fetch_tile_counts_retry_failures(self):
        downloader = ReferenceDownloader()
        tile = mercantile.Tile(x=0, y=0, z=1)

        downloader.session.get = mock.Mock(side_effect=requests.RequestException("network down"))
        image = downloader._fetch_tile(tile)

        self.assertIsNone(image)
        self.assertEqual(downloader._tile_error_count, downloader.MAX_RETRIES)
        self.assertEqual(downloader._tile_warning_count, downloader.MAX_RETRIES)

    @mock.patch("map_downloader.downloaders.buildings.requests.get")
    @mock.patch("map_downloader.downloaders.buildings.mercantile.tiles")
    def test_microsoft_buildings_fetch_counts_tile_issues(self, mock_tiles, mock_get):
        downloader = BuildingsDownloader()
        bbox = BoundingBox(-74.01, 40.70, -74.00, 40.71)

        mock_tiles.return_value = [
            mercantile.Tile(x=100, y=200, z=9),
            mercantile.Tile(x=101, y=200, z=9),
        ]

        http_error_resp = mock.Mock()
        http_error_resp.status_code = 500
        http_error_resp.text = ""
        mock_get.side_effect = [http_error_resp, requests.RequestException("timeout")]

        gdf = downloader._fetch_microsoft_buildings(bbox)

        self.assertIsNone(gdf)
        self.assertEqual(downloader._ms_tile_issue_count, 2)
        self.assertEqual(downloader._ms_warning_count, 2)

    def test_overpass_buildings_uses_tiled_fallback_for_small_bbox(self):
        downloader = BuildingsDownloader()

        building_elem = {
            "id": 123,
            "type": "way",
            "geometry": [
                {"lon": -74.001, "lat": 40.701},
                {"lon": -74.000, "lat": 40.701},
                {"lon": -74.000, "lat": 40.702},
                {"lon": -74.001, "lat": 40.702},
            ],
            "tags": {"building": "yes"},
        }

        responses = [None, {"elements": [building_elem]}, None, None, None]

        def fake_query(*args, **kwargs):
            return responses.pop(0) if responses else None

        downloader._query_overpass_json = mock.Mock(side_effect=fake_query)

        elems = downloader._query_overpass_buildings(-74.01, 40.70, -74.00, 40.71)

        self.assertEqual(len(elems), 1)
        self.assertEqual(elems[0].get("id"), 123)

    def test_conus_city_presets_include_5x5_variants(self):
        self.assertEqual(len(BASE_CITY_PRESETS), 10)
        self.assertEqual(len(CITY_PRESETS), 20)
        self.assertIn("New York City (Full)", CITY_PRESETS)
        self.assertIn("Los Angeles (Full)", CITY_PRESETS)
        self.assertIn("Washington, DC (Full)", CITY_PRESETS)
        self.assertIn("New York City (5x5km)", CITY_PRESETS)
        self.assertIn("Los Angeles (5x5km)", CITY_PRESETS)
        self.assertIn("Washington, DC (5x5km)", CITY_PRESETS)

        for bounds in CITY_PRESETS.values():
            self.assertEqual(len(bounds), 4)
            min_lat, min_lon, max_lat, max_lon = bounds
            self.assertLess(min_lat, max_lat)
            self.assertLess(min_lon, max_lon)
            self.assertGreaterEqual(min_lat, 24.0)
            self.assertLessEqual(max_lat, 50.0)
            self.assertGreaterEqual(min_lon, -125.0)
            self.assertLessEqual(max_lon, -66.0)

        for city_name in BASE_CITY_PRESETS:
            preset_name = f"{city_name} (5x5km)"
            self.assertIn(preset_name, CITY_PRESETS)
            min_lat, min_lon, max_lat, max_lon = CITY_PRESETS[preset_name]
            bbox = BoundingBox(min_lon, min_lat, max_lon, max_lat)
            self.assertGreaterEqual(bbox.width_km(), 4.0)
            self.assertLessEqual(bbox.width_km(), 6.0)
            self.assertGreaterEqual(bbox.height_km(), 4.0)
            self.assertLessEqual(bbox.height_km(), 6.0)

    def test_bbox_widget_crs_switch_translates_corners(self):
        self._ensure_qapp()
        widget = BboxInputWidget()

        widget.mode_corners.setChecked(True)
        widget.crs_latlong.setChecked(True)
        widget.lat1_input.setValue(47.700000)
        widget.lon1_input.setValue(-122.500000)
        widget.lat2_input.setValue(47.600000)
        widget.lon2_input.setValue(-122.300000)

        original_bbox = widget.get_bbox()

        widget.crs_utm.setChecked(True)
        utm_bbox = widget.get_bbox()
        self.assertAlmostEqual(utm_bbox.min_lat, original_bbox.min_lat, places=4)
        self.assertAlmostEqual(utm_bbox.min_lon, original_bbox.min_lon, places=4)
        self.assertAlmostEqual(utm_bbox.max_lat, original_bbox.max_lat, places=4)
        self.assertAlmostEqual(utm_bbox.max_lon, original_bbox.max_lon, places=4)

        widget.crs_latlong.setChecked(True)
        roundtrip_bbox = widget.get_bbox()
        self.assertAlmostEqual(roundtrip_bbox.min_lat, original_bbox.min_lat, places=4)
        self.assertAlmostEqual(roundtrip_bbox.min_lon, original_bbox.min_lon, places=4)
        self.assertAlmostEqual(roundtrip_bbox.max_lat, original_bbox.max_lat, places=4)
        self.assertAlmostEqual(roundtrip_bbox.max_lon, original_bbox.max_lon, places=4)

    def test_bbox_widget_utm_auto_zone_is_allowed(self):
        self._ensure_qapp()
        widget = BboxInputWidget()
        self.assertEqual(widget.utm_zone_input.minimum(), 0)
        self.assertEqual(widget.utm_zone_input.value(), 0)

    def test_bbox_widget_utm_zone_change_preserves_bbox(self):
        self._ensure_qapp()
        widget = BboxInputWidget()

        widget.mode_corners.setChecked(True)
        widget.crs_latlong.setChecked(True)
        widget.lat1_input.setValue(47.700000)
        widget.lon1_input.setValue(-122.500000)
        widget.lat2_input.setValue(47.600000)
        widget.lon2_input.setValue(-122.300000)

        widget.crs_utm.setChecked(True)
        bbox_before = widget.get_bbox()
        zone_before = widget.utm_zone_input.value()
        self.assertGreater(zone_before, 0)

        zone_after = zone_before + 1 if zone_before < 60 else zone_before - 1
        widget.utm_zone_input.setValue(zone_after)
        bbox_after = widget.get_bbox()

        self.assertAlmostEqual(bbox_after.min_lat, bbox_before.min_lat, places=4)
        self.assertAlmostEqual(bbox_after.min_lon, bbox_before.min_lon, places=4)
        self.assertAlmostEqual(bbox_after.max_lat, bbox_before.max_lat, places=4)
        self.assertAlmostEqual(bbox_after.max_lon, bbox_before.max_lon, places=4)

    def test_bbox_widget_utm_rounding_to_nearest_100m(self):
        self._ensure_qapp()
        widget = BboxInputWidget()

        widget.mode_corners.setChecked(True)
        widget.crs_utm.setChecked(True)
        widget.lat1_input.setValue(5234980.2)
        widget.lon1_input.setValue(523456.7)
        widget.lat2_input.setValue(5234123.4)
        widget.lon2_input.setValue(524321.9)

        widget.utm_round_check.setChecked(True)

        self.assertAlmostEqual(widget.lat1_input.value() % 100.0, 0.0, places=6)
        self.assertAlmostEqual(widget.lon1_input.value() % 100.0, 0.0, places=6)
        self.assertAlmostEqual(widget.lat2_input.value() % 100.0, 0.0, places=6)
        self.assertAlmostEqual(widget.lon2_input.value() % 100.0, 0.0, places=6)

    def test_terrain_extract_dem_array_handles_tuple_and_array(self):
        downloader = TerrainDownloader()

        arr = np.arange(9, dtype="float32").reshape(3, 3)
        out_direct = downloader._extract_dem_array(arr)
        out_tuple = downloader._extract_dem_array((arr, {"meta": "ignored"}))
        out_singleton = downloader._extract_dem_array(arr[np.newaxis, :, :])

        self.assertEqual(out_direct.shape, (3, 3))
        self.assertEqual(out_tuple.shape, (3, 3))
        self.assertEqual(out_singleton.shape, (3, 3))

    def test_terrain_save_geotiff_preserves_source_georeferencing(self):
        downloader = TerrainDownloader()

        with tempfile.TemporaryDirectory() as td:
            output_path = Path(td) / "terrain.tif"
            bbox = BoundingBox(-74.01, 40.70, -74.00, 40.71)
            arr = np.arange(9, dtype="float32").reshape(3, 3)
            expected_transform = Affine(30.0, 0.0, 500000.0, 0.0, -30.0, 4500000.0)

            downloader._save_geotiff(
                arr,
                output_path,
                bbox,
                profile={
                    "crs": "EPSG:32618",
                    "transform": expected_transform,
                    "width": 3,
                    "height": 3,
                    "count": 1,
                    "dtype": rasterio.float32,
                },
            )

            with rasterio.open(output_path) as ds:
                self.assertEqual(str(ds.crs), "EPSG:32618")
                self.assertEqual(ds.transform, expected_transform)

    def test_get_utm_epsg_clamps_zone_at_180_degrees(self):
        west_edge = BoundingBox(-180.0, 40.0, -179.9, 40.1)
        east_edge = BoundingBox(180.0, 40.0, 180.0, 40.1)
        self.assertEqual(get_utm_epsg(west_edge), "EPSG:32601")
        self.assertEqual(get_utm_epsg(east_edge), "EPSG:32660")

    def test_reproject_raster_fails_without_source_crs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src_path = root / "src_no_crs.tif"
            out_path = root / "out.tif"

            arr = np.arange(100, dtype="float32").reshape(10, 10)
            with rasterio.open(
                src_path,
                "w",
                driver="GTiff",
                height=10,
                width=10,
                count=1,
                dtype="float32",
                transform=from_origin(0, 10, 1, 1),
            ) as dst:
                dst.write(arr, 1)

            self.assertFalse(reproject_raster(src_path, out_path, "EPSG:32618"))
            self.assertFalse(out_path.exists())

    @mock.patch("map_downloader.downloaders.landuse.requests.post")
    def test_landuse_overpass_retries_after_invalid_json(self, mock_post):
        downloader = LanduseDownloader()

        bad_json_resp = mock.Mock()
        bad_json_resp.status_code = 200
        bad_json_resp.json.side_effect = ValueError("invalid json")

        good_resp = mock.Mock()
        good_resp.status_code = 200
        good_resp.json.return_value = {"elements": []}

        mock_post.side_effect = [bad_json_resp, good_resp]

        result = downloader._query_overpass_json("[out:json];out;")
        self.assertEqual(result, {"elements": []})
        self.assertGreaterEqual(mock_post.call_count, 2)

    @mock.patch("map_downloader.downloaders.landuse.time.sleep")
    @mock.patch("map_downloader.downloaders.landuse.requests.post")
    def test_landuse_overpass_retries_after_429(self, mock_post, mock_sleep):
        downloader = LanduseDownloader()

        throttled = mock.Mock()
        throttled.status_code = 429

        good_resp = mock.Mock()
        good_resp.status_code = 200
        good_resp.json.return_value = {"elements": []}

        mock_post.side_effect = [throttled, good_resp]

        result = downloader._query_overpass_json("[out:json];out;")
        self.assertEqual(result, {"elements": []})
        self.assertGreaterEqual(mock_post.call_count, 2)
        mock_sleep.assert_called()

    @mock.patch("map_downloader.downloaders.water.requests.post")
    def test_water_overpass_retries_after_invalid_json(self, mock_post):
        downloader = WaterDownloader()

        bad_json_resp = mock.Mock()
        bad_json_resp.status_code = 200
        bad_json_resp.json.side_effect = ValueError("invalid json")

        good_resp = mock.Mock()
        good_resp.status_code = 200
        good_resp.json.return_value = {"elements": []}

        mock_post.side_effect = [bad_json_resp, good_resp]

        result = downloader._query_overpass_json("[out:json];out;")
        self.assertEqual(result, {"elements": []})
        self.assertGreaterEqual(mock_post.call_count, 2)

    @mock.patch("map_downloader.downloaders.water.time.sleep")
    @mock.patch("map_downloader.downloaders.water.requests.post")
    def test_water_overpass_retries_after_429(self, mock_post, mock_sleep):
        downloader = WaterDownloader()

        throttled = mock.Mock()
        throttled.status_code = 429

        good_resp = mock.Mock()
        good_resp.status_code = 200
        good_resp.json.return_value = {"elements": []}

        mock_post.side_effect = [throttled, good_resp]

        result = downloader._query_overpass_json("[out:json];out;")
        self.assertEqual(result, {"elements": []})
        self.assertGreaterEqual(mock_post.call_count, 2)
        mock_sleep.assert_called()

    @mock.patch("map_downloader.downloaders.buildings.time.sleep")
    @mock.patch("map_downloader.downloaders.buildings.requests.post")
    def test_buildings_overpass_retries_after_429(self, mock_post, mock_sleep):
        downloader = BuildingsDownloader()

        throttled = mock.Mock()
        throttled.status_code = 429

        good_resp = mock.Mock()
        good_resp.status_code = 200
        good_resp.json.return_value = {"elements": []}

        mock_post.side_effect = [throttled, good_resp]

        result = downloader._query_overpass_json("[out:json];out;")
        self.assertEqual(result, {"elements": []})
        self.assertGreaterEqual(mock_post.call_count, 2)
        mock_sleep.assert_called()

    def test_buildings_message_indicates_degraded_sources(self):
        downloader = BuildingsDownloader()
        bbox = BoundingBox(-74.01, 40.70, -74.00, 40.71)

        def _osm_side_effect(_bbox):
            downloader._overpass_issue_count = 1
            return None

        def _ms_side_effect(_bbox):
            downloader._ms_tile_issue_count = 1
            return None

        downloader._fetch_osm_buildings = mock.Mock(side_effect=_osm_side_effect)
        downloader._fetch_microsoft_buildings = mock.Mock(side_effect=_ms_side_effect)

        with tempfile.TemporaryDirectory() as td:
            result = downloader.download(
                bbox=bbox,
                resolution_m=5,
                output_path=Path(td),
                building_source="MERGED",
            )

        self.assertFalse(result["success"])
        self.assertIn("degraded", result["message"].lower())
        self.assertIn("OSM Overpass", result["message"])

    def test_water_no_data_message_indicates_degraded_source(self):
        downloader = WaterDownloader()
        bbox = BoundingBox(-74.01, 40.70, -74.00, 40.71)

        def _water_side_effect(_bbox, _out):
            downloader._overpass_issue_count = 1
            return None

        downloader._download_osm_water = mock.Mock(side_effect=_water_side_effect)

        with tempfile.TemporaryDirectory() as td:
            result = downloader.download(
                bbox=bbox,
                resolution_m=5,
                output_path=Path(td),
                include_vector=True,
            )

        self.assertFalse(result["success"])
        self.assertIn("degraded", result["message"].lower())

    def test_landuse_no_data_message_indicates_degraded_source(self):
        downloader = LanduseDownloader()
        bbox = BoundingBox(-74.01, 40.70, -74.00, 40.71)

        def _landuse_side_effect(_bbox, _out):
            downloader._overpass_issue_count = 1
            return None

        downloader._download_osm_landuse = mock.Mock(side_effect=_landuse_side_effect)

        with tempfile.TemporaryDirectory() as td:
            result = downloader.download(
                bbox=bbox,
                resolution_m=5,
                output_path=Path(td),
                include_raster=False,
                include_vector=True,
            )

        self.assertFalse(result["success"])
        self.assertIn("degraded", result["message"].lower())

    def test_project_output_root_uses_project_name(self):
        project = Project(
            name="My Region",
            output_folder="C:/tmp/region3d",
            timestamp_mode="none",
            append_timestamp_to_name=False,
        )
        out_root = project.resolve_output_root(force_refresh=True)
        self.assertTrue(str(out_root).replace("\\", "/").endswith("/region3d/My_Region"))

    def test_project_output_root_can_append_timestamp(self):
        project = Project(
            name="My Region",
            output_folder="C:/tmp/region3d",
            timestamp_mode="append",
            append_timestamp_to_name=True,
        )
        out_root = project.resolve_output_root(force_refresh=True)
        out_name = out_root.name
        self.assertTrue(out_name.startswith("My_Region_"))
        self.assertGreaterEqual(len(out_name), len("My_Region_20260101_000000"))

    def test_project_output_root_can_prepend_timestamp(self):
        project = Project(
            name="My Region",
            output_folder="C:/tmp/region3d",
            timestamp_mode="prepend",
            append_timestamp_to_name=True,
        )
        out_root = project.resolve_output_root(force_refresh=True)
        out_name = out_root.name
        self.assertTrue(out_name.endswith("_My_Region"))
        self.assertGreaterEqual(len(out_name), len("20260101_000000_My_Region"))

    def test_bbox_expanded_adds_margin(self):
        bbox = BoundingBox(-100.0, 40.0, -99.0, 41.0)
        expanded = bbox.expanded(0.25)

        self.assertAlmostEqual(expanded.min_lon, -100.25)
        self.assertAlmostEqual(expanded.max_lon, -98.75)
        self.assertAlmostEqual(expanded.min_lat, 39.75)
        self.assertAlmostEqual(expanded.max_lat, 41.25)

    def test_layer_config_includes_water_vector_flag(self):
        cfg = LayerConfig()
        self.assertTrue(cfg.water_vector)
        as_dict = cfg.as_dict()
        self.assertIn("water_vector", as_dict)
        self.assertTrue(as_dict["water_vector"])

    def test_project_from_dict_backfills_missing_water_layer(self):
        old_payload = {
            "name": "Legacy Project",
            "layers": {
                "terrain": {"enabled": True},
                "buildings": {"enabled": True},
                "landuse": {"enabled": True},
                "reference": {"enabled": True},
            },
        }
        project = Project.from_dict(old_payload)
        self.assertIn("water", project.layers)
        self.assertTrue(project.layers["water"].enabled)

    def test_project_append_timestamp_defaults_true(self):
        project = Project()
        self.assertTrue(project.append_timestamp_to_name)
        self.assertEqual(project.timestamp_mode, "append")

        loaded = Project.from_dict({"name": "Legacy"})
        self.assertTrue(loaded.append_timestamp_to_name)
        self.assertEqual(loaded.timestamp_mode, "append")

    def test_project_from_dict_legacy_append_false_maps_to_none(self):
        loaded = Project.from_dict({"name": "Legacy", "append_timestamp_to_name": False})
        self.assertFalse(loaded.append_timestamp_to_name)
        self.assertEqual(loaded.timestamp_mode, "none")

    def test_output_page_uses_bbox_preset_as_default_project_name(self):
        self._ensure_qapp()

        class _StubBBoxWidget:
            def selected_preset_name(self):
                return "Seattle (5x5km)"

        class _StubBBoxPage:
            bbox_widget = _StubBBoxWidget()

        class _StubWizard:
            bbox_page = _StubBBoxPage()

        page = OutputPage()
        page.setField = mock.Mock()  # keep QObject happy if any internal field ops happen
        page.wizard = lambda: _StubWizard()

        page.initializePage()

        self.assertEqual(page.name_input.text(), "Seattle (5x5km)")
        self.assertTrue(page.timestamp_append_radio.isChecked())

    def test_output_page_does_not_override_user_project_name(self):
        self._ensure_qapp()

        class _StubBBoxWidget:
            def selected_preset_name(self):
                return "Chicago (Full)"

        class _StubBBoxPage:
            bbox_widget = _StubBBoxWidget()

        class _StubWizard:
            bbox_page = _StubBBoxPage()

        page = OutputPage()
        page.wizard = lambda: _StubWizard()
        page.name_input.setText("Custom Name")
        page._name_user_edited = True

        page.initializePage()

        self.assertEqual(page.name_input.text(), "Custom Name")

    def test_augment_with_height_rejects_unknown_mode(self):
        ok = augment_with_height(
            Path("in.geojson"),
            Path("terrain.tif"),
            Path("out.geojson"),
            height_mode="mystery",
        )
        self.assertFalse(ok)

    @mock.patch("rasterio.mask.mask")
    @mock.patch("rasterio.open")
    @mock.patch("geopandas.GeoDataFrame.to_file")
    @mock.patch("geopandas.read_file")
    def test_augment_with_height_mask_failure_falls_back_to_zero(
        self,
        mock_read_file,
        mock_to_file,
        mock_rasterio_open,
        mock_mask,
    ):
        import geopandas as gpd
        from shapely.geometry import Polygon

        gdf = gpd.GeoDataFrame(
            [{"geometry": Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])}],
            crs="EPSG:4326",
        )
        mock_read_file.return_value = gdf

        mock_src = mock.Mock()
        mock_src.crs = "EPSG:4326"
        mock_rasterio_open.return_value.__enter__.return_value = mock_src
        mock_mask.side_effect = RuntimeError("mask failed")

        with tempfile.TemporaryDirectory() as td:
            ok = augment_with_height(
                Path(td) / "buildings.geojson",
                Path(td) / "terrain.tif",
                Path(td) / "out" / "buildings_aug.geojson",
                height_mode="mean",
            )

        self.assertTrue(ok)
        self.assertIn("height", gdf.columns)
        self.assertEqual(float(gdf.loc[0, "height"]), 0.0)
        mock_to_file.assert_called_once()

    def test_exporter_can_export_bbox_geojson(self):
        with tempfile.TemporaryDirectory() as td:
            output_root = Path(td)
            exporter = Exporter(output_root)
            bbox = BoundingBox(-74.01, 40.70, -74.00, 40.71)

            result = exporter.export(
                terrain_formats=[],
                buildings_formats=[],
                landuse_formats=[],
                water_formats=[],
                big_streets_formats=[],
                small_streets_formats=[],
                reference_formats=[],
                bbox_formats=["GeoJSON"],
                qgis_project_formats=[],
                bbox=bbox,
            )

            self.assertTrue(result.success)
            self.assertTrue(any(path.endswith("bounding_box.geojson") for path in result.files))

    def test_exporter_can_export_bbox_kml_and_kmz(self):
        with tempfile.TemporaryDirectory() as td:
            output_root = Path(td)
            exporter = Exporter(output_root)
            bbox = BoundingBox(-74.01, 40.70, -74.00, 40.71)

            result = exporter.export(
                terrain_formats=[],
                buildings_formats=[],
                landuse_formats=[],
                water_formats=[],
                big_streets_formats=[],
                small_streets_formats=[],
                reference_formats=[],
                bbox_formats=["KML", "KMZ"],
                qgis_project_formats=[],
                bbox=bbox,
            )

            self.assertTrue(result.success)
            kml_paths = [Path(path) for path in result.files if path.endswith(".kml")]
            kmz_paths = [Path(path) for path in result.files if path.endswith(".kmz")]
            self.assertTrue(kml_paths)
            self.assertTrue(kmz_paths)
            self.assertIn("<kml", kml_paths[0].read_text(encoding="utf-8").lower())
            with zipfile.ZipFile(kmz_paths[0], "r") as zf:
                self.assertIn("doc.kml", zf.namelist())

    def test_exporter_reprojects_vectors_to_epsg4326_when_requested(self):
        import geopandas as gpd
        from shapely.geometry import Polygon

        with tempfile.TemporaryDirectory() as td:
            output_root = Path(td)
            processed = output_root / "processed"
            processed.mkdir(parents=True, exist_ok=True)

            gdf = gpd.GeoDataFrame(
                [{"id": 1, "geometry": Polygon([(500000, 4500000), (500100, 4500000), (500100, 4500100), (500000, 4500100)])}],
                crs="EPSG:32618",
            )
            gdf.to_file(processed / "buildings.geojson", driver="GeoJSON")

            exporter = Exporter(output_root)
            result = exporter.export(
                terrain_formats=[],
                buildings_formats=["GeoJSON"],
                landuse_formats=[],
                water_formats=[],
                big_streets_formats=[],
                small_streets_formats=[],
                reference_formats=[],
                bbox_formats=[],
                qgis_project_formats=[],
                export_crs_mode="epsg:4326",
                bbox=None,
            )

            self.assertTrue(result.success)
            out_path = next(Path(path) for path in result.files if path.endswith("buildings.geojson"))
            out_gdf = gpd.read_file(out_path)
            self.assertEqual(out_gdf.crs.to_epsg(), 4326)

    def test_exporter_reprojects_bbox_to_project_utm_when_requested(self):
        import geopandas as gpd

        with tempfile.TemporaryDirectory() as td:
            output_root = Path(td)
            exporter = Exporter(output_root)
            bbox = BoundingBox(-74.01, 40.70, -74.00, 40.71)

            result = exporter.export(
                terrain_formats=[],
                buildings_formats=[],
                landuse_formats=[],
                water_formats=[],
                big_streets_formats=[],
                small_streets_formats=[],
                reference_formats=[],
                bbox_formats=["GeoJSON"],
                qgis_project_formats=[],
                export_crs_mode="project_utm",
                bbox=bbox,
            )

            self.assertTrue(result.success)
            out_path = next(Path(path) for path in result.files if path.endswith("bounding_box.geojson"))
            out_gdf = gpd.read_file(out_path)
            self.assertEqual(out_gdf.crs.to_epsg(), 32618)

    def test_exporter_can_export_reference_png_and_jpg(self):
        try:
            from PIL import Image  # noqa: F401
        except Exception:
            self.skipTest("Pillow is not available in this environment")

        with tempfile.TemporaryDirectory() as td:
            output_root = Path(td)
            processed = output_root / "processed"
            processed.mkdir(parents=True, exist_ok=True)

            src = processed / "reference.tif"
            arr = np.arange(100, dtype="float32").reshape(10, 10)
            with rasterio.open(
                src,
                "w",
                driver="GTiff",
                height=10,
                width=10,
                count=1,
                dtype="float32",
                crs="EPSG:32618",
                transform=from_origin(500000, 4500000, 1, 1),
            ) as dst:
                dst.write(arr, 1)

            exporter = Exporter(output_root)
            result = exporter.export(
                terrain_formats=[],
                buildings_formats=[],
                landuse_formats=[],
                water_formats=[],
                big_streets_formats=[],
                small_streets_formats=[],
                reference_formats=["PNG", "JPG"],
                bbox_formats=[],
                qgis_project_formats=[],
                bbox=None,
            )

            self.assertTrue(result.success)
            self.assertTrue(any(path.endswith("reference.png") for path in result.files))
            self.assertTrue(any(path.endswith("reference.jpg") for path in result.files))

    def test_exporter_supports_multiple_formats_per_datatype(self):
        import geopandas as gpd
        from shapely.geometry import Polygon

        with tempfile.TemporaryDirectory() as td:
            output_root = Path(td)
            processed = output_root / "processed"
            processed.mkdir(parents=True, exist_ok=True)

            gdf = gpd.GeoDataFrame(
                [{"id": 1, "geometry": Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])}],
                crs="EPSG:4326",
            )
            src = processed / "buildings.geojson"
            gdf.to_file(src, driver="GeoJSON")

            exporter = Exporter(output_root)
            result = exporter.export(
                terrain_formats=[],
                buildings_formats=["GeoJSON", "GeoPackage"],
                landuse_formats=[],
                water_formats=[],
                big_streets_formats=[],
                small_streets_formats=[],
                reference_formats=[],
                bbox_formats=[],
                qgis_project_formats=[],
                bbox=None,
            )

            self.assertTrue(result.success)
            self.assertTrue(any(path.endswith("buildings.geojson") for path in result.files))
            self.assertTrue(any(path.endswith("buildings.gpkg") for path in result.files))

    def test_exporter_repeated_runs_do_not_overwrite(self):
        import geopandas as gpd
        from shapely.geometry import Polygon

        with tempfile.TemporaryDirectory() as td:
            output_root = Path(td)
            processed = output_root / "processed"
            processed.mkdir(parents=True, exist_ok=True)

            gdf = gpd.GeoDataFrame(
                [{"id": 1, "geometry": Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])}],
                crs="EPSG:4326",
            )
            src = processed / "buildings.geojson"
            gdf.to_file(src, driver="GeoJSON")

            exporter = Exporter(output_root)

            result1 = exporter.export(
                terrain_formats=[],
                buildings_formats=["GeoJSON"],
                landuse_formats=[],
                water_formats=[],
                big_streets_formats=[],
                small_streets_formats=[],
                reference_formats=[],
                bbox_formats=[],
                qgis_project_formats=[],
                bbox=None,
            )

            result2 = exporter.export(
                terrain_formats=[],
                buildings_formats=["GeoJSON"],
                landuse_formats=[],
                water_formats=[],
                big_streets_formats=[],
                small_streets_formats=[],
                reference_formats=[],
                bbox_formats=[],
                qgis_project_formats=[],
                bbox=None,
            )

            self.assertTrue(result1.success)
            self.assertTrue(result2.success)
            self.assertEqual(len(result1.files), 1)
            self.assertEqual(len(result2.files), 1)
            self.assertNotEqual(result1.files[0], result2.files[0])

    def test_exporter_can_export_big_and_small_streets(self):
        import geopandas as gpd
        from shapely.geometry import LineString

        with tempfile.TemporaryDirectory() as td:
            output_root = Path(td)
            processed = output_root / "processed"
            processed.mkdir(parents=True, exist_ok=True)

            big_gdf = gpd.GeoDataFrame(
                [{"id": 1, "geometry": LineString([(0, 0), (1, 1)])}],
                crs="EPSG:4326",
            )
            small_gdf = gpd.GeoDataFrame(
                [{"id": 2, "geometry": LineString([(0, 1), (1, 0)])}],
                crs="EPSG:4326",
            )
            big_gdf.to_file(processed / "big_streets.geojson", driver="GeoJSON")
            small_gdf.to_file(processed / "small_streets.geojson", driver="GeoJSON")

            exporter = Exporter(output_root)
            result = exporter.export(
                terrain_formats=[],
                buildings_formats=[],
                landuse_formats=[],
                water_formats=[],
                big_streets_formats=["GeoJSON"],
                small_streets_formats=["GeoJSON"],
                reference_formats=[],
                bbox_formats=[],
                qgis_project_formats=[],
                bbox=None,
            )

            self.assertTrue(result.success)
            self.assertTrue(any(path.endswith("big_streets.geojson") for path in result.files))
            self.assertTrue(any(path.endswith("small_streets.geojson") for path in result.files))

    def test_exporter_can_export_buildings_kml_and_kmz(self):
        import geopandas as gpd
        from shapely.geometry import Polygon

        with tempfile.TemporaryDirectory() as td:
            output_root = Path(td)
            processed = output_root / "processed"
            processed.mkdir(parents=True, exist_ok=True)

            gdf = gpd.GeoDataFrame(
                [{"name": "b1", "geometry": Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])}],
                crs="EPSG:4326",
            )
            gdf.to_file(processed / "buildings.geojson", driver="GeoJSON")

            exporter = Exporter(output_root)
            result = exporter.export(
                terrain_formats=[],
                buildings_formats=["KML", "KMZ"],
                landuse_formats=[],
                water_formats=[],
                big_streets_formats=[],
                small_streets_formats=[],
                reference_formats=[],
                bbox_formats=[],
                qgis_project_formats=[],
                bbox=None,
            )

            self.assertTrue(result.success)
            kml_paths = [Path(path) for path in result.files if path.endswith(".kml")]
            kmz_paths = [Path(path) for path in result.files if path.endswith(".kmz")]
            self.assertTrue(kml_paths)
            self.assertTrue(kmz_paths)
            self.assertIn("<kml", kml_paths[0].read_text(encoding="utf-8").lower())
            with zipfile.ZipFile(kmz_paths[0], "r") as zf:
                self.assertIn("doc.kml", zf.namelist())

    def test_exporter_can_export_qgs_with_utm_crs(self):
        with tempfile.TemporaryDirectory() as td:
            output_root = Path(td)
            processed = output_root / "processed"
            processed.mkdir(parents=True, exist_ok=True)

            src = processed / "terrain.tif"
            arr = np.arange(100, dtype="float32").reshape(10, 10)
            with rasterio.open(
                src,
                "w",
                driver="GTiff",
                height=10,
                width=10,
                count=1,
                dtype="float32",
                crs="EPSG:32618",
                transform=from_origin(500000, 4500000, 1, 1),
            ) as dst:
                dst.write(arr, 1)

            exporter = Exporter(output_root)
            result = exporter.export(
                terrain_formats=["GeoTIFF"],
                buildings_formats=[],
                landuse_formats=[],
                water_formats=[],
                big_streets_formats=[],
                small_streets_formats=[],
                reference_formats=[],
                bbox_formats=[],
                qgis_project_formats=["QGS"],
                bbox=BoundingBox(-74.01, 40.70, -74.00, 40.71),
            )

            self.assertTrue(result.success)
            qgs_paths = [Path(path) for path in result.files if path.endswith(".qgs")]
            self.assertTrue(qgs_paths)
            qgs_text = qgs_paths[0].read_text(encoding="utf-8")
            self.assertIn("<authid>EPSG:32618</authid>", qgs_text)
            self.assertIn("terrain", qgs_text)

    def test_exporter_can_export_qgz(self):
        with tempfile.TemporaryDirectory() as td:
            output_root = Path(td)
            processed = output_root / "processed"
            processed.mkdir(parents=True, exist_ok=True)

            src = processed / "terrain.tif"
            arr = np.arange(100, dtype="float32").reshape(10, 10)
            with rasterio.open(
                src,
                "w",
                driver="GTiff",
                height=10,
                width=10,
                count=1,
                dtype="float32",
                crs="EPSG:32618",
                transform=from_origin(500000, 4500000, 1, 1),
            ) as dst:
                dst.write(arr, 1)

            exporter = Exporter(output_root)
            result = exporter.export(
                terrain_formats=["GeoTIFF"],
                buildings_formats=[],
                landuse_formats=[],
                water_formats=[],
                big_streets_formats=[],
                small_streets_formats=[],
                reference_formats=[],
                bbox_formats=[],
                qgis_project_formats=["QGZ"],
                bbox=BoundingBox(-74.01, 40.70, -74.00, 40.71),
            )

            self.assertTrue(result.success)
            qgz_paths = [Path(path) for path in result.files if path.endswith(".qgz")]
            self.assertTrue(qgz_paths)
            with zipfile.ZipFile(qgz_paths[0], "r") as zf:
                self.assertIn("project.qgs", zf.namelist())
                qgs_text = zf.read("project.qgs").decode("utf-8")
                self.assertIn("<authid>EPSG:32618</authid>", qgs_text)

    def test_exporter_can_export_project_readme(self):
        with tempfile.TemporaryDirectory() as td:
            output_root = Path(td)
            processed = output_root / "processed"
            processed.mkdir(parents=True, exist_ok=True)

            src = processed / "terrain.tif"
            arr = np.arange(100, dtype="float32").reshape(10, 10)
            with rasterio.open(
                src,
                "w",
                driver="GTiff",
                height=10,
                width=10,
                count=1,
                dtype="float32",
                crs="EPSG:32618",
                transform=from_origin(500000, 4500000, 1, 1),
            ) as dst:
                dst.write(arr, 1)

            bbox = BoundingBox(-74.01, 40.70, -74.00, 40.71)
            project = Project(name="README Test Project")
            project.set_bbox(bbox)
            project.output_folder = str(output_root)
            project.resolution_m = 5.0

            exporter = Exporter(output_root)
            result = exporter.export(
                terrain_formats=["GeoTIFF"],
                buildings_formats=[],
                landuse_formats=[],
                water_formats=[],
                big_streets_formats=[],
                small_streets_formats=[],
                reference_formats=[],
                bbox_formats=[],
                qgis_project_formats=[],
                readme_formats=["README.md"],
                bbox=bbox,
                project=project,
            )

            self.assertTrue(result.success)
            readme_paths = [Path(path) for path in result.files if Path(path).name.lower().startswith("readme")]
            self.assertTrue(readme_paths)
            readme_text = readme_paths[0].read_text(encoding="utf-8")
            self.assertIn("README Test Project", readme_text)
            self.assertIn("## Bounding Box", readme_text)
            self.assertIn("## Layer Configuration", readme_text)
            self.assertIn("## Files Generated In This Export", readme_text)

    def test_exporter_readme_uses_strict_utm_corners_when_available(self):
        with tempfile.TemporaryDirectory() as td:
            output_root = Path(td)
            processed = output_root / "processed"
            processed.mkdir(parents=True, exist_ok=True)

            src = processed / "terrain.tif"
            arr = np.arange(100, dtype="float32").reshape(10, 10)
            with rasterio.open(
                src,
                "w",
                driver="GTiff",
                height=10,
                width=10,
                count=1,
                dtype="float32",
                crs="EPSG:32616",
                transform=from_origin(422900, 4641300, 30, 30),
            ) as dst:
                dst.write(arr, 1)

            bbox = BoundingBox.from_corners(
                4636800.0,
                422900.0,
                4641300.0,
                427800.0,
                crs="utm",
                utm_zone=16,
            )
            project = Project(name="README UTM Corners")
            project.set_bbox(bbox)
            project.output_folder = str(output_root)

            exporter = Exporter(output_root)
            result = exporter.export(
                terrain_formats=["GeoTIFF"],
                buildings_formats=[],
                landuse_formats=[],
                water_formats=[],
                big_streets_formats=[],
                small_streets_formats=[],
                reference_formats=[],
                bbox_formats=[],
                qgis_project_formats=[],
                readme_formats=["README.md"],
                bbox=bbox,
                project=project,
                export_crs_mode="project_utm",
            )

            self.assertTrue(result.success)
            readme_paths = [Path(path) for path in result.files if Path(path).name.lower().startswith("readme")]
            self.assertTrue(readme_paths)
            readme_text = readme_paths[0].read_text(encoding="utf-8")
            self.assertIn("SW corner (E, N): 422900.000, 4636800.000", readme_text)
            self.assertIn("SE corner (E, N): 427800.000, 4636800.000", readme_text)
            self.assertIn("NE corner (E, N): 427800.000, 4641300.000", readme_text)
            self.assertIn("NW corner (E, N): 422900.000, 4641300.000", readme_text)

    def test_qgs_uses_one_format_per_datatype_and_reference_off(self):
        import geopandas as gpd
        from shapely.geometry import Polygon

        with tempfile.TemporaryDirectory() as td:
            output_root = Path(td)
            processed = output_root / "processed"
            processed.mkdir(parents=True, exist_ok=True)

            # Terrain for CRS inference and bottom placement.
            terrain_src = processed / "terrain.tif"
            arr = np.arange(100, dtype="float32").reshape(10, 10)
            with rasterio.open(
                terrain_src,
                "w",
                driver="GTiff",
                height=10,
                width=10,
                count=1,
                dtype="float32",
                crs="EPSG:32618",
                transform=from_origin(500000, 4500000, 1, 1),
            ) as dst:
                dst.write(arr, 1)

            # Buildings in two formats; QGS should pick preferred one only.
            gdf = gpd.GeoDataFrame(
                [{"name": "b1", "geometry": Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])}],
                crs="EPSG:4326",
            )
            gdf.to_file(processed / "buildings.geojson", driver="GeoJSON")
            gdf.to_file(processed / "buildings.gpkg", driver="GPKG")

            # Reference image should be included but unchecked.
            ref_src = processed / "reference.tif"
            with rasterio.open(
                ref_src,
                "w",
                driver="GTiff",
                height=10,
                width=10,
                count=1,
                dtype="float32",
                crs="EPSG:32618",
                transform=from_origin(500000, 4500000, 1, 1),
            ) as dst:
                dst.write(arr, 1)

            exporter = Exporter(output_root)
            result = exporter.export(
                terrain_formats=["GeoTIFF"],
                buildings_formats=["GeoJSON", "GeoPackage"],
                landuse_formats=[],
                water_formats=[],
                big_streets_formats=[],
                small_streets_formats=[],
                reference_formats=["GeoTIFF"],
                bbox_formats=[],
                qgis_project_formats=["QGS"],
                bbox=BoundingBox(-74.01, 40.70, -74.00, 40.71),
            )

            qgs_paths = [Path(path) for path in result.files if path.endswith(".qgs")]
            self.assertTrue(qgs_paths)
            qgs_text = qgs_paths[0].read_text(encoding="utf-8")

            self.assertIn('name="Datatypes"', qgs_text)
            self.assertIn('name="Buildings"', qgs_text)
            self.assertIn('name="Reference"', qgs_text)

            # One buildings layer should be present in project tree.
            self.assertEqual(qgs_text.count('name="buildings"'), 1)

            # Reference layers should default to unchecked visibility.
            self.assertIn('name="reference"', qgs_text)
            self.assertIn('checked="Qt::Unchecked" name="reference"', qgs_text)


if __name__ == "__main__":
    unittest.main()
