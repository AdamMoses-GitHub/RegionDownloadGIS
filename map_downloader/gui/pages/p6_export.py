"""Page 6: Export Data."""

from PySide6.QtWidgets import (
    QWizardPage, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox,
    QPushButton, QTextEdit, QGroupBox
)

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
        if wizard and hasattr(wizard, "project"):
            output_folder = str(wizard.project.resolve_output_root(force_refresh=False))

        if not output_folder:
            self.summary_text.setText("Export failed: output folder is not configured.")
            return

        self.export_btn.setEnabled(False)
        try:
            exporter = Exporter(output_folder)
            bbox = wizard.project.get_bbox() if wizard and hasattr(wizard, "project") else None
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
                bbox=bbox,
            )

            lines = []
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

            self.summary_text.setText("\n".join(lines))
        except Exception as exc:
            self.summary_text.setText(f"Export failed: {exc}")
        finally:
            self.export_btn.setEnabled(True)
    
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
        ])
        if not selected:
            self.setSubTitle("❌ Error: Select at least one export format")
            return False
        return True
    
    def nextId(self) -> int:
        """No more pages."""
        return -1
