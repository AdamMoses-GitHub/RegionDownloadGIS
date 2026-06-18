"""Page 3: Output Settings."""

from PySide6.QtWidgets import (
    QWizardPage, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, 
    QPushButton, QDoubleSpinBox, QSpinBox, QFileDialog, QRadioButton, QButtonGroup
)
from pathlib import Path
from map_downloader.core.project import Project, TimestampMode


class OutputPage(QWizardPage):
    """Step 3: Configure output settings (folder, resolution, CRS, project name)."""
    
    def __init__(self):
        super().__init__()
        self._name_user_edited = False
        self.setTitle("Step 3: Output Settings")
        self.setSubTitle("Configure where to save data and at what resolution")
        
        layout = QVBoxLayout()
        
        # Project name
        name_layout = QHBoxLayout()
        name_layout.addWidget(QLabel("Project Name:"))
        self.name_input = QLineEdit("My Region")
        name_layout.addWidget(self.name_input)
        layout.addLayout(name_layout)

        timestamp_layout = QVBoxLayout()
        timestamp_layout.addWidget(QLabel("Timestamp in Project Folder Name:"))
        mode_row = QHBoxLayout()
        self.timestamp_mode_group = QButtonGroup(self)
        self.timestamp_none_radio = QRadioButton("No timestamp")
        self.timestamp_prepend_radio = QRadioButton("Prepend timestamp")
        self.timestamp_append_radio = QRadioButton("Append timestamp")
        self.timestamp_mode_group.addButton(self.timestamp_none_radio)
        self.timestamp_mode_group.addButton(self.timestamp_prepend_radio)
        self.timestamp_mode_group.addButton(self.timestamp_append_radio)
        self.timestamp_append_radio.setChecked(True)
        mode_row.addWidget(self.timestamp_none_radio)
        mode_row.addWidget(self.timestamp_prepend_radio)
        mode_row.addWidget(self.timestamp_append_radio)
        mode_row.addStretch()
        timestamp_layout.addLayout(mode_row)
        layout.addLayout(timestamp_layout)
        
        # Output folder
        default_output_dir = Path.cwd() / "region3d_output"
        folder_layout = QHBoxLayout()
        folder_layout.addWidget(QLabel("Output Folder:"))
        self.folder_input = QLineEdit(str(default_output_dir))
        folder_layout.addWidget(self.folder_input)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_folder)
        folder_layout.addWidget(browse_btn)
        layout.addLayout(folder_layout)

        resolved_layout = QHBoxLayout()
        resolved_layout.addWidget(QLabel("Effective Project Folder:"))
        self.resolved_folder_label = QLabel("")
        self.resolved_folder_label.setStyleSheet("color: #1D4ED8; font-style: italic;")
        resolved_layout.addWidget(self.resolved_folder_label)
        resolved_layout.addStretch()
        layout.addLayout(resolved_layout)
        
        # Resolution
        res_layout = QHBoxLayout()
        res_layout.addWidget(QLabel("Resolution (meters):"))
        self.resolution_spin = QDoubleSpinBox()
        self.resolution_spin.setRange(1.0, 100.0)
        self.resolution_spin.setValue(5.0)
        self.resolution_spin.setSingleStep(1.0)
        res_layout.addWidget(self.resolution_spin)
        res_layout.addStretch()
        layout.addLayout(res_layout)
        
        # CRS display
        crs_layout = QHBoxLayout()
        crs_layout.addWidget(QLabel("Output CRS:"))
        self.crs_label = QLabel("UTM (auto-detected)")
        self.crs_label.setStyleSheet("font-weight: bold;")
        crs_layout.addWidget(self.crs_label)
        crs_layout.addStretch()
        layout.addLayout(crs_layout)
        
        # UTM zone override
        utm_layout = QHBoxLayout()
        utm_layout.addWidget(QLabel("UTM Zone Override:"))
        self.utm_spin = QSpinBox()
        self.utm_spin.setRange(0, 60)
        self.utm_spin.setValue(0)
        self.utm_spin.setPrefix("Auto (0) or ")
        utm_layout.addWidget(self.utm_spin)
        utm_layout.addStretch()
        layout.addLayout(utm_layout)
        
        # Estimated output size
        self.size_label = QLabel("Estimated output size: (pending bbox)")
        self.size_label.setStyleSheet("color: blue; font-style: italic;")
        layout.addWidget(self.size_label)
        
        layout.addStretch()
        self.setLayout(layout)

        self.name_input.textChanged.connect(self._update_resolved_folder_preview)
        self.name_input.textEdited.connect(self._on_name_edited)
        self.folder_input.textChanged.connect(self._update_resolved_folder_preview)
        self.timestamp_none_radio.toggled.connect(self._update_resolved_folder_preview)
        self.timestamp_prepend_radio.toggled.connect(self._update_resolved_folder_preview)
        self.timestamp_append_radio.toggled.connect(self._update_resolved_folder_preview)
        self._update_resolved_folder_preview()

    def initializePage(self):
        """Apply smart defaults when entering output settings."""
        preset_name = self._selected_bbox_preset_name()
        if preset_name:
            current = self.name_input.text().strip()
            if (not self._name_user_edited) and (not current or current == "My Region"):
                self.name_input.setText(preset_name)

        # Default for new runs unless user already changed it explicitly.
        self.timestamp_append_radio.setChecked(True)
        self._update_resolved_folder_preview()

    def selected_timestamp_mode(self) -> str:
        if self.timestamp_none_radio.isChecked():
            return TimestampMode.NONE.value
        if self.timestamp_prepend_radio.isChecked():
            return TimestampMode.PREPEND.value
        return TimestampMode.APPEND.value

    def _on_name_edited(self, _text: str):
        self._name_user_edited = True

    def _selected_bbox_preset_name(self) -> str | None:
        wizard = self.wizard()
        if wizard is None or not hasattr(wizard, "bbox_page"):
            return None
        bbox_page = wizard.bbox_page
        if bbox_page is None or not hasattr(bbox_page, "bbox_widget"):
            return None
        getter = getattr(bbox_page.bbox_widget, "selected_preset_name", None)
        if callable(getter):
            return getter()
        return None

    def _update_resolved_folder_preview(self):
        """Show where the project-specific run artifacts will be written."""
        project = Project(
            name=self.name_input.text(),
            output_folder=self.folder_input.text(),
            timestamp_mode=self.selected_timestamp_mode(),
            append_timestamp_to_name=(self.selected_timestamp_mode() != TimestampMode.NONE.value),
        )
        self.resolved_folder_label.setText(str(project.resolve_output_root(force_refresh=True)))
    
    def _browse_folder(self):
        """Open folder browser dialog."""
        folder = QFileDialog.getExistingDirectory(self, "Select Output Folder")
        if folder:
            self.folder_input.setText(folder)
    
    def validatePage(self) -> bool:
        """Validate settings."""
        if not self.folder_input.text():
            self.setSubTitle("❌ Error: Output folder required")
            return False
        if self.resolution_spin.value() <= 0:
            self.setSubTitle("❌ Error: Resolution must be > 0")
            return False
        return True
    
    def nextId(self) -> int:
        """Next page ID."""
        return 3
