"""Page 4: Download Progress."""

from pathlib import Path
import time
import numpy as np
import rasterio
from rasterio.transform import rowcol
from rasterio.warp import transform_bounds

from PySide6.QtCore import QObject, QThread, Signal, Qt
from PySide6.QtWidgets import (
    QWizardPage, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit, QProgressBar
)

from map_downloader.core.cache import CacheManager
from map_downloader.downloaders.terrain import TerrainDownloader
from map_downloader.downloaders.buildings import BuildingsDownloader
from map_downloader.downloaders.landuse import LanduseDownloader
from map_downloader.downloaders.water import WaterDownloader
from map_downloader.downloaders.roads import RoadsDownloader
from map_downloader.downloaders.reference import ReferenceDownloader
from map_downloader.processing.reproject import get_utm_epsg, reproject_raster, reproject_vector
from map_downloader.processing.resample import crop_and_resample_raster
from map_downloader.processing.merge import augment_with_height
from map_downloader.gui.widgets.progress_panel import ProgressPanel


class DownloadWorker(QObject):
    """Background worker for download + processing pipeline."""

    layer_progress = Signal(str, int, str)
    layer_done = Signal(str, bool, bool, str)
    overall_progress = Signal(int)
    log = Signal(str)
    finished = Signal(bool)

    def __init__(self, project):
        super().__init__()
        self.project = project
        self.cancel_requested = False

    def cancel(self):
        """Request graceful cancellation."""
        self.cancel_requested = True

    def _target_crs(self, bbox) -> str:
        if self.project.utm_zone_override:
            centroid_lat = (bbox.min_lat + bbox.max_lat) / 2
            epsg = (32700 if centroid_lat < 0 else 32600) + int(self.project.utm_zone_override)
            return f"EPSG:{epsg}"
        return get_utm_epsg(bbox)

    def _is_cancelled(self) -> bool:
        if self.cancel_requested:
            self.log.emit("Cancellation requested.")
            return True
        return False

    def _draw_reference_inset_border(self, context_path: Path, inner_bbox) -> bool:
        """Draw a visible border for the original bbox onto the context reference image."""
        try:
            with rasterio.open(context_path, "r+") as ds:
                if ds.count < 1:
                    return False

                minx, miny, maxx, maxy = inner_bbox.to_polygon_wgs84().bounds
                if ds.crs is not None and str(ds.crs) != "EPSG:4326":
                    minx, miny, maxx, maxy = transform_bounds(
                        "EPSG:4326", ds.crs, minx, miny, maxx, maxy, densify_pts=21
                    )

                row_top, col_left = rowcol(ds.transform, minx, maxy)
                row_bottom, col_right = rowcol(ds.transform, maxx, miny)

                h, w = ds.height, ds.width
                r0, r1 = sorted((int(row_top), int(row_bottom)))
                c0, c1 = sorted((int(col_left), int(col_right)))
                r0, r1 = max(0, r0), min(h - 1, r1)
                c0, c1 = max(0, c0), min(w - 1, c1)

                data = ds.read()
                if data.size == 0:
                    return False

                band_max = np.iinfo(data.dtype).max if np.issubdtype(data.dtype, np.integer) else 1.0
                band_min = np.iinfo(data.dtype).min if np.issubdtype(data.dtype, np.integer) else 0.0

                if ds.count >= 3:
                    border_color = np.array([band_max, band_min, band_min], dtype=data.dtype)[:, None]
                else:
                    border_color = np.array([band_max], dtype=data.dtype)[:, None]

                # Handle tiny insets by drawing a center cross marker instead of a box.
                if r0 >= r1 or c0 >= c1:
                    center_lat = (inner_bbox.min_lat + inner_bbox.max_lat) / 2.0
                    center_lon = (inner_bbox.min_lon + inner_bbox.max_lon) / 2.0
                    cx, cy = center_lon, center_lat
                    if ds.crs is not None and str(ds.crs) != "EPSG:4326":
                        cx, cy, _, _ = transform_bounds("EPSG:4326", ds.crs, center_lon, center_lat, center_lon, center_lat)
                    row_c, col_c = rowcol(ds.transform, cx, cy)
                    rr = max(0, min(h - 1, int(row_c)))
                    cc = max(0, min(w - 1, int(col_c)))
                    for d in range(-3, 4):
                        r = max(0, min(h - 1, rr + d))
                        c = max(0, min(w - 1, cc + d))
                        c2 = max(0, min(w - 1, cc - d))
                        data[:, r, c] = border_color
                        data[:, r, c2] = border_color
                    ds.write(data)
                    return True

                thickness = max(2, int(round(min(h, w) * 0.003)))

                for t in range(thickness):
                    rt = min(h - 1, r0 + t)
                    rb = max(0, r1 - t)
                    cl = min(w - 1, c0 + t)
                    cr = max(0, c1 - t)
                    if rt > rb or cl > cr:
                        break

                    data[:, rt, cl:cr + 1] = border_color
                    data[:, rb, cl:cr + 1] = border_color
                    data[:, rt:rb + 1, cl] = border_color
                    data[:, rt:rb + 1, cr] = border_color

                ds.write(data)
            return True
        except Exception:
            return False

    def run(self):
        """Execute full download + processing pipeline in worker thread."""
        project = self.project
        bbox = project.get_bbox()
        if bbox is None:
            self.log.emit("Error: no bounding box configured.")
            self.finished.emit(False)
            return

        output_root = project.resolve_output_root(force_refresh=False)
        downloads_dir = output_root / "downloads"
        processed_dir = output_root / "processed"
        downloads_dir.mkdir(parents=True, exist_ok=True)
        processed_dir.mkdir(parents=True, exist_ok=True)

        project_cache = CacheManager(str(output_root / ".cache"))
        global_cache = CacheManager(
            str(CacheManager.default_global_cache_dir()),
            fallback_cache=project_cache,
        )
        target_crs = self._target_crs(bbox)
        terrain_processed = processed_dir / "terrain.tif"

        enabled_layers = [name for name, cfg in project.layers.items() if cfg.enabled]
        total_layers = max(1, len(enabled_layers))
        completed_layers = 0
        all_ok = True
        layer_results = {}
        layer_outputs = {}
        layer_started_at = {}
        run_started_at = time.perf_counter()

        def layer_progress_cb(layer_name: str):
            return lambda p, s: self.layer_progress.emit(layer_name, p, s)

        def finalize_layer(layer_name: str, success: bool, cache_hit: bool, message: str):
            nonlocal completed_layers
            completed_layers += 1
            elapsed_s = None
            if layer_name in layer_started_at:
                elapsed_s = max(0.0, time.perf_counter() - layer_started_at[layer_name])
            layer_results[layer_name] = {
                "success": success,
                "cache_hit": cache_hit,
                "message": message,
                "elapsed_s": elapsed_s,
            }
            self.layer_done.emit(layer_name, success, cache_hit, message)
            self.overall_progress.emit(int((completed_layers / total_layers) * 100))

        def emit_run_summary():
            total_elapsed = max(0.0, time.perf_counter() - run_started_at)
            self.log.emit("=== Run Summary ===")
            for layer_name in enabled_layers:
                result = layer_results.get(layer_name)
                if result is None:
                    self.log.emit(f"[{layer_name}] skipped or not executed")
                    continue

                status = "OK" if result["success"] else "FAILED"
                cache_note = "cache_hit" if result["cache_hit"] else "no_cache"
                elapsed = result.get("elapsed_s")
                elapsed_note = f" | elapsed={elapsed:.1f}s" if elapsed is not None else ""
                self.log.emit(
                    f"[{layer_name}] {status} | {cache_note}{elapsed_note} | {result['message']}"
                )

                outputs = layer_outputs.get(layer_name, [])
                if outputs:
                    for out_path in outputs:
                        self.log.emit(f"[{layer_name}] output: {out_path}")
            self.log.emit(f"Total elapsed: {total_elapsed:.1f}s")

        try:
            if self._is_cancelled():
                self.finished.emit(False)
                return

            if project.layers["terrain"].enabled:
                layer_started_at["terrain"] = time.perf_counter()
                self.log.emit("[terrain] Starting terrain download stage")
                terrain_downloader = TerrainDownloader(
                    cache_manager=global_cache,
                    progress_callback=layer_progress_cb("terrain"),
                )
                result = terrain_downloader.download(
                    bbox=bbox,
                    resolution_m=int(project.resolution_m),
                    output_path=downloads_dir / "terrain",
                    region="CONUS",
                )
                layer_outputs["terrain"] = list(result.get("files", []))
                self.log.emit(f"[terrain] Download stage finished: {result.get('message', 'no message')}")
                if result["success"] and result["files"]:
                    terrain_src = Path(result["files"][0])
                    terrain_reproj = processed_dir / "terrain_reprojected.tif"
                    self.log.emit("[terrain] Reprojecting terrain raster to target CRS...")
                    ok_reproj = reproject_raster(terrain_src, terrain_reproj, target_crs)
                    self.log.emit(f"[terrain] Reproject result: {'ok' if ok_reproj else 'failed'}")
                    if ok_reproj:
                        self.log.emit("[terrain] Cropping/resampling terrain raster to bbox/resolution...")
                    ok_resample = ok_reproj and crop_and_resample_raster(
                        terrain_reproj,
                        terrain_processed,
                        bbox,
                        float(project.resolution_m),
                        target_crs=target_crs,
                    )
                    self.log.emit(f"[terrain] Crop/resample result: {'ok' if ok_resample else 'failed'}")
                    finalize_layer("terrain", ok_resample, result.get("cache_hit", False), result["message"])
                    all_ok = all_ok and ok_resample
                else:
                    finalize_layer("terrain", False, result.get("cache_hit", False), result["message"])
                    all_ok = False

            if self._is_cancelled():
                self.finished.emit(False)
                return

            if project.layers["buildings"].enabled:
                layer_started_at["buildings"] = time.perf_counter()
                cfg = project.layers["buildings"]
                buildings_downloader = BuildingsDownloader(
                    cache_manager=global_cache,
                    progress_callback=layer_progress_cb("buildings"),
                )
                result = buildings_downloader.download(
                    bbox=bbox,
                    resolution_m=int(project.resolution_m),
                    output_path=downloads_dir / "buildings",
                    building_source=str(cfg.building_source).upper(),
                )
                layer_outputs["buildings"] = list(result.get("files", []))
                if result["success"] and result["files"]:
                    buildings_src = Path(result["files"][0])
                    buildings_processed = processed_dir / "buildings.geojson"
                    ok = reproject_vector(buildings_src, buildings_processed, target_crs, "GeoJSON", clip_bbox=bbox)
                    if ok and terrain_processed.exists():
                        ok = augment_with_height(
                            buildings_processed,
                            terrain_processed,
                            buildings_processed,
                            height_mode=cfg.building_height_mode.value,
                        )
                    finalize_layer("buildings", ok, result.get("cache_hit", False), result["message"])
                    all_ok = all_ok and ok
                else:
                    finalize_layer("buildings", False, result.get("cache_hit", False), result["message"])
                    all_ok = False

            if self._is_cancelled():
                self.finished.emit(False)
                return

            if project.layers["landuse"].enabled:
                layer_started_at["landuse"] = time.perf_counter()
                cfg = project.layers["landuse"]
                landuse_downloader = LanduseDownloader(
                    cache_manager=global_cache,
                    progress_callback=layer_progress_cb("landuse"),
                )
                result = landuse_downloader.download(
                    bbox=bbox,
                    resolution_m=int(project.resolution_m),
                    output_path=downloads_dir / "landuse",
                    include_raster=cfg.landuse_raster,
                    include_vector=cfg.landuse_vector,
                )
                layer_outputs["landuse"] = list(result.get("files", []))
                ok = result["success"]
                if ok and result["files"]:
                    for path_str in result["files"]:
                        src = Path(path_str)
                        if src.suffix.lower() in [".tif", ".tiff"]:
                            tmp = processed_dir / "landuse_reprojected.tif"
                            ok = ok and reproject_raster(src, tmp, target_crs)
                            ok = ok and crop_and_resample_raster(
                                tmp,
                                processed_dir / "landuse.tif",
                                bbox,
                                float(project.resolution_m),
                                target_crs=target_crs,
                            )
                        else:
                            ok = ok and reproject_vector(src, processed_dir / "landuse.geojson", target_crs, "GeoJSON", clip_bbox=bbox)
                finalize_layer("landuse", ok, result.get("cache_hit", False), result["message"])
                all_ok = all_ok and ok

            if self._is_cancelled():
                self.finished.emit(False)
                return

            if project.layers["water"].enabled:
                layer_started_at["water"] = time.perf_counter()
                cfg = project.layers["water"]
                water_downloader = WaterDownloader(
                    cache_manager=global_cache,
                    progress_callback=layer_progress_cb("water"),
                )
                result = water_downloader.download(
                    bbox=bbox,
                    resolution_m=int(project.resolution_m),
                    output_path=downloads_dir / "water",
                    include_vector=cfg.water_vector,
                )
                layer_outputs["water"] = list(result.get("files", []))
                ok = result["success"] and bool(result["files"])
                if ok:
                    src = Path(result["files"][0])
                    ok = reproject_vector(src, processed_dir / "water.geojson", target_crs, "GeoJSON", clip_bbox=bbox)
                    if ok:
                        layer_outputs["water"].append(str(processed_dir / "water.geojson"))
                finalize_layer("water", ok, result.get("cache_hit", False), result["message"])
                all_ok = all_ok and ok

            if self._is_cancelled():
                self.finished.emit(False)
                return

            if project.layers["big_streets"].enabled:
                layer_started_at["big_streets"] = time.perf_counter()
                roads_downloader = RoadsDownloader(
                    cache_manager=global_cache,
                    progress_callback=layer_progress_cb("big_streets"),
                )
                result = roads_downloader.download(
                    bbox=bbox,
                    resolution_m=int(project.resolution_m),
                    output_path=downloads_dir / "roads",
                    include_major=True,
                    include_minor=False,
                )
                layer_outputs["big_streets"] = list(result.get("files", []))
                ok = result["success"] and bool(result["files"])
                if ok:
                    src = Path(result["files"][0])
                    out_path = processed_dir / "big_streets.geojson"
                    ok = reproject_vector(src, out_path, target_crs, "GeoJSON", clip_bbox=bbox)
                    if ok:
                        layer_outputs["big_streets"].append(str(out_path))
                finalize_layer("big_streets", ok, result.get("cache_hit", False), result["message"])
                all_ok = all_ok and ok

            if self._is_cancelled():
                self.finished.emit(False)
                return

            if project.layers["small_streets"].enabled:
                layer_started_at["small_streets"] = time.perf_counter()
                roads_downloader = RoadsDownloader(
                    cache_manager=global_cache,
                    progress_callback=layer_progress_cb("small_streets"),
                )
                result = roads_downloader.download(
                    bbox=bbox,
                    resolution_m=int(project.resolution_m),
                    output_path=downloads_dir / "roads",
                    include_major=False,
                    include_minor=True,
                )
                layer_outputs["small_streets"] = list(result.get("files", []))
                ok = result["success"] and bool(result["files"])
                if ok:
                    src = Path(result["files"][0])
                    out_path = processed_dir / "small_streets.geojson"
                    ok = reproject_vector(src, out_path, target_crs, "GeoJSON", clip_bbox=bbox)
                    if ok:
                        layer_outputs["small_streets"].append(str(out_path))
                finalize_layer("small_streets", ok, result.get("cache_hit", False), result["message"])
                all_ok = all_ok and ok

            if self._is_cancelled():
                self.finished.emit(False)
                return

            if project.layers["reference"].enabled:
                layer_started_at["reference"] = time.perf_counter()
                cfg = project.layers["reference"]
                reference_downloader = ReferenceDownloader(
                    cache_manager=global_cache,
                    progress_callback=layer_progress_cb("reference"),
                )
                # Download tiles for the expanded context bbox so both the main
                # crop and the +25% context image have full tile coverage.
                context_bbox = bbox.expanded(0.25)
                result = reference_downloader.download(
                    bbox=context_bbox,
                    resolution_m=int(project.resolution_m),
                    output_path=downloads_dir / "reference",
                    zoom_level=cfg.reference_zoom,
                )
                layer_outputs["reference"] = list(result.get("files", []))
                ok = result["success"] and bool(result["files"])
                reference_message = result["message"]
                if ok:
                    src = Path(result["files"][0])
                    tmp = processed_dir / "reference_reprojected.tif"
                    ok = reproject_raster(src, tmp, target_crs)
                    ok = ok and crop_and_resample_raster(
                        tmp,
                        processed_dir / "reference.tif",
                        bbox,
                        float(project.resolution_m),
                        target_crs=target_crs,
                    )

                    # Also create a context image with +25% margin around bbox.
                    # Context crop is non-fatal: a failure does not mark the
                    # reference layer as failed.
                    if ok:
                        ok_context = crop_and_resample_raster(
                            tmp,
                            processed_dir / "reference_context.tif",
                            context_bbox,
                            float(project.resolution_m),
                            target_crs=target_crs,
                        )
                        if ok_context:
                            ok_inset = self._draw_reference_inset_border(
                                processed_dir / "reference_context.tif",
                                bbox,
                            )
                            if ok_inset:
                                reference_message = (
                                    f"{result['message']} + context image (25% margin, original-area inset)"
                                )
                            else:
                                reference_message = f"{result['message']} + context image (25% margin)"
                                self.log.emit("[reference] Context image generated, but failed to draw inset border")
                            layer_outputs["reference"].append(str(processed_dir / "reference_context.tif"))
                        else:
                            self.log.emit("[reference] Context image skipped (tiles did not cover 25% margin)")

                    layer_outputs["reference"].append(str(processed_dir / "reference.tif"))

                finalize_layer("reference", ok, result.get("cache_hit", False), reference_message)
                all_ok = all_ok and ok

            emit_run_summary()
            self.finished.emit(all_ok)
        except Exception as exc:
            self.log.emit(f"Pipeline failed: {exc}")
            emit_run_summary()
            self.finished.emit(False)
        finally:
            global_cache.cleanup()
            project_cache.cleanup()


