"""Main application window hosting the workflow wizard with native menus."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QFileDialog,
    QMainWindow,
    QMessageBox,
)

from map_downloader.gui.wizard import MapDownloaderWizard


class MainWindow(QMainWindow):
    """Native desktop shell around the Region3D wizard."""

    def __init__(self, parent=None):
        super().__init__(parent)

        self.wizard = MapDownloaderWizard(self)
        self.setCentralWidget(self.wizard)
        self.wizard.windowTitleChanged.connect(self.setWindowTitle)
        self.setWindowTitle(self.wizard.windowTitle())

        self.resize(980, 760)
        self.setMinimumSize(900, 680)

        self._build_menus()

    def _build_menus(self):
        menu = self.menuBar()

        file_menu = menu.addMenu("&File")

        new_action = QAction("&New Project", self)
        new_action.triggered.connect(self._new_project)
        file_menu.addAction(new_action)

        open_action = QAction("&Open Project...", self)
        open_action.triggered.connect(self._open_project)
        file_menu.addAction(open_action)

        save_action = QAction("&Save Project", self)
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self._save_project)
        file_menu.addAction(save_action)

        save_as_action = QAction("Save Project &As...", self)
        save_as_action.setShortcut("Ctrl+Shift+S")
        save_as_action.triggered.connect(self._save_project_as)
        file_menu.addAction(save_as_action)

        file_menu.addSeparator()

        open_output_action = QAction("Open &Output Folder", self)
        open_output_action.triggered.connect(self._open_output_folder)
        file_menu.addAction(open_output_action)

        open_exports_action = QAction("Open E&xport Folder", self)
        open_exports_action.triggered.connect(self._open_export_folder)
        file_menu.addAction(open_exports_action)

        file_menu.addSeparator()

        exit_action = QAction("E&xit", self)
        exit_action.setShortcut("Alt+F4")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        run_menu = menu.addMenu("&Run")

        run_action = QAction("Start Download + &Processing", self)
        run_action.setShortcut("F5")
        run_action.triggered.connect(self._start_run)
        run_menu.addAction(run_action)

        cancel_action = QAction("&Cancel Current Run", self)
        cancel_action.triggered.connect(self._cancel_run)
        run_menu.addAction(cancel_action)

        view_menu = menu.addMenu("&View")
        for idx, title in [
            (0, "Step 1: Define Area"),
            (1, "Step 2: Configure Layers"),
            (2, "Step 3: Output Settings"),
            (3, "Step 4: Download"),
            (4, "Step 5: Preview"),
            (5, "Step 6: Export"),
        ]:
            action = QAction(title, self)
            action.triggered.connect(lambda _checked=False, step=idx: self.wizard.setCurrentId(step))
            view_menu.addAction(action)

        help_menu = menu.addMenu("&Help")
        about_action = QAction("&About", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _new_project(self):
        self.wizard.reset_new_project()
        QMessageBox.information(self, "New Project", "Started a new project.")

    def _open_project(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Project",
            str(Path.cwd()),
            "Region3D Project (*.r3d.json);;JSON Files (*.json);;All Files (*)",
        )
        if not path:
            return

        try:
            self.wizard.load_project_from_file(path)
            QMessageBox.information(self, "Project Loaded", f"Loaded project:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Open Project Failed", f"Could not open project:\n{exc}")

    def _save_project(self):
        try:
            existing = self.wizard.current_project_file_path()
            if existing:
                saved = self.wizard.save_project_as(existing)
            else:
                saved = self.wizard.save_project_to_default_location()
            QMessageBox.information(self, "Project Saved", f"Saved project:\n{saved}")
        except Exception as exc:
            QMessageBox.critical(self, "Save Failed", f"Could not save project:\n{exc}")

    def _save_project_as(self):
        default_name = f"{self.wizard.project.sanitize_name(self.wizard.project.name)}.r3d.json"
        default_dir = str(self.wizard.project.resolve_output_root(force_refresh=False))
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Project As",
            str(Path(default_dir) / default_name),
            "Region3D Project (*.r3d.json);;JSON Files (*.json);;All Files (*)",
        )
        if not path:
            return

        try:
            saved = self.wizard.save_project_as(path)
            QMessageBox.information(self, "Project Saved", f"Saved project:\n{saved}")
        except Exception as exc:
            QMessageBox.critical(self, "Save Failed", f"Could not save project:\n{exc}")

    def _open_output_folder(self):
        output_root = self.wizard.project.resolve_output_root(force_refresh=False)
        self.wizard.export_page._open_folder_path(str(output_root), "output folder")

    def _open_export_folder(self):
        output_root = self.wizard.project.resolve_output_root(force_refresh=False)
        export_dir = output_root / "exports"
        self.wizard.export_page._open_folder_path(str(export_dir), "export folder")

    def _start_run(self):
        self.wizard.setCurrentId(3)
        self.wizard.download_page._run_pipeline()

    def _cancel_run(self):
        self.wizard.setCurrentId(3)
        self.wizard.download_page._cancel_wizard()

    def _show_about(self):
        QMessageBox.information(
            self,
            "About Region3D",
            "Region3D Map Data Downloader\n\n"
            "Desktop workflow for defining regions, downloading GIS layers, previewing, and exporting.",
        )
