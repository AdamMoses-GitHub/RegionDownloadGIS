"""Main QWizard implementation."""

from PySide6.QtWidgets import QWizard, QVBoxLayout, QHBoxLayout, QPushButton, QLabel
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
        self.setButtonText(QWizard.WizardButton.BackButton, "← Back")
        self.setButtonText(QWizard.WizardButton.NextButton, "Next →")
        self.setButtonText(QWizard.WizardButton.FinishButton, "Finish")
        self.setButtonText(QWizard.WizardButton.CancelButton, "Cancel")
        
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
        self._sync_project_from_page(self.currentId())
        
        # Save project
        try:
            output_root = self.project.resolve_output_root(force_refresh=False)
            project_stem = self.project.sanitize_name(self.project.name)
            project_file = output_root / f"{project_stem}.r3d.json"
            project_file.parent.mkdir(parents=True, exist_ok=True)
            self.project.save(str(project_file))
        except Exception as e:
            print(f"Error saving project: {e}")
        
        self.accept()
