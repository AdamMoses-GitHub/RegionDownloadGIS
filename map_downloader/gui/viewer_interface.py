"""3D viewer abstraction layer used by the preview page."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class ViewerInterface(ABC):
    """Abstract interface for pluggable 3D viewers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable viewer name."""

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if this viewer backend can render 3D content."""

    @abstractmethod
    def create_widget(
        self,
        parent: Optional[QWidget] = None,
        data_paths: Optional[Iterable[Path]] = None,
    ) -> QWidget:
        """Return a QWidget that hosts the viewer UI."""

    @abstractmethod
    def unavailable_reason(self) -> str:
        """Return a message describing why the backend is unavailable."""


class NullViewer(ViewerInterface):
    """Fallback viewer backend used until a real 3D renderer is integrated."""

    @property
    def name(self) -> str:
        return "NullViewer"

    def is_available(self) -> bool:
        return False

    def unavailable_reason(self) -> str:
        return (
            "3D viewer is not implemented yet. "
            "Export data and inspect it in QGIS/ArcGIS for now."
        )

    def create_widget(
        self,
        parent: Optional[QWidget] = None,
        data_paths: Optional[Iterable[Path]] = None,
    ) -> QWidget:
        widget = QWidget(parent)
        layout = QVBoxLayout(widget)

        label = QLabel(
            "3D View\n\n"
            "Coming in a future release.\n\n"
            f"{self.unavailable_reason()}"
        )
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("color: gray; font-style: italic;")
        layout.addWidget(label)

        return widget
