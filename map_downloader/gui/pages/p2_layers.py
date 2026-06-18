"""Page 2: Configure Layers."""

from PySide6.QtWidgets import QWizardPage, QVBoxLayout, QLabel, QScrollArea
from map_downloader.gui.widgets.layer_card import LayerCard
from map_downloader.core.project import LayerConfig


class LayersPage(QWizardPage):
    """Step 2: Configure data layers (terrain, buildings, land use, water, streets, reference map)."""
    
    def __init__(self):
        super().__init__()
        self.setTitle("Step 2: Configure Layers")
        self.setSubTitle("Select which data layers to download and how to handle them")
        
        layout = QVBoxLayout()
        
        # Instructions
        instructions = QLabel(
            "Choose which data layers you want to download:\n"
            "• Terrain: Digital elevation model\n"
            "• Buildings: Footprints with optional height data\n"
            "• Land Use: Land cover classification and OSM polygons\n"
            "• Water: OSM water polygons (lakes, rivers, reservoirs)\n"
            "• Big Streets: Highways and major streets\n"
            "• Small Streets: Secondary/local/residential streets\n"
            "• Reference: OSM basemap tiles (visual reference only)"
        )
        layout.addWidget(instructions)
        
        # Layer cards in scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_layout = QVBoxLayout()
        
        self.layer_cards = {}
        for layer_name in ["terrain", "buildings", "landuse", "water", "big_streets", "small_streets", "reference"]:
            card = LayerCard(layer_name, LayerConfig())
            self.layer_cards[layer_name] = card
            scroll_layout.addWidget(card)
        
        scroll_layout.addStretch()
        scroll_container = type('obj', (object,), {'setLayout': lambda x: None})()
        from PySide6.QtWidgets import QWidget
        scroll_container = QWidget()
        scroll_container.setLayout(scroll_layout)
        scroll.setWidget(scroll_container)
        layout.addWidget(scroll)
        
        self.setLayout(layout)
    
    def validatePage(self) -> bool:
        """Validate: at least one layer enabled."""
        enabled = any(card.enable_checkbox.isChecked() for card in self.layer_cards.values())
        if not enabled:
            self.setSubTitle("❌ Error: At least one layer must be enabled")
            return False
        return True
    
    def nextId(self) -> int:
        """Next page ID."""
        return 2
