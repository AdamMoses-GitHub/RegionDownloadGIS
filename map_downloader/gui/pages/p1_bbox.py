"""Page 1: Define Bounding Box."""

from PySide6.QtWidgets import QWizardPage, QVBoxLayout, QLabel
from map_downloader.gui.widgets.bbox_widget import BboxInputWidget


class BboxPage(QWizardPage):
    """Step 1: Define bounding box (corners or centroid+size, lat/long or UTM)."""
    
    def __init__(self):
        super().__init__()
        self.setTitle("Step 1: Define Area")
        self.setSubTitle("Specify your bounding box via corners or centroid + size")
        
        layout = QVBoxLayout()
        
        # Instructions
        instructions = QLabel(
            "Enter your area of interest. You can define it by:\n"
            "• Four corners (NW and SE)\n"
            "• Centroid point + width and height\n"
            "\n"
            "Coordinates can be in Lat/Long (WGS84) or UTM.\n"
            "\n"
            "UTM mode preserves exact UTM min/max rectangle edges through processing and export."
        )
        layout.addWidget(instructions)
        
        # Bbox input widget
        self.bbox_widget = BboxInputWidget()
        layout.addWidget(self.bbox_widget)
        
        self.setLayout(layout)
    
    def validatePage(self) -> bool:
        """Validate before moving to next page."""
        try:
            bbox = self.bbox_widget.get_bbox()
            if bbox.max_dimension_km() > 500:
                self.setSubTitle("⚠ Warning: area > 500km side (may download large dataset)")
            return True
        except Exception as e:
            self.setSubTitle(f"❌ Error: {str(e)}")
            return False
    
    def nextId(self) -> int:
        """Next page ID."""
        return 1
