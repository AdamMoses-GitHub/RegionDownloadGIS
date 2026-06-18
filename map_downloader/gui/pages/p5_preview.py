"""Page 5: 2D Preview and 3D Viewer Placeholder."""

from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.transform import Affine
from PySide6.QtWidgets import (
    QWizardPage, QVBoxLayout, QHBoxLayout, QLabel, QTabWidget,
    QWidget, QCheckBox, QTextEdit, QComboBox
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap

from map_downloader.gui.viewer_interface import NullViewer


class PreviewPage(QWizardPage):
    """Step 5: Preview downloaded data with optional 3D viewer."""

    PREVIEW_MAX_DIM = 1024
    PREVIEW_QUALITY_DIMS = {
        "Fast": 640,
        "Balanced": 1024,
        "High": 1600,
    }
    
    def __init__(self):
        super().__init__()
        self.viewer_backend = NullViewer()
        self._pixmap_cache: dict[tuple, QPixmap] = {}
        self._stats_cache_key: tuple | None = None
        self._stats_cache_text: str = ""
        self.setTitle("Step 5: Preview Data")
        self.setSubTitle("Review your downloaded data layers")
        
        layout = QVBoxLayout()
        
        # Tabs
        self.tabs = QTabWidget()
        
        # Tab 1: 2D Map
        map_widget = QWidget()
        map_layout = QVBoxLayout()
        self.map_label = QLabel("2D Map Preview")
        self.map_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.map_label.setMinimumHeight(300)
        self.map_label.setStyleSheet("background-color: #f0f0f0; border: 1px solid gray;")
        map_layout.addWidget(self.map_label)
        
        # Layer toggles
        toggles_layout = QHBoxLayout()
        self.terrain_check = QCheckBox("Terrain")
        self.terrain_check.setChecked(True)
        self.buildings_check = QCheckBox("Buildings")
        self.buildings_check.setChecked(True)
        self.landuse_check = QCheckBox("Land Use")
        self.landuse_check.setChecked(True)
        self.water_check = QCheckBox("Water")
        self.water_check.setChecked(True)
        self.big_streets_check = QCheckBox("Big Streets")
        self.big_streets_check.setChecked(True)
        self.small_streets_check = QCheckBox("Small Streets")
        self.small_streets_check.setChecked(True)
        self.reference_check = QCheckBox("Reference")
        self.reference_check.setChecked(True)
        quality_label = QLabel("Preview Quality:")
        self.preview_quality_combo = QComboBox()
        self.preview_quality_combo.addItems(["Fast", "Balanced", "High"])
        self.preview_quality_combo.setCurrentText("Balanced")
        toggles_layout.addWidget(self.terrain_check)
        toggles_layout.addWidget(self.buildings_check)
        toggles_layout.addWidget(self.landuse_check)
        toggles_layout.addWidget(self.water_check)
        toggles_layout.addWidget(self.big_streets_check)
        toggles_layout.addWidget(self.small_streets_check)
        toggles_layout.addWidget(self.reference_check)
        toggles_layout.addSpacing(12)
        toggles_layout.addWidget(quality_label)
        toggles_layout.addWidget(self.preview_quality_combo)
        toggles_layout.addStretch()
        map_layout.addLayout(toggles_layout)
        
        map_widget.setLayout(map_layout)
        self.tabs.addTab(map_widget, "2D Map")

        # Tab 2: Statistics
        stats_widget = QWidget()
        stats_layout = QVBoxLayout()
        self.stats_text = QTextEdit()
        self.stats_text.setReadOnly(True)
        stats_layout.addWidget(self.stats_text)
        stats_widget.setLayout(stats_layout)
        self.tabs.addTab(stats_widget, "Statistics")
        
        # Tab 3: 3D Viewer backend (currently NullViewer placeholder)
        viewer_widget = self.viewer_backend.create_widget(parent=self)
        self.tabs.addTab(viewer_widget, "3D View")
        self.tabs.setTabEnabled(2, self.viewer_backend.is_available())

        self.terrain_check.toggled.connect(self._refresh_preview)
        self.buildings_check.toggled.connect(self._refresh_preview)
        self.landuse_check.toggled.connect(self._refresh_preview)
        self.water_check.toggled.connect(self._refresh_preview)
        self.big_streets_check.toggled.connect(self._refresh_preview)
        self.small_streets_check.toggled.connect(self._refresh_preview)
        self.reference_check.toggled.connect(self._refresh_preview)
        self.preview_quality_combo.currentTextChanged.connect(self._on_preview_quality_changed)
        
        layout.addWidget(self.tabs)
        
        self.setLayout(layout)

    def initializePage(self):
        """Refresh preview and statistics when entering the page."""
        self._refresh_preview()

    def _get_output_root(self) -> Path | None:
        wizard = self.wizard()
        if wizard is None or not hasattr(wizard, "project"):
            return None
        try:
            return wizard.project.resolve_output_root(force_refresh=False)
        except Exception:
            return None

    def _refresh_preview(self):
        """Render 2D preview and statistics from processed outputs."""
        output_root = self._get_output_root()
        if output_root is None:
            self.map_label.setText("2D Map Preview\n(project unavailable)")
            self.stats_text.setText("Project context unavailable.")
            self.map_label.setPixmap(QPixmap())
            return

        processed = output_root / "processed"
        if not processed.exists():
            self.map_label.setText("2D Map Preview\n(no processed outputs yet)")
            self.stats_text.setText(f"No processed outputs found at:\n{processed}")
            self.map_label.setPixmap(QPixmap())
            return

        preview_file = self._choose_preview_raster(processed)
        if preview_file is None:
            self.map_label.setText("2D Map Preview\n(no raster layer selected/available)")
            self.map_label.setPixmap(QPixmap())
        else:
            buildings_overlay = processed / "buildings.geojson" if self.buildings_check.isChecked() else None
            water_overlay = processed / "water.geojson" if self.water_check.isChecked() else None
            big_streets_overlay = processed / "big_streets.geojson" if self.big_streets_check.isChecked() else None
            small_streets_overlay = processed / "small_streets.geojson" if self.small_streets_check.isChecked() else None
            target_max_dim = self._target_preview_dim()
            cache_key = self._preview_cache_key(
                preview_file,
                buildings_overlay,
                water_overlay,
                big_streets_overlay,
                small_streets_overlay,
                target_max_dim,
            )
            pixmap = self._pixmap_cache.get(cache_key)
            if pixmap is None:
                pixmap = self._raster_to_pixmap(
                    preview_file,
                    buildings_overlay,
                    water_overlay,
                    big_streets_overlay,
                    small_streets_overlay,
                    target_max_dim,
                )
                if pixmap is not None:
                    self._pixmap_cache[cache_key] = pixmap
            if pixmap is not None:
                self.map_label.setPixmap(
                    pixmap.scaled(
                        self.map_label.width(),
                        self.map_label.height(),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
                self.map_label.setText("")
            else:
                self.map_label.setText(f"2D Map Preview\n(failed to render {preview_file.name})")
                self.map_label.setPixmap(QPixmap())

        stats_key = self._stats_key(processed)
        if stats_key != self._stats_cache_key:
            self._stats_cache_text = self._build_stats_text(processed)
            self._stats_cache_key = stats_key
        self.stats_text.setText(self._stats_cache_text)

    def _target_preview_dim(self) -> int:
        """Compute a bounded render target for responsive previews."""
        quality = self.preview_quality_combo.currentText()
        quality_cap = self.PREVIEW_QUALITY_DIMS.get(quality, self.PREVIEW_MAX_DIM)
        label_w = max(1, self.map_label.width())
        label_h = max(1, self.map_label.height())
        return int(min(quality_cap, max(label_w, label_h) * 2))

    def _on_preview_quality_changed(self):
        """Invalidate cached preview pixmaps when quality setting changes."""
        self._pixmap_cache.clear()
        self._refresh_preview()

    def _mtime_ns(self, path: Path | None) -> int:
        if path is None or not path.exists():
            return -1
        try:
            return path.stat().st_mtime_ns
        except Exception:
            return -1

    def _preview_cache_key(
        self,
        raster_path: Path,
        buildings_path: Path | None,
        water_path: Path | None,
        big_streets_path: Path | None,
        small_streets_path: Path | None,
        target_max_dim: int,
    ) -> tuple:
        return (
            str(raster_path),
            self._mtime_ns(raster_path),
            str(buildings_path) if buildings_path is not None else "",
            self._mtime_ns(buildings_path),
            str(water_path) if water_path is not None else "",
            self._mtime_ns(water_path),
            str(big_streets_path) if big_streets_path is not None else "",
            self._mtime_ns(big_streets_path),
            str(small_streets_path) if small_streets_path is not None else "",
            self._mtime_ns(small_streets_path),
            target_max_dim,
        )

    def _stats_key(self, processed_dir: Path) -> tuple:
        tracked = [
            processed_dir / "terrain.tif",
            processed_dir / "buildings.geojson",
            processed_dir / "landuse.tif",
            processed_dir / "landuse.geojson",
            processed_dir / "water.geojson",
            processed_dir / "big_streets.geojson",
            processed_dir / "small_streets.geojson",
            processed_dir / "reference.tif",
        ]
        return tuple((str(path), self._mtime_ns(path)) for path in tracked)

    def _choose_preview_raster(self, processed_dir: Path) -> Path | None:
        """Pick a raster file for visualization from selected layer toggles."""
        candidates = []
        if self.reference_check.isChecked():
            candidates.append(processed_dir / "reference.tif")
        if self.terrain_check.isChecked():
            candidates.append(processed_dir / "terrain.tif")
        if self.landuse_check.isChecked():
            candidates.append(processed_dir / "landuse.tif")

        for path in candidates:
            if path.exists():
                return path
        return None

    def _raster_to_pixmap(
        self,
        raster_path: Path,
        buildings_path: Path | None = None,
        water_path: Path | None = None,
        big_streets_path: Path | None = None,
        small_streets_path: Path | None = None,
        target_max_dim: int = PREVIEW_MAX_DIM,
    ) -> QPixmap | None:
        """Convert a raster to a displayable pixmap."""
        try:
            with rasterio.open(raster_path) as src:
                src_max_dim = max(src.width, src.height)
                if src_max_dim > target_max_dim:
                    scale = src_max_dim / float(target_max_dim)
                    out_width = max(1, int(src.width / scale))
                    out_height = max(1, int(src.height / scale))
                else:
                    out_width = src.width
                    out_height = src.height

                read_kwargs = {
                    "out_shape": (out_height, out_width),
                    "resampling": Resampling.bilinear,
                }
                preview_transform = src.transform * Affine.scale(
                    src.width / float(out_width),
                    src.height / float(out_height),
                )

                if src.count >= 3:
                    r = src.read(1, masked=True, **read_kwargs)
                    g = src.read(2, masked=True, **read_kwargs)
                    b = src.read(3, masked=True, **read_kwargs)
                    rgb = np.stack([
                        self._normalize_band(r),
                        self._normalize_band(g),
                        self._normalize_band(b),
                    ], axis=-1)
                else:
                    band = src.read(1, masked=True, **read_kwargs)
                    gray = self._normalize_band(band)
                    rgb = np.stack([gray, gray, gray], axis=-1)

                if buildings_path is not None and buildings_path.exists():
                    self._apply_buildings_overlay(
                        rgb,
                        buildings_path,
                        src.crs,
                        preview_transform,
                    )

                if water_path is not None and water_path.exists():
                    self._apply_water_overlay(
                        rgb,
                        water_path,
                        src.crs,
                        preview_transform,
                    )

                if big_streets_path is not None and big_streets_path.exists():
                    self._apply_roads_overlay(
                        rgb,
                        big_streets_path,
                        src.crs,
                        preview_transform,
                        line_color=np.array([245, 180, 45], dtype=np.float32),
                    )

                if small_streets_path is not None and small_streets_path.exists():
                    self._apply_roads_overlay(
                        rgb,
                        small_streets_path,
                        src.crs,
                        preview_transform,
                        line_color=np.array([255, 220, 120], dtype=np.float32),
                    )

                height, width = rgb.shape[0], rgb.shape[1]
                image = QImage(
                    rgb.data,
                    width,
                    height,
                    3 * width,
                    QImage.Format.Format_RGB888,
                )
                return QPixmap.fromImage(image.copy())
        except Exception:
            return None

    def _apply_buildings_overlay(self, rgb: np.ndarray, buildings_path: Path, raster_crs, raster_transform):
        """Draw building polygons as a semi-transparent overlay in raster pixel space."""
        try:
            gdf = gpd.read_file(buildings_path)
            if len(gdf) == 0 or "geometry" not in gdf.columns:
                return

            if raster_crs is not None and gdf.crs is not None and gdf.crs != raster_crs:
                gdf = gdf.to_crs(raster_crs)

            shapes = [(geom, 1) for geom in gdf.geometry if geom is not None and not geom.is_empty]
            if not shapes:
                return

            mask = rasterize(
                shapes,
                out_shape=(rgb.shape[0], rgb.shape[1]),
                transform=raster_transform,
                fill=0,
                all_touched=False,
                dtype="uint8",
            ).astype(bool)

            if not mask.any():
                return

            # Semi-transparent fill for building footprints.
            fill_color = np.array([255, 90, 70], dtype=np.float32)
            alpha = 0.32
            rgb_f = rgb.astype(np.float32)
            rgb_f[mask] = (1.0 - alpha) * rgb_f[mask] + alpha * fill_color

            # Stronger edge to make individual buildings easier to read.
            eroded = mask.copy()
            eroded[1:, :] &= mask[:-1, :]
            eroded[:-1, :] &= mask[1:, :]
            eroded[:, 1:] &= mask[:, :-1]
            eroded[:, :-1] &= mask[:, 1:]
            edge = mask & (~eroded)
            rgb_f[edge] = np.array([220, 20, 20], dtype=np.float32)

            rgb[:, :, :] = np.clip(rgb_f, 0, 255).astype(np.uint8)
        except Exception:
            # Keep preview resilient; failure to overlay vectors should not fail raster preview.
            return

    def _apply_water_overlay(self, rgb: np.ndarray, water_path: Path, raster_crs, raster_transform):
        """Draw water polygons as a blue semi-transparent overlay in raster pixel space."""
        try:
            gdf = gpd.read_file(water_path)
            if len(gdf) == 0 or "geometry" not in gdf.columns:
                return

            if raster_crs is not None and gdf.crs is not None and gdf.crs != raster_crs:
                gdf = gdf.to_crs(raster_crs)

            shapes = [(geom, 1) for geom in gdf.geometry if geom is not None and not geom.is_empty]
            if not shapes:
                return

            mask = rasterize(
                shapes,
                out_shape=(rgb.shape[0], rgb.shape[1]),
                transform=raster_transform,
                fill=0,
                all_touched=False,
                dtype="uint8",
            ).astype(bool)

            if not mask.any():
                return

            fill_color = np.array([40, 130, 255], dtype=np.float32)
            alpha = 0.38
            rgb_f = rgb.astype(np.float32)
            rgb_f[mask] = (1.0 - alpha) * rgb_f[mask] + alpha * fill_color

            # Stronger blue edge for readability.
            eroded = mask.copy()
            eroded[1:, :] &= mask[:-1, :]
            eroded[:-1, :] &= mask[1:, :]
            eroded[:, 1:] &= mask[:, :-1]
            eroded[:, :-1] &= mask[:, 1:]
            edge = mask & (~eroded)
            rgb_f[edge] = np.array([15, 85, 220], dtype=np.float32)

            rgb[:, :, :] = np.clip(rgb_f, 0, 255).astype(np.uint8)
        except Exception:
            return

    def _apply_roads_overlay(
        self,
        rgb: np.ndarray,
        roads_path: Path,
        raster_crs,
        raster_transform,
        line_color: np.ndarray,
    ):
        """Draw roads as highlighted line overlays in raster pixel space."""
        try:
            gdf = gpd.read_file(roads_path)
            if len(gdf) == 0 or "geometry" not in gdf.columns:
                return

            if raster_crs is not None and gdf.crs is not None and gdf.crs != raster_crs:
                gdf = gdf.to_crs(raster_crs)

            shapes = [(geom, 1) for geom in gdf.geometry if geom is not None and not geom.is_empty]
            if not shapes:
                return

            mask = rasterize(
                shapes,
                out_shape=(rgb.shape[0], rgb.shape[1]),
                transform=raster_transform,
                fill=0,
                all_touched=True,
                dtype="uint8",
            ).astype(bool)

            if not mask.any():
                return

            alpha = 0.45
            rgb_f = rgb.astype(np.float32)
            rgb_f[mask] = (1.0 - alpha) * rgb_f[mask] + alpha * line_color
            rgb[:, :, :] = np.clip(rgb_f, 0, 255).astype(np.uint8)
        except Exception:
            return

    def _normalize_band(self, band: np.ma.MaskedArray) -> np.ndarray:
        """Normalize raster values into 0-255 uint8 range."""
        if band is None:
            return np.zeros((1, 1), dtype=np.uint8)

        if np.ma.isMaskedArray(band):
            values = band.compressed()
            filled = band.filled(np.nan)
        else:
            values = np.asarray(band).ravel()
            filled = np.asarray(band, dtype=float)

        finite = values[np.isfinite(values)] if values.size else np.array([])
        if finite.size == 0:
            return np.zeros(filled.shape, dtype=np.uint8)

        vmin = float(np.percentile(finite, 2))
        vmax = float(np.percentile(finite, 98))
        if vmax <= vmin:
            vmax = vmin + 1.0

        scaled = np.clip((filled - vmin) / (vmax - vmin), 0.0, 1.0)
        scaled = np.nan_to_num(scaled, nan=0.0)
        return (scaled * 255.0).astype(np.uint8)

    def _build_stats_text(self, processed_dir: Path) -> str:
        """Build per-data-type summary statistics for the statistics tab."""
        lines = ["Summary Statistics", "===================", ""]

        lines.extend(self._raster_stats_block("Terrain", processed_dir / "terrain.tif"))
        lines.append("")
        lines.extend(self._vector_stats_block("Buildings", processed_dir / "buildings.geojson"))
        lines.append("")
        lines.extend(self._landuse_stats_block(processed_dir))
        lines.append("")
        lines.extend(self._vector_stats_block("Water", processed_dir / "water.geojson"))
        lines.append("")
        lines.extend(self._vector_stats_block("Big Streets", processed_dir / "big_streets.geojson"))
        lines.append("")
        lines.extend(self._vector_stats_block("Small Streets", processed_dir / "small_streets.geojson"))
        lines.append("")
        lines.extend(self._raster_stats_block("Reference", processed_dir / "reference.tif"))

        return "\n".join(lines)

    def _raster_stats_block(self, title: str, path: Path) -> list[str]:
        if not path.exists():
            return [f"{title}: not available"]

        try:
            with rasterio.open(path) as src:
                lines = [f"{title} ({path.name})"]
                lines.append(f"- Size: {src.width} x {src.height}, bands={src.count}")
                lines.append(f"- CRS: {src.crs}")

                band = src.read(1, masked=True)
                valid = band.compressed() if np.ma.isMaskedArray(band) else band.ravel()
                valid = valid[np.isfinite(valid)] if valid.size else np.array([])
                if valid.size:
                    lines.append(
                        "- Band1 min/mean/max: "
                        f"{float(np.min(valid)):.2f} / {float(np.mean(valid)):.2f} / {float(np.max(valid)):.2f}"
                    )
                else:
                    lines.append("- Band1: no valid pixels")

                return lines
        except Exception as exc:
            return [f"{title}: failed to compute stats ({exc})"]

    def _vector_stats_block(self, title: str, path: Path) -> list[str]:
        if not path.exists():
            return [f"{title}: not available"]

        try:
            gdf = gpd.read_file(path)
            lines = [f"{title} ({path.name})"]
            lines.append(f"- Feature count: {len(gdf)}")
            if len(gdf) == 0:
                return lines

            geom_counts = gdf.geometry.geom_type.value_counts()
            geom_summary = ", ".join([f"{k}:{v}" for k, v in geom_counts.items()])
            lines.append(f"- Geometry types: {geom_summary}")

            try:
                area_gdf = gdf.to_crs(epsg=3857) if gdf.crs else gdf
                areas = area_gdf.geometry.area.to_numpy(dtype=float)
                areas = areas[np.isfinite(areas)]
                if areas.size:
                    lines.append(
                        "- Area m2 min/mean/max: "
                        f"{float(np.min(areas)):.1f} / {float(np.mean(areas)):.1f} / {float(np.max(areas)):.1f}"
                    )
            except Exception:
                pass

            return lines
        except Exception as exc:
            return [f"{title}: failed to compute stats ({exc})"]

    def _landuse_stats_block(self, processed_dir: Path) -> list[str]:
        raster_path = processed_dir / "landuse.tif"
        vector_path = processed_dir / "landuse.geojson"

        lines = ["Land Use"]
        if raster_path.exists():
            lines.extend([f"- Raster: {raster_path.name}"])
            lines.extend([f"  {line}" for line in self._raster_stats_block("", raster_path)[1:]])
            try:
                with rasterio.open(raster_path) as src:
                    band = src.read(1, masked=True)
                    vals = band.compressed() if np.ma.isMaskedArray(band) else band.ravel()
                    vals = vals[np.isfinite(vals)] if vals.size else np.array([])
                    if vals.size:
                        unique_count = int(np.unique(vals.astype(np.int64)).size)
                        lines.append(f"  - Unique class values: {unique_count}")
            except Exception:
                pass
        else:
            lines.append("- Raster: not available")

        if vector_path.exists():
            lines.extend([f"- Vector: {vector_path.name}"])
            vec_lines = self._vector_stats_block("", vector_path)
            for line in vec_lines[1:]:
                lines.append(f"  {line}")
        else:
            lines.append("- Vector: not available")

        return lines
    
    def nextId(self) -> int:
        """Next page ID."""
        return 5
