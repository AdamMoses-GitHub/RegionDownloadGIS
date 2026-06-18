"""Per-layer configuration card widget."""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QCheckBox, QGroupBox, 
    QLabel, QComboBox, QSpinBox
)
from map_downloader.core.project import LayerConfig, HeightMode


class LayerCard(QWidget):
    """Configuration card for a single data layer."""
    
    def __init__(self, layer_name: str, config: LayerConfig, parent=None):
        super().__init__(parent)
        self.layer_name = layer_name
        self.config = config
        self._init_ui()
    
    def _init_ui(self):
        """Build UI."""
        layout = QVBoxLayout()

        display_name = self.layer_name.title().replace("_", " ")
        if self.layer_name == "big_streets":
            display_name = "Big Streets"
        elif self.layer_name == "small_streets":
            display_name = "Small Streets"
        
        # Enable checkbox
        self.enable_checkbox = QCheckBox(f"Enable {display_name}")
        self.enable_checkbox.setChecked(self.config.enabled)
        layout.addWidget(self.enable_checkbox)
        
        # Layer-specific options
        self.options_group = QGroupBox("Options")
        options_layout = QVBoxLayout()
        
        if self.layer_name == "terrain":
            source_layout = QHBoxLayout()
            source_layout.addWidget(QLabel("Source:"))
            self.source_combo = QComboBox()
            self.source_combo.addItems(["3DEP (CONUS)", "SRTM (Global)", "Auto"])
            idx = {"3dep": 0, "srtm": 1, "auto": 2}.get(self.config.terrain_source, 0)
            self.source_combo.setCurrentIndex(idx)
            source_layout.addWidget(self.source_combo)
            source_layout.addStretch()
            options_layout.addLayout(source_layout)
        
        elif self.layer_name == "buildings":
            source_layout = QHBoxLayout()
            source_layout.addWidget(QLabel("Source:"))
            self.source_combo = QComboBox()
            self.source_combo.addItems(["OSM", "Microsoft", "Merged (Smart)"])
            idx = {"osm": 0, "microsoft": 1, "merged": 2}.get(self.config.building_source, 2)
            self.source_combo.setCurrentIndex(idx)
            source_layout.addWidget(self.source_combo)
            source_layout.addStretch()
            options_layout.addLayout(source_layout)
            
            height_layout = QHBoxLayout()
            height_layout.addWidget(QLabel("Height Mode:"))
            self.height_combo = QComboBox()
            self.height_combo.addItems(["Flat", "Rough Estimate", "Dataset Mean"])
            idx = {HeightMode.FLAT: 0, HeightMode.ESTIMATE: 1, HeightMode.MEAN: 2}.get(
                self.config.building_height_mode, 1)
            self.height_combo.setCurrentIndex(idx)
            height_layout.addWidget(self.height_combo)
            height_layout.addStretch()
            options_layout.addLayout(height_layout)
        
        elif self.layer_name == "landuse":
            raster_layout = QHBoxLayout()
            self.raster_check = QCheckBox("NLCD Raster (30m)")
            self.raster_check.setChecked(self.config.landuse_raster)
            raster_layout.addWidget(self.raster_check)
            raster_layout.addStretch()
            options_layout.addLayout(raster_layout)
            
            vector_layout = QHBoxLayout()
            self.vector_check = QCheckBox("OSM Vector Polygons")
            self.vector_check.setChecked(self.config.landuse_vector)
            vector_layout.addWidget(self.vector_check)
            vector_layout.addStretch()
            options_layout.addLayout(vector_layout)

        elif self.layer_name == "water":
            vector_layout = QHBoxLayout()
            self.vector_check = QCheckBox("OSM Water Polygons")
            self.vector_check.setChecked(self.config.water_vector)
            vector_layout.addWidget(self.vector_check)
            vector_layout.addStretch()
            options_layout.addLayout(vector_layout)

        elif self.layer_name == "big_streets":
            info_layout = QHBoxLayout()
            info_layout.addWidget(QLabel("OSM highways + major arterials"))
            info_layout.addStretch()
            options_layout.addLayout(info_layout)

        elif self.layer_name == "small_streets":
            info_layout = QHBoxLayout()
            info_layout.addWidget(QLabel("OSM tertiary + local/residential roads"))
            info_layout.addStretch()
            options_layout.addLayout(info_layout)
        
        elif self.layer_name == "reference":
            zoom_layout = QHBoxLayout()
            zoom_layout.addWidget(QLabel("Zoom Level:"))
            self.zoom_spin = QSpinBox()
            self.zoom_spin.setRange(0, 20)
            self.zoom_spin.setValue(self.config.reference_zoom or 0)
            self.zoom_spin.setPrefix("Auto (0) or ")
            zoom_layout.addWidget(self.zoom_spin)
            zoom_layout.addStretch()
            options_layout.addLayout(zoom_layout)
        
        self.options_group.setLayout(options_layout)
        layout.addWidget(self.options_group)
        layout.addStretch()
        self.setLayout(layout)
    
    def get_config(self) -> LayerConfig:
        """Get current config from UI."""
        self.config.enabled = self.enable_checkbox.isChecked()
        
        if self.layer_name == "terrain":
            idx_to_source = {0: "3dep", 1: "srtm", 2: "auto"}
            self.config.terrain_source = idx_to_source.get(self.source_combo.currentIndex(), "auto")
        
        elif self.layer_name == "buildings":
            idx_to_source = {0: "osm", 1: "microsoft", 2: "merged"}
            self.config.building_source = idx_to_source.get(self.source_combo.currentIndex(), "merged")
            idx_to_height = {0: HeightMode.FLAT, 1: HeightMode.ESTIMATE, 2: HeightMode.MEAN}
            self.config.building_height_mode = idx_to_height.get(self.height_combo.currentIndex(), HeightMode.ESTIMATE)
        
        elif self.layer_name == "landuse":
            self.config.landuse_raster = self.raster_check.isChecked()
            self.config.landuse_vector = self.vector_check.isChecked()

        elif self.layer_name == "water":
            self.config.water_vector = self.vector_check.isChecked()
        
        elif self.layer_name == "reference":
            zoom = self.zoom_spin.value()
            self.config.reference_zoom = zoom if zoom > 0 else None
        
        return self.config
