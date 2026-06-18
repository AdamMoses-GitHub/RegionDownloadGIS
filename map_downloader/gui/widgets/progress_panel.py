"""Progress display widget for downloads."""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar
)


class ProgressPanel(QWidget):
    """Per-layer progress display."""
    
    def __init__(self, layer_name: str, parent=None):
        super().__init__(parent)
        self.layer_name = layer_name
        self._init_ui()
    
    def _init_ui(self):
        """Build UI."""
        layout = QHBoxLayout()
        
        # Layer label
        self.label = QLabel(f"{self.layer_name.title()}:")
        self.label.setMinimumWidth(100)
        layout.addWidget(self.label)
        
        # Progress bar
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)
        
        # Status text
        self.status_label = QLabel("Ready")
        self.status_label.setMinimumWidth(100)
        layout.addWidget(self.status_label)
        
        # Cache badge
        self.cache_label = QLabel("")
        self.cache_label.setStyleSheet("color: green; font-weight: bold;")
        layout.addWidget(self.cache_label)
        
        self.setLayout(layout)
    
    def set_progress(self, percent: int):
        """Update progress bar (0-100)."""
        self.progress.setValue(percent)
    
    def set_status(self, status: str):
        """Update status text."""
        self.status_label.setText(status)
    
    def set_cache_hit(self, is_hit: bool):
        """Show cache hit badge."""
        self.cache_label.setText("(cached)" if is_hit else "")
    
    def reset(self):
        """Reset to initial state."""
        self.progress.setValue(0)
        self.status_label.setText("Ready")
        self.cache_label.setText("")
