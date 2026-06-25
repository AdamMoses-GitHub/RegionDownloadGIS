"""Page 6: Export Data."""

from pathlib import Path

from PySide6.QtWidgets import (
    QWizardPage, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox,
    QPushButton, QTextEdit, QGroupBox, QComboBox
)
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

from map_downloader.export import Exporter


class ExportPage(QWizardPage):
    """Step 6: Export processed data to GIS formats."""
    
    def __init__(self):
        super().__init__()
        self.setTitle("Step 6: Export Data")
        self.setSubTitle("Choose which layers and formats to export")
        
        layout = QVBoxLayout()
        layout.addWidget(QLabel("Select export formats per datatype (none, one, or many):"))

        # Terrain
        self.terrain_group = self._build_format_group(
            "Terrain",
            ["GeoTIFF"],
            default_checked={"GeoTIFF"},
        )
        layout.addWidget(self.terrain_group["widget"])

        # Buildings
        self.buildings_group = self._build_format_group(
            "Buildings",
            ["GeoJSON", "Shapefile", "GeoPackage", "KML", "KMZ"],
            default_checked={"GeoJSON"},
        )
        layout.addWidget(self.buildings_group["widget"])

        # Land use
        self.landuse_group = self._build_format_group(
            "Land Use",
            ["GeoTIFF", "GeoJSON", "Shapefile", "GeoPackage"],
            default_checked={"GeoTIFF", "GeoPackage"},
        )
        layout.addWidget(self.landuse_group["widget"])

        # Water
        self.water_group = self._build_format_group(
            "Water",
            ["GeoJSON", "Shapefile", "GeoPackage", "KML", "KMZ"],
            default_checked={"GeoJSON"},
        )
        layout.addWidget(self.water_group["widget"])

        # Big streets
        self.big_streets_group = self._build_format_group(
            "Big Streets",
            ["GeoJSON", "Shapefile", "GeoPackage", "KML", "KMZ"],
            default_checked={"GeoJSON"},
        )
        layout.addWidget(self.big_streets_group["widget"])

        # Small streets
        self.small_streets_group = self._build_format_group(
            "Small Streets",
            ["GeoJSON", "Shapefile", "GeoPackage", "KML", "KMZ"],
            default_checked={"GeoJSON"},
        )
        layout.addWidget(self.small_streets_group["widget"])

        # Reference
        self.reference_group = self._build_format_group(
            "Reference",
            ["GeoTIFF", "PNG", "JPG"],
            default_checked=set(),
        )
        layout.addWidget(self.reference_group["widget"])
        layout.addWidget(QLabel("Note: Reference PNG/JPG exports are plain images (no geospatial coordinates)."))

        # Bounding box
        self.bbox_group = self._build_format_group(
            "Bounding Box",
            ["GeoJSON", "Shapefile", "GeoPackage", "KML", "KMZ"],
            default_checked=set(),
        )
        layout.addWidget(self.bbox_group["widget"])

        # QGIS project
        self.qgis_group = self._build_format_group(
            "QGIS Project",
            ["QGS", "QGZ"],
            default_checked=set(),
        )
        layout.addWidget(self.qgis_group["widget"])
        layout.addWidget(QLabel("Note: QGS exports a QGIS project with selected/exported layers preloaded."))

        # Project README summary
        self.readme_group = self._build_format_group(
            "Project README",
            ["README.md"],
            default_checked={"README.md"},
        )
        layout.addWidget(self.readme_group["widget"])
        layout.addWidget(QLabel("README export summarizes project settings and generated files for this run."))

        # Export CRS selection
        crs_layout = QHBoxLayout()
        crs_layout.addWidget(QLabel("Export CRS:"))
        self.export_crs_combo = QComboBox()
        self.export_crs_combo.addItem("Keep source CRS", "source")
        self.export_crs_combo.addItem("EPSG:4326 (WGS84)", "epsg:4326")
        self.export_crs_combo.addItem("Project UTM zone", "project_utm")
        crs_layout.addWidget(self.export_crs_combo)
        crs_layout.addStretch()
        layout.addLayout(crs_layout)
        layout.addWidget(QLabel("Note: KML/KMZ always use EPSG:4326 by specification."))
        
        layout.addWidget(QLabel("\nExport Summary:"))
        self.summary_text = QTextEdit()
        self.summary_text.setReadOnly(True)
        self.summary_text.setMaximumHeight(150)
        layout.addWidget(self.summary_text)
        
        # Export button
        export_layout = QHBoxLayout()
        export_layout.addStretch()
        self.export_btn = QPushButton("Export Files")
        self.export_btn.clicked.connect(self._on_export)
        export_layout.addWidget(self.export_btn)
        self.open_export_folder_btn = QPushButton("Open Export Folder")
        self.open_export_folder_btn.setEnabled(False)
        self.open_export_folder_btn.clicked.connect(self._open_export_folder)
        export_layout.addWidget(self.open_export_folder_btn)
        self.open_project_folder_btn = QPushButton("Open Project Folder")
        self.open_project_folder_btn.setEnabled(False)
        self.open_project_folder_btn.clicked.connect(self._open_project_folder)
        export_layout.addWidget(self.open_project_folder_btn)
        layout.addLayout(export_layout)
        
        layout.addStretch()
        self.setLayout(layout)

    def _build_format_group(self, title: str, formats: list[str], default_checked: set[str]):
        """Create a datatype group with per-format checkboxes."""
        group = QGroupBox(title)
        row = QHBoxLayout()
        checks = {}
        for fmt in formats:
            cb = QCheckBox(fmt)
            cb.setChecked(fmt in default_checked)
            row.addWidget(cb)
            checks[fmt] = cb
        row.addStretch()
        group.setLayout(row)
        return {"widget": group, "checks": checks}

    def _selected_formats(self, group: dict) -> list[str]:
        checks = group["checks"]
        return [fmt for fmt, cb in checks.items() if cb.isChecked()]
    
    def _on_export(self):
        """Trigger export."""
        wizard = self.wizard()
        output_folder = ""
        self.open_export_folder_btn.setEnabled(False)
        self.open_export_folder_btn.setProperty("export_dir", "")
        self.open_project_folder_btn.setEnabled(False)
        self.open_project_folder_btn.setProperty("project_dir", "")
        if wizard and hasattr(wizard, "project"):
            output_folder = str(wizard.project.resolve_output_root(force_refresh=False))

        if not output_folder:
            self.summary_text.setText("Export failed: output folder is not configured.")
            return

        self.export_btn.setEnabled(False)
        try:
            exporter = Exporter(output_folder)
            bbox = wizard.project.get_bbox() if wizard and hasattr(wizard, "project") else None
            selected_export_crs_label = self.export_crs_combo.currentText()
            result = exporter.export(
                terrain_formats=self._selected_formats(self.terrain_group),
                buildings_formats=self._selected_formats(self.buildings_group),
                landuse_formats=self._selected_formats(self.landuse_group),
                water_formats=self._selected_formats(self.water_group),
                big_streets_formats=self._selected_formats(self.big_streets_group),
                small_streets_formats=self._selected_formats(self.small_streets_group),
                reference_formats=self._selected_formats(self.reference_group),
                bbox_formats=self._selected_formats(self.bbox_group),
                qgis_project_formats=self._selected_formats(self.qgis_group),
                readme_formats=self._selected_formats(self.readme_group),
                export_crs_mode=self.export_crs_combo.currentData(),
                bbox=bbox,
                project=(wizard.project if wizard and hasattr(wizard, "project") else None),
            )

            lines = [f"Export CRS: {selected_export_crs_label}", ""]
            if result.success:
                lines.append("Export completed.")
            else:
                lines.append("Export completed with warnings.")

            if result.files:
                lines.append("")
                lines.append("Generated files:")
                for path in result.files:
                    lines.append(f"- {path}")

            if result.messages:
                lines.append("")
                lines.append("Details:")
                lines.extend(result.messages)

            export_dir = Path(output_folder) / "exports"
            if result.files and export_dir.exists():
                self.open_export_folder_btn.setProperty("export_dir", str(export_dir))
                self.open_export_folder_btn.setEnabled(True)
                self.open_project_folder_btn.setProperty("project_dir", output_folder)
                self.open_project_folder_btn.setEnabled(True)

            self.summary_text.setText("\n".join(lines))
        except Exception as exc:
            self.summary_text.setText(f"Export failed: {exc}")
        finally:
            self.export_btn.setEnabled(True)

    def _open_export_folder(self):
        """Open export output folder in the system file explorer."""
        export_dir = self.open_export_folder_btn.property("export_dir")
        self._open_folder_path(export_dir, "export folder")

    def _open_project_folder(self):
        """Open project output root folder in the system file explorer."""
        project_dir = self.open_project_folder_btn.property("project_dir")
        self._open_folder_path(project_dir, "project folder")

    def _open_folder_path(self, folder_path: str | None, label: str):
        """Open a local folder path in the system file explorer."""
        if not folder_path:
            return

        opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder_path)))
        if not opened:
            self.summary_text.append(f"\nCould not open {label}: {folder_path}")
    
    def validatePage(self) -> bool:
        """Validate at least one layer selected."""
        selected = any([
            self._selected_formats(self.terrain_group),
            self._selected_formats(self.buildings_group),
            self._selected_formats(self.landuse_group),
            self._selected_formats(self.water_group),
            self._selected_formats(self.big_streets_group),
            self._selected_formats(self.small_streets_group),
            self._selected_formats(self.reference_group),
            self._selected_formats(self.bbox_group),
            self._selected_formats(self.qgis_group),
            self._selected_formats(self.readme_group),
        ])
        if not selected:
            self.setSubTitle("❌ Error: Select at least one export format")
            return False
        return True
    
    def nextId(self) -> int:
        """No more pages."""
        return -1
