"""Main QWizard implementation."""

from PySide6.QtWidgets import QWizard, QMessageBox
from PySide6.QtCore import Qt
from map_downloader.gui.pages.p1_bbox import BboxPage
from map_downloader.gui.pages.p2_layers import LayersPage
from map_downloader.gui.pages.p3_output import OutputPage
from map_downloader.gui.pages.p4_download import DownloadPage
from map_downloader.gui.pages.p5_preview import PreviewPage
from map_downloader.gui.pages.p6_export import ExportPage
from map_downloader.core.project import Project, TimestampMode
from pathlib import Path


class MapDownloaderWizard(QWizard):
    """Main wizard for Region3D Map Data Downloader."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._last_page_id = 0
        self._run_status = "Idle"
        self._project_file_path: str | None = None

        # Use standard top-level window controls (minimize/maximize/close) on Windows.
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        
        self._update_window_title(0)
        self.setGeometry(100, 100, 900, 700)
        
        # Current project
        self.project = Project()
        
        # Add pages
        self.bbox_page = BboxPage()
        self.layers_page = LayersPage()
        self.output_page = OutputPage()
        self.download_page = DownloadPage()
        self.preview_page = PreviewPage()
        self.export_page = ExportPage()
        
        self.addPage(self.bbox_page)        # ID 0
        self.addPage(self.layers_page)      # ID 1
        self.addPage(self.output_page)      # ID 2
        self.addPage(self.download_page)    # ID 3
        self.addPage(self.preview_page)     # ID 4
        self.addPage(self.export_page)      # ID 5
        
        # Connect signals
        self.currentIdChanged.connect(self._on_page_changed)
        self.button(QWizard.WizardButton.FinishButton).clicked.connect(self._on_finish)
        
        # Customize buttons
        self.setButtonText(QWizard.WizardButton.BackButton, "Previous")
        self.setButtonText(QWizard.WizardButton.NextButton, "Next")
        self.setButtonText(QWizard.WizardButton.FinishButton, "Finish")
        self.setButtonText(QWizard.WizardButton.CancelButton, "Quit Application")
        
        # Add toolbar with Save/Load
        self._create_toolbar()
    
    def _create_toolbar(self):
        """Create toolbar with Save/Load buttons."""
        # This would be better in a custom wizard, but for now we use button customization
        pass
    
    def _on_page_changed(self, page_id: int):
        """Handle page change."""
        # Sync data from the page being left.
        self._sync_project_from_page(self._last_page_id)
        self._last_page_id = page_id

        self._update_window_title(page_id)
        
        # Keep page data up to date while staying on the page as well.
        self._sync_project_from_page(page_id)

    def _step_title(self, page_id: int) -> str:
        if page_id == 0:
            return "Step 1: Define Area"
        if page_id == 1:
            return "Step 2: Configure Layers"
        if page_id == 2:
            return "Step 3: Output Settings"
        if page_id == 3:
            return "Step 4: Download"
        if page_id == 4:
            return "Step 5: Preview"
        if page_id == 5:
            return "Step 6: Export"
        return "Wizard"

    def _update_window_title(self, page_id: int):
        base = "Region3D Map Data Downloader"
        step = self._step_title(page_id)
        self.setWindowTitle(f"{base} - {step} [{self._run_status}]")

    def set_run_status(self, status: str):
        """Update run status badge in title bar."""
        self._run_status = status
        self._update_window_title(self.currentId())
    
    def _sync_project_from_page(self, page_id: int):
        """Sync project data from current page."""
        if page_id == 0:
            try:
                bbox = self.bbox_page.bbox_widget.get_bbox()
                self.project.set_bbox(bbox)
            except Exception as exc:
                print(f"Warning: failed to sync bbox from page: {exc}")
        elif page_id == 1:
            for layer_name, card in self.layers_page.layer_cards.items():
                config = card.get_config()
                self.project.layers[layer_name] = config
        elif page_id == 2:
            self.project.name = self.output_page.name_input.text()
            self.project.output_folder = self.output_page.folder_input.text()
            self.project.resolution_m = self.output_page.resolution_spin.value()
            mode = self.output_page.selected_timestamp_mode()
            self.project.timestamp_mode = mode
            self.project.append_timestamp_to_name = (mode != TimestampMode.NONE.value)
            utm_val = self.output_page.utm_spin.value()
            self.project.utm_zone_override = utm_val if utm_val > 0 else None
            self.project.resolve_output_root(force_refresh=False)
    
    def _on_finish(self):
        """Handle finish button."""
        try:
            self.save_project_to_default_location()
        except Exception as e:
            print(f"Error saving project: {e}")
        
        self.accept()

    def _confirm_quit(self) -> bool:
        """Return True if user confirms quitting the application."""
        reply = QMessageBox.question(
            self,
            "Quit Application",
            "Quit Region3D Map Data Downloader? Unsaved changes may be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def reject(self):
        """Intercept cancel/close to confirm quitting."""
        if self._confirm_quit():
            super().reject()

    def save_project_to_default_location(self) -> str:
        """Sync current page and save project into output root with sanitized name."""
        self._sync_project_from_page(self.currentId())
        output_root = self.project.resolve_output_root(force_refresh=False)
        project_stem = self.project.sanitize_name(self.project.name)
        project_file = output_root / f"{project_stem}.r3d.json"
        project_file.parent.mkdir(parents=True, exist_ok=True)
        self.project.save(str(project_file))
        self._project_file_path = str(project_file)
        return str(project_file)

    def save_project_as(self, path: str) -> str:
        """Sync current page and save project to explicit path."""
        self._sync_project_from_page(self.currentId())
        path_obj = Path(path)
        path_obj.parent.mkdir(parents=True, exist_ok=True)
        self.project.save(str(path_obj))
        self._project_file_path = str(path_obj)
        return str(path_obj)

    def load_project_from_file(self, path: str):
        """Load project from .r3d.json and refresh UI page state."""
        loaded = Project.load(path)
        self.project = loaded
        self._project_file_path = str(path)
        self._apply_project_to_pages()

    def current_project_file_path(self) -> str | None:
        """Return last explicit project file path if known."""
        return self._project_file_path

    def reset_new_project(self):
        """Reset wizard to a new blank project and refresh page state."""
        self.project = Project()
        self._project_file_path = None
        self._apply_project_to_pages()
        self.setCurrentId(0)

    def _apply_project_to_pages(self):
        """Push current project values into page widgets."""
        bbox = self.project.get_bbox()
        if bbox is not None:
            crs = "utm" if bbox.has_strict_utm_bounds() else "latlong"
            self.bbox_page.bbox_widget.mode_corners.setChecked(True)
            self.bbox_page.bbox_widget.crs_utm.setChecked(crs == "utm")
            self.bbox_page.bbox_widget.crs_latlong.setChecked(crs != "utm")
            if crs == "utm":
                min_e, min_n, max_e, max_n = bbox.get_utm_bounds()
                zone = bbox.get_utm_zone()
                self.bbox_page.bbox_widget.utm_zone_input.setValue(zone)
                self.bbox_page.bbox_widget.lat1_input.setValue(max_n)
                self.bbox_page.bbox_widget.lon1_input.setValue(min_e)
                self.bbox_page.bbox_widget.lat2_input.setValue(min_n)
                self.bbox_page.bbox_widget.lon2_input.setValue(max_e)
            else:
                self.bbox_page.bbox_widget.lat1_input.setValue(bbox.max_lat)
                self.bbox_page.bbox_widget.lon1_input.setValue(bbox.min_lon)
                self.bbox_page.bbox_widget.lat2_input.setValue(bbox.min_lat)
                self.bbox_page.bbox_widget.lon2_input.setValue(bbox.max_lon)

        for layer_name, card in self.layers_page.layer_cards.items():
            cfg = self.project.layers.get(layer_name)
            if cfg is None:
                continue
            card.config = cfg
            card.enable_checkbox.setChecked(cfg.enabled)
            if layer_name == "terrain":
                idx = {"3dep": 0, "srtm": 1, "auto": 2}.get(cfg.terrain_source, 2)
                card.source_combo.setCurrentIndex(idx)
            elif layer_name == "buildings":
                idx = {"osm": 0, "microsoft": 1, "merged": 2}.get(cfg.building_source, 2)
                card.source_combo.setCurrentIndex(idx)
                hidx = {"flat": 0, "estimate": 1, "mean": 2}.get(cfg.building_height_mode.value, 1)
                card.height_combo.setCurrentIndex(hidx)
            elif layer_name == "landuse":
                card.raster_check.setChecked(cfg.landuse_raster)
                card.vector_check.setChecked(cfg.landuse_vector)
            elif layer_name == "water":
                card.vector_check.setChecked(cfg.water_vector)
            elif layer_name == "reference":
                card.zoom_spin.setValue(cfg.reference_zoom if cfg.reference_zoom is not None else 0)

        self.output_page.name_input.setText(self.project.name)
        self.output_page.folder_input.setText(self.project.output_folder)
        self.output_page.resolution_spin.setValue(float(self.project.resolution_m))
        mode = str(self.project.timestamp_mode or "").lower().strip()
        self.output_page.timestamp_none_radio.setChecked(mode == TimestampMode.NONE.value)
        self.output_page.timestamp_prepend_radio.setChecked(mode == TimestampMode.PREPEND.value)
        self.output_page.timestamp_append_radio.setChecked(mode == TimestampMode.APPEND.value)
        utm_override = self.project.utm_zone_override if self.project.utm_zone_override is not None else 0
        self.output_page.utm_spin.setValue(int(utm_override))
        self.output_page._update_resolved_folder_preview()