class DownloadPage(QWizardPage):
    """Step 4: Download data with progress tracking."""

    _STATUS_STYLE = {
        "Idle": ("#6B7280", "#F3F4F6"),
        "Running": ("#1D4ED8", "#DBEAFE"),
        "Cancelling": ("#92400E", "#FEF3C7"),
        "Completed": ("#065F46", "#D1FAE5"),
        "Cancelled": ("#9A3412", "#FFEDD5"),
        "Failed": ("#991B1B", "#FEE2E2"),
    }

    def __init__(self):
        super().__init__()
        self._download_success = False
        self._run_started = False
        self._is_running = False
        self._thread = None
        self._worker = None
        self._last_layer_status = {}
        self.setTitle("Step 4: Download Data")
        self.setSubTitle("Downloading GIS data layers...")

        layout = QVBoxLayout()

        # Overall progress
        overall_layout = QHBoxLayout()
        overall_layout.addWidget(QLabel("Overall Progress:"))
        self.overall_progress = QProgressBar()
        self.overall_progress.setRange(0, 100)
        overall_layout.addWidget(self.overall_progress)
        layout.addLayout(overall_layout)

        status_row = QHBoxLayout()
        status_row.addWidget(QLabel("Run Status:"))
        self.status_badge = QLabel()
        self.status_badge.setMinimumWidth(110)
        self.status_badge.setAlignment(Qt.AlignCenter)
        status_row.addWidget(self.status_badge)
        status_row.addStretch()
        layout.addLayout(status_row)

        # Per-layer progress
        self.progress_panels = {}
        for layer_name in ["terrain", "buildings", "landuse", "water", "big_streets", "small_streets", "reference"]:
            panel = ProgressPanel(layer_name)
            self.progress_panels[layer_name] = panel
            layout.addWidget(panel)

        # Download log
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(150)
        layout.addWidget(QLabel("Download Log:"))
        layout.addWidget(self.log_text)

        # Control buttons
        btn_layout = QHBoxLayout()
        self.run_btn = QPushButton("Run Download + Processing")
        self.run_btn.clicked.connect(self._run_pipeline)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._cancel_wizard)
        btn_layout.addWidget(self.run_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(self.cancel_btn)
        layout.addLayout(btn_layout)

        self.setLayout(layout)
        self._set_run_status("Idle")

    def _append_log(self, message: str):
        self.log_text.append(message)

    def _set_layer_progress(self, layer: str, percent: int, status: str):
        panel = self.progress_panels[layer]
        panel.set_progress(percent)
        panel.set_status(status)
        if self._last_layer_status.get(layer) != status:
            self._append_log(f"[{layer}] {status} ({percent}%)")
            self._last_layer_status[layer] = status

    def _set_run_status(self, status: str):
        """Keep in-page badge and wizard title status synchronized."""
        fg, bg = self._STATUS_STYLE.get(status, ("#374151", "#F3F4F6"))
        self.status_badge.setText(status)
        self.status_badge.setStyleSheet(
            f"color: {fg}; "
            f"background-color: {bg}; "
            f"border: 1px solid {fg}; "
            "border-radius: 10px; "
            "padding: 4px; "
            "font-weight: bold;"
        )

        wiz = self.wizard()
        if wiz is not None and hasattr(wiz, "set_run_status"):
            wiz.set_run_status(status)

    def _on_layer_done(self, layer_name: str, success: bool, cache_hit: bool, message: str):
        panel = self.progress_panels[layer_name]
        panel.set_cache_hit(cache_hit)
        panel.set_status("Done" if success else "Failed")
        panel.set_progress(100 if success else 0)
        self._append_log(f"[{layer_name}] {message}")

    def _on_worker_finished(self, success: bool):
        was_cancelled = bool(self._worker and self._worker.cancel_requested)
        self._download_success = success
        self._is_running = False
        if success:
            self.setSubTitle("Download and processing completed successfully.")
            self.overall_progress.setValue(100)
            self._set_run_status("Completed")
        else:
            self.setSubTitle("Completed with errors or cancelled. Check log for details.")
            self._set_run_status("Cancelled" if was_cancelled else "Failed")

        if self._thread is not None:
            self._thread.quit()
            self._thread.wait()
            self._thread = None
            self._worker = None

        self.run_btn.setEnabled(True)

    def initializePage(self):
        """Reset state when page appears."""
        self.overall_progress.setValue(0)
        self.log_text.clear()
        self._last_layer_status.clear()
        for panel in self.progress_panels.values():
            panel.reset()
        self._download_success = False
        self._run_started = False
        self._is_running = False
        self.run_btn.setEnabled(True)
        self.log_text.append("Ready. Click 'Run Download + Processing' to start.\n")
        self._set_run_status("Idle")

    def _run_pipeline(self):
        if self._is_running:
            return

        wizard = self.wizard()
        if wizard is None or not hasattr(wizard, "project"):
            self._append_log("Error: project context is unavailable.")
            return

        project = wizard.project
        bbox = project.get_bbox()
        if bbox is None:
            self._append_log("Error: no bounding box configured.")
            return
        if not project.output_folder:
            self._append_log("Error: no output folder configured in Step 3.")
            return

        self._run_started = True
        self._is_running = True
        self._download_success = False
        self.run_btn.setEnabled(False)
        self._set_run_status("Running")

        self._thread = QThread(self)
        self._worker = DownloadWorker(project)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.layer_progress.connect(self._set_layer_progress)
        self._worker.layer_done.connect(self._on_layer_done)
        self._worker.overall_progress.connect(self.overall_progress.setValue)
        self._worker.log.connect(self._append_log)
        self._worker.finished.connect(self._on_worker_finished)

        self._thread.start()

    def _cancel_wizard(self):
        if self._is_running and self._worker is not None:
            self._worker.cancel()
            self._append_log("Cancelling current run...")
            self._set_run_status("Cancelling")
            return

        wizard = self.wizard()
        if wizard is not None:
            wizard.reject()

    def validatePage(self) -> bool:
        """Check if download completed successfully."""
        if self._is_running:
            self.setSubTitle("Download/processing is still running.")
            return False
        if not self._run_started:
            self.setSubTitle("Run download + processing before continuing.")
            return False
        if not self._download_success:
            self.setSubTitle("Resolve download/processing errors before continuing.")
            return False
        return True

    def nextId(self) -> int:
        """Next page ID."""
        return 4
