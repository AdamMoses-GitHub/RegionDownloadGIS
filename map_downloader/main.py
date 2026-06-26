"""Entry point for the Region3D Map Data Downloader GUI."""

import sys
from map_downloader.gui.app import create_app
from map_downloader.gui.wizard import MapDownloaderWizard


def main():
    """Launch the application."""
    app = create_app()
    wizard = MapDownloaderWizard()
    wizard.setCurrentId(0)
    wizard.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
