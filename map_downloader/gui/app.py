"""Main QApplication and wizard launcher."""

import sys
import warnings
from PySide6.QtWidgets import QApplication


def create_app() -> QApplication:
    """Create and configure QApplication."""
    warnings.filterwarnings(
        "ignore",
        message=r"In a future version of xarray the default value for join will change.*",
        category=FutureWarning,
        module=r"pygeoutils\.pygeoutils",
    )
    warnings.filterwarnings(
        "ignore",
        message=r"In a future version of xarray the default value for compat will change.*",
        category=FutureWarning,
        module=r"pygeoutils\.pygeoutils",
    )

    app = QApplication(sys.argv)
    app.setApplicationName("Region3D Map Data Downloader")
    app.setApplicationVersion("0.1.0")
    return app
