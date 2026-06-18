"""Reusable bounding box input widget."""

import math

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QRadioButton, QButtonGroup, QGroupBox, QDoubleSpinBox, QSpinBox, QComboBox, QCheckBox
)
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QPainter, QPen, QBrush, QColor, QPolygonF
from pyproj import CRS, Transformer
from map_downloader.core.bbox import BoundingBox


# Approximate city extents in WGS84: (min_lat, min_lon, max_lat, max_lon)
BASE_CITY_PRESETS = {
    "New York City": (40.30, -74.50, 41.10, -73.50),
    "Los Angeles": (33.60, -118.95, 34.45, -117.65),
    "Chicago": (41.30, -88.60, 42.50, -87.20),
    "Dallas": (32.35, -97.70, 33.25, -96.35),
    "Houston": (29.25, -95.95, 30.25, -94.85),
    "Washington, DC": (38.55, -77.65, 39.25, -76.70),
    "Miami": (25.30, -80.90, 26.30, -80.00),
    "Philadelphia": (39.55, -75.85, 40.40, -74.75),
    "Atlanta": (33.20, -84.90, 34.40, -83.80),
    "Phoenix": (33.00, -112.60, 34.10, -111.30),
    "Boston": (41.95, -71.80, 42.95, -70.60),
    "Riverside": (33.45, -117.80, 34.35, -116.20),
    "San Francisco": (37.10, -123.10, 38.55, -121.45),
    "Detroit": (42.00, -83.75, 42.95, -82.50),
    "Seattle": (47.00, -122.75, 47.90, -121.40),
    "Minneapolis": (44.55, -93.85, 45.45, -92.75),
    "San Diego": (32.50, -117.60, 33.50, -116.50),
    "Tampa": (27.55, -82.95, 28.45, -82.10),
    "Denver": (39.35, -105.40, 40.20, -104.20),
    "Baltimore": (39.00, -77.15, 39.75, -76.20),
}


def _build_city_presets() -> dict:
    """Expand city presets with a compact 5x5 km variant for each city."""
    presets = {}
    half_km = 2.5  # Approximately 5x5 km city-center window.

    for city_name, bounds in BASE_CITY_PRESETS.items():
        min_lat, min_lon, max_lat, max_lon = bounds
        presets[f"{city_name} (Full)"] = bounds

        center_lat = (min_lat + max_lat) / 2.0
        center_lon = (min_lon + max_lon) / 2.0

        # Convert kilometers to degrees at this latitude.
        lat_deg_per_km = 1.0 / 111.32
        lon_deg_per_km = 1.0 / (111.32 * max(0.2, math.cos(math.radians(center_lat))))

        lat_half_deg = half_km * lat_deg_per_km
        lon_half_deg = half_km * lon_deg_per_km

        presets[f"{city_name} (5x5km)"] = (
            center_lat - lat_half_deg,
            center_lon - lon_half_deg,
            center_lat + lat_half_deg,
            center_lon + lon_half_deg,
        )

    return presets


CITY_PRESETS = _build_city_presets()

# Backward-compatible alias for tests/imports that still use old name.
METRO_PRESETS = CITY_PRESETS
BASE_METRO_PRESETS = BASE_CITY_PRESETS


class ConusPreviewWidget(QWidget):
    """Small CONUS overview with current bbox overlay."""

    # Lower-48 viewport bounds in lon/lat.
    CONUS_MIN_LON = -125.0
    CONUS_MAX_LON = -66.0
    CONUS_MIN_LAT = 24.0
    CONUS_MAX_LAT = 50.0
    MIN_BOX_PIXELS = 8.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bbox = None
        self.setMinimumSize(280, 170)
        self.setMaximumHeight(220)
        self.setToolTip("CONUS overview of current bounding box")

    def set_bbox(self, bbox: BoundingBox | None):
        self._bbox = bbox
        self.update()

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        frame = self.rect().adjusted(8, 8, -8, -8)
        map_rect = self._fit_aspect_rect(QRectF(frame))

        # Background water tone.
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor("#dbeafe")))
        painter.drawRoundedRect(map_rect, 6, 6)

        # Approximate CONUS silhouette polygon in lon/lat.
        conus_outline = [
            (-124.7, 48.6), (-124.2, 43.0), (-123.5, 40.0), (-122.5, 38.0),
            (-121.0, 36.0), (-119.8, 34.8), (-117.2, 32.6), (-114.7, 32.3),
            (-111.0, 31.5), (-107.0, 31.4), (-103.0, 29.8), (-98.5, 27.8),
            (-96.0, 28.7), (-92.0, 29.2), (-88.0, 30.3), (-85.0, 29.8),
            (-83.0, 28.2), (-81.6, 25.2), (-80.0, 26.0), (-80.4, 29.5),
            (-81.0, 31.0), (-80.8, 32.8), (-79.0, 33.8), (-77.5, 35.4),
            (-76.0, 37.5), (-75.2, 39.6), (-74.1, 40.7), (-73.0, 41.2),
            (-71.5, 41.6), (-70.2, 42.6), (-69.5, 44.2), (-70.8, 45.2),
            (-73.3, 45.3), (-77.2, 43.8), (-79.0, 43.5), (-82.5, 45.0),
            (-87.5, 47.0), (-94.0, 49.0), (-105.0, 49.0), (-114.5, 49.0),
            (-124.7, 48.6),
        ]
        poly = QPolygonF([self._to_point(lon, lat, map_rect) for lon, lat in conus_outline])
        painter.setBrush(QBrush(QColor("#c7e9c0")))
        painter.setPen(QPen(QColor("#4b5563"), 1.0))
        painter.drawPolygon(poly)

        # State-ish graticule for orientation.
        painter.setPen(QPen(QColor("#9ca3af"), 0.7, Qt.DotLine))
        for lon in (-120, -110, -100, -90, -80, -70):
            p1 = self._to_point(lon, self.CONUS_MIN_LAT, map_rect)
            p2 = self._to_point(lon, self.CONUS_MAX_LAT, map_rect)
            painter.drawLine(p1, p2)
        for lat in (25, 30, 35, 40, 45, 50):
            p1 = self._to_point(self.CONUS_MIN_LON, lat, map_rect)
            p2 = self._to_point(self.CONUS_MAX_LON, lat, map_rect)
            painter.drawLine(p1, p2)

        # Draw bbox overlay if present.
        if self._bbox is not None:
            overlay = self._bbox_rect(self._bbox, map_rect)
            marker = self._bbox_marker_point(self._bbox, map_rect)
            if overlay is not None and overlay.width() >= self.MIN_BOX_PIXELS and overlay.height() >= self.MIN_BOX_PIXELS:
                painter.setPen(QPen(QColor("#dc2626"), 2.0))
                painter.setBrush(QBrush(QColor(220, 38, 38, 40)))
                painter.drawRect(overlay)

            if marker is not None:
                painter.setPen(QPen(QColor("#b91c1c"), 2.0))
                painter.drawLine(QPointF(marker.x() - 5, marker.y() - 5), QPointF(marker.x() + 5, marker.y() + 5))
                painter.drawLine(QPointF(marker.x() - 5, marker.y() + 5), QPointF(marker.x() + 5, marker.y() - 5))

        painter.setPen(QPen(QColor("#6b7280"), 1.0))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(map_rect, 6, 6)

    def _to_point(self, lon: float, lat: float, rect: QRectF) -> QPointF:
        lon_span = self.CONUS_MAX_LON - self.CONUS_MIN_LON
        lat_span = self.CONUS_MAX_LAT - self.CONUS_MIN_LAT
        x = rect.left() + ((lon - self.CONUS_MIN_LON) / lon_span) * rect.width()
        y = rect.bottom() - ((lat - self.CONUS_MIN_LAT) / lat_span) * rect.height()
        return QPointF(x, y)

    def _fit_aspect_rect(self, frame: QRectF) -> QRectF:
        """Fit a rect preserving CONUS lon/lat aspect ratio."""
        if frame.width() <= 0 or frame.height() <= 0:
            return frame

        conus_aspect = (self.CONUS_MAX_LON - self.CONUS_MIN_LON) / (self.CONUS_MAX_LAT - self.CONUS_MIN_LAT)
        frame_aspect = frame.width() / frame.height()

        if frame_aspect > conus_aspect:
            h = frame.height()
            w = h * conus_aspect
        else:
            w = frame.width()
            h = w / conus_aspect

        x = frame.left() + (frame.width() - w) / 2.0
        y = frame.top() + (frame.height() - h) / 2.0
        return QRectF(x, y, w, h)

    def _bbox_rect(self, bbox: BoundingBox, rect: QRectF) -> QRectF | None:
        min_lon = max(self.CONUS_MIN_LON, bbox.min_lon)
        max_lon = min(self.CONUS_MAX_LON, bbox.max_lon)
        min_lat = max(self.CONUS_MIN_LAT, bbox.min_lat)
        max_lat = min(self.CONUS_MAX_LAT, bbox.max_lat)

        if max_lon <= min_lon or max_lat <= min_lat:
            return None

        tl = self._to_point(min_lon, max_lat, rect)
        br = self._to_point(max_lon, min_lat, rect)
        return QRectF(tl, br).normalized()

    def _bbox_marker_point(self, bbox: BoundingBox, rect: QRectF) -> QPointF | None:
        """Return center marker point for bbox, clipped to CONUS extent."""
        min_lon = max(self.CONUS_MIN_LON, bbox.min_lon)
        max_lon = min(self.CONUS_MAX_LON, bbox.max_lon)
        min_lat = max(self.CONUS_MIN_LAT, bbox.min_lat)
        max_lat = min(self.CONUS_MAX_LAT, bbox.max_lat)

        if max_lon <= min_lon or max_lat <= min_lat:
            return None

        center_lon = (min_lon + max_lon) / 2.0
        center_lat = (min_lat + max_lat) / 2.0
        return self._to_point(center_lon, center_lat, rect)


class BboxInputWidget(QWidget):
    """
    Widget for entering bounding box via corners or centroid+size.
    Supports lat/long or UTM input.
    """
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.bbox = None
        self._updating_ui = False
        self._last_crs = "latlong"
        self._init_ui()
    
    def _init_ui(self):
        """Build UI."""
        layout = QVBoxLayout()

        # Optional city presets
        preset_layout = QHBoxLayout()
        preset_layout.addWidget(QLabel("Preset City (CONUS):"))
        self.city_preset_combo = QComboBox()
        self.city_preset_combo.addItem("Custom (manual entry)")
        for city_name in CITY_PRESETS:
            self.city_preset_combo.addItem(city_name)
        preset_layout.addWidget(self.city_preset_combo)
        preset_layout.addStretch()
        layout.addLayout(preset_layout)
        
        # CRS selector
        crs_layout = QHBoxLayout()
        crs_label = QLabel("Coordinate System:")
        self.crs_group = QButtonGroup()
        self.crs_latlong = QRadioButton("Lat/Long (WGS84)")
        self.crs_utm = QRadioButton("UTM")
        self.crs_latlong.setChecked(True)
        self.crs_group.addButton(self.crs_latlong)
        self.crs_group.addButton(self.crs_utm)
        crs_layout.addWidget(crs_label)
        crs_layout.addWidget(self.crs_latlong)
        crs_layout.addWidget(self.crs_utm)
        crs_layout.addStretch()
        layout.addLayout(crs_layout)
        
        # Input mode selector
        mode_layout = QHBoxLayout()
        mode_label = QLabel("Input Mode:")
        self.mode_group = QButtonGroup()
        self.mode_corners = QRadioButton("Four Corners")
        self.mode_centroid = QRadioButton("Centroid + Size")
        self.mode_corners.setChecked(True)
        self.mode_group.addButton(self.mode_corners)
        self.mode_group.addButton(self.mode_centroid)
        mode_layout.addWidget(mode_label)
        mode_layout.addWidget(self.mode_corners)
        mode_layout.addWidget(self.mode_centroid)
        mode_layout.addStretch()
        layout.addLayout(mode_layout)
        
        # Input fields (corners mode)
        self.corners_group = QGroupBox("Corners (Lat/Long or Easting/Northing)")
        corners_layout = QVBoxLayout()
        
        lat1_layout = QHBoxLayout()
        lat1_layout.addWidget(QLabel("Corner 1 Lat/North:"))
        self.lat1_input = QDoubleSpinBox()
        self.lat1_input.setRange(-90, 90)
        lat1_layout.addWidget(self.lat1_input)
        corners_layout.addLayout(lat1_layout)
        
        lon1_layout = QHBoxLayout()
        lon1_layout.addWidget(QLabel("Corner 1 Lon/East:"))
        self.lon1_input = QDoubleSpinBox()
        self.lon1_input.setRange(-180, 180)
        lon1_layout.addWidget(self.lon1_input)
        corners_layout.addLayout(lon1_layout)
        
        lat2_layout = QHBoxLayout()
        lat2_layout.addWidget(QLabel("Corner 2 Lat/North:"))
        self.lat2_input = QDoubleSpinBox()
        self.lat2_input.setRange(-90, 90)
        lat2_layout.addWidget(self.lat2_input)
        corners_layout.addLayout(lat2_layout)
        
        lon2_layout = QHBoxLayout()
        lon2_layout.addWidget(QLabel("Corner 2 Lon/East:"))
        self.lon2_input = QDoubleSpinBox()
        self.lon2_input.setRange(-180, 180)
        lon2_layout.addWidget(self.lon2_input)
        corners_layout.addLayout(lon2_layout)
        
        self.corners_group.setLayout(corners_layout)
        layout.addWidget(self.corners_group)
        
        # Input fields (centroid mode)
        self.centroid_group = QGroupBox("Centroid + Size")
        centroid_layout = QVBoxLayout()
        
        clat_layout = QHBoxLayout()
        clat_layout.addWidget(QLabel("Centroid Lat/North:"))
        self.clat_input = QDoubleSpinBox()
        self.clat_input.setRange(-90, 90)
        clat_layout.addWidget(self.clat_input)
        centroid_layout.addLayout(clat_layout)
        
        clon_layout = QHBoxLayout()
        clon_layout.addWidget(QLabel("Centroid Lon/East:"))
        self.clon_input = QDoubleSpinBox()
        self.clon_input.setRange(-180, 180)
        clon_layout.addWidget(self.clon_input)
        centroid_layout.addLayout(clon_layout)
        
        width_layout = QHBoxLayout()
        width_layout.addWidget(QLabel("Width (meters):"))
        self.width_input = QSpinBox()
        self.width_input.setRange(100, 1000000)
        self.width_input.setValue(10000)
        width_layout.addWidget(self.width_input)
        centroid_layout.addLayout(width_layout)
        
        height_layout = QHBoxLayout()
        height_layout.addWidget(QLabel("Height (meters):"))
        self.height_input = QSpinBox()
        self.height_input.setRange(100, 1000000)
        self.height_input.setValue(10000)
        height_layout.addWidget(self.height_input)
        centroid_layout.addLayout(height_layout)
        
        self.centroid_group.setLayout(centroid_layout)
        self.centroid_group.setVisible(False)
        layout.addWidget(self.centroid_group)
        
        # UTM zone override
        utm_layout = QHBoxLayout()
        utm_layout.addWidget(QLabel("UTM Zone Override:"))
        self.utm_zone_input = QSpinBox()
        self.utm_zone_input.setRange(1, 60)
        self.utm_zone_input.setValue(0)
        self.utm_zone_input.setPrefix("Auto (0) or ")
        utm_layout.addWidget(self.utm_zone_input)
        utm_layout.addStretch()
        layout.addLayout(utm_layout)

        round_layout = QHBoxLayout()
        self.utm_round_check = QCheckBox("Round UTM coordinates to nearest 100m")
        self.utm_round_check.setChecked(False)
        round_layout.addWidget(self.utm_round_check)
        round_layout.addStretch()
        layout.addLayout(round_layout)
        
        # Info display
        self.info_label = QLabel("")
        self.info_label.setStyleSheet("color: blue; font-size: 10pt;")
        layout.addWidget(self.info_label)

        preview_header = QLabel("CONUS Preview")
        preview_header.setStyleSheet("font-weight: 600;")
        layout.addWidget(preview_header)
        self.conus_preview = ConusPreviewWidget()
        layout.addWidget(self.conus_preview)
        
        layout.addStretch()
        self.setLayout(layout)
        
        # Connect signals
        self.mode_corners.toggled.connect(self._on_mode_changed)
        self.crs_latlong.toggled.connect(self._on_crs_changed)
        self.lat1_input.valueChanged.connect(self._update_info)
        self.lon1_input.valueChanged.connect(self._update_info)
        self.lat2_input.valueChanged.connect(self._update_info)
        self.lon2_input.valueChanged.connect(self._update_info)
        self.clat_input.valueChanged.connect(self._update_info)
        self.clon_input.valueChanged.connect(self._update_info)
        self.width_input.valueChanged.connect(self._update_info)
        self.height_input.valueChanged.connect(self._update_info)
        self.city_preset_combo.currentIndexChanged.connect(self._on_city_preset_changed)
        self.utm_round_check.toggled.connect(self._on_rounding_toggled)
        self.utm_zone_input.valueChanged.connect(self._on_utm_zone_changed)

        self._set_crs_input_ranges("latlong")
        self._update_info()
    
    def _on_mode_changed(self):
        """Toggle between corners and centroid modes."""
        is_corners = self.mode_corners.isChecked()
        self.corners_group.setVisible(is_corners)
        self.centroid_group.setVisible(not is_corners)
        if self.crs_utm.isChecked() and self.utm_round_check.isChecked():
            self._apply_utm_rounding()
        self._update_info()
    
    def _on_crs_changed(self):
        """Update labels and convert coordinate values when CRS changes."""
        if self._updating_ui:
            return

        new_crs = self._current_crs()
        old_crs = self._last_crs

        if old_crs != new_crs:
            # Avoid clipping when switching to UTM by widening ranges first.
            if new_crs == "utm":
                self._set_crs_input_ranges(new_crs)

            self._translate_inputs(old_crs, new_crs)
            self._last_crs = new_crs

        is_latlong = self.crs_latlong.isChecked()
        if is_latlong:
            self.corners_group.setTitle("Corners (Lat/Long)")
            self.centroid_group.setTitle("Centroid + Size (degrees)")
        else:
            self.corners_group.setTitle("Corners (Easting/Northing - meters)")
            self.centroid_group.setTitle("Centroid + Size (meters)")
        # For UTM this was already done before conversion; for lat/long apply now.
        if new_crs != "utm":
            self._set_crs_input_ranges(new_crs)

        if new_crs == "utm" and self.utm_round_check.isChecked():
            self._apply_utm_rounding()
        self._update_info()

    def _on_city_preset_changed(self):
        """Apply selected city preset to corner fields in current CRS."""
        preset_name = self.city_preset_combo.currentText()
        if preset_name == "Custom (manual entry)":
            return

        bounds = CITY_PRESETS.get(preset_name)
        if bounds is None:
            return

        min_lat, min_lon, max_lat, max_lon = bounds

        self.mode_corners.setChecked(True)

        if self.crs_latlong.isChecked():
            self._set_spin_value(self.lat1_input, max_lat)
            self._set_spin_value(self.lon1_input, min_lon)
            self._set_spin_value(self.lat2_input, min_lat)
            self._set_spin_value(self.lon2_input, max_lon)
        else:
            utm_zone = self.utm_zone_input.value() if self.utm_zone_input.value() > 0 else None
            bbox = BoundingBox(min_lon=min_lon, min_lat=min_lat, max_lon=max_lon, max_lat=max_lat, utm_zone_override=utm_zone)
            zone = utm_zone or bbox.get_utm_zone()
            epsg = 32600 + zone if ((min_lat + max_lat) / 2) >= 0 else 32700 + zone
            transformer = Transformer.from_crs("EPSG:4326", CRS.from_epsg(epsg), always_xy=True)
            x1, y1 = transformer.transform(min_lon, max_lat)
            x2, y2 = transformer.transform(max_lon, min_lat)
            self._set_spin_value(self.lat1_input, y1)
            self._set_spin_value(self.lon1_input, x1)
            self._set_spin_value(self.lat2_input, y2)
            self._set_spin_value(self.lon2_input, x2)
            if self.utm_round_check.isChecked():
                self._apply_utm_rounding()

        self._update_info()

    def _on_rounding_toggled(self):
        if self.crs_utm.isChecked() and self.utm_round_check.isChecked():
            self._apply_utm_rounding()

    def _on_utm_zone_changed(self):
        """When zone changes in UTM mode, keep same bbox by re-projecting displayed values."""
        if self._updating_ui or not self.crs_utm.isChecked() or self.utm_zone_input.value() <= 0:
            return
        self._translate_inputs("utm", "utm")

    def _current_crs(self) -> str:
        return "latlong" if self.crs_latlong.isChecked() else "utm"

    def _set_spin_value(self, spin: QDoubleSpinBox, value: float):
        self._updating_ui = True
        try:
            spin.setValue(float(value))
        finally:
            self._updating_ui = False

    def _set_crs_input_ranges(self, crs: str):
        """Update coordinate input ranges/precision for selected CRS."""
        if crs == "latlong":
            for spin in [self.lat1_input, self.lat2_input, self.clat_input]:
                spin.setDecimals(6)
                spin.setRange(-90.0, 90.0)
            for spin in [self.lon1_input, self.lon2_input, self.clon_input]:
                spin.setDecimals(6)
                spin.setRange(-180.0, 180.0)
        else:
            for spin in [self.lat1_input, self.lat2_input, self.clat_input]:
                spin.setDecimals(2)
                spin.setRange(-10000000.0, 10000000.0)
            for spin in [self.lon1_input, self.lon2_input, self.clon_input]:
                spin.setDecimals(2)
                spin.setRange(-10000000.0, 10000000.0)

    def _translate_inputs(self, old_crs: str, new_crs: str):
        """Translate currently entered coordinates between CRS modes while preserving bbox."""
        try:
            utm_zone = self.utm_zone_input.value() if self.utm_zone_input.value() > 0 else None

            if self.mode_corners.isChecked():
                bbox = BoundingBox.from_corners(
                    self.lat1_input.value(), self.lon1_input.value(),
                    self.lat2_input.value(), self.lon2_input.value(),
                    crs=old_crs, utm_zone=utm_zone,
                )
                self._apply_bbox_to_inputs(bbox, new_crs)
            else:
                bbox = BoundingBox.from_centroid_and_size(
                    self.clat_input.value(), self.clon_input.value(),
                    self.width_input.value(), self.height_input.value(),
                    crs=old_crs, utm_zone=utm_zone,
                )
                self._apply_centroid_from_bbox(bbox, new_crs)
        except Exception:
            return

    def _apply_bbox_to_inputs(self, bbox: BoundingBox, crs: str):
        utm_zone = self.utm_zone_input.value() if self.utm_zone_input.value() > 0 else None
        if crs == "latlong":
            self._set_spin_value(self.lat1_input, bbox.max_lat)
            self._set_spin_value(self.lon1_input, bbox.min_lon)
            self._set_spin_value(self.lat2_input, bbox.min_lat)
            self._set_spin_value(self.lon2_input, bbox.max_lon)
            return

        zone = utm_zone or bbox.get_utm_zone()
        centroid_lat = (bbox.min_lat + bbox.max_lat) / 2
        epsg = 32600 + zone if centroid_lat >= 0 else 32700 + zone
        transformer = Transformer.from_crs("EPSG:4326", CRS.from_epsg(epsg), always_xy=True)
        x1, y1 = transformer.transform(bbox.min_lon, bbox.max_lat)
        x2, y2 = transformer.transform(bbox.max_lon, bbox.min_lat)
        self._set_spin_value(self.lat1_input, y1)
        self._set_spin_value(self.lon1_input, x1)
        self._set_spin_value(self.lat2_input, y2)
        self._set_spin_value(self.lon2_input, x2)

        if self.utm_round_check.isChecked():
            self._apply_utm_rounding()

    def _apply_centroid_from_bbox(self, bbox: BoundingBox, crs: str):
        centroid_lat = (bbox.min_lat + bbox.max_lat) / 2
        centroid_lon = (bbox.min_lon + bbox.max_lon) / 2

        if crs == "latlong":
            self._set_spin_value(self.clat_input, centroid_lat)
            self._set_spin_value(self.clon_input, centroid_lon)
            return

        utm_zone = self.utm_zone_input.value() if self.utm_zone_input.value() > 0 else bbox.get_utm_zone()
        epsg = 32600 + utm_zone if centroid_lat >= 0 else 32700 + utm_zone
        transformer = Transformer.from_crs("EPSG:4326", CRS.from_epsg(epsg), always_xy=True)
        x, y = transformer.transform(centroid_lon, centroid_lat)
        self._set_spin_value(self.clat_input, y)
        self._set_spin_value(self.clon_input, x)

        if self.utm_round_check.isChecked():
            self._apply_utm_rounding()

    def _apply_utm_rounding(self):
        """Round displayed UTM coordinates to nearest 100m."""
        if not self.crs_utm.isChecked():
            return

        step = 100.0
        if self.mode_corners.isChecked():
            spins = [self.lat1_input, self.lon1_input, self.lat2_input, self.lon2_input]
        else:
            spins = [self.clat_input, self.clon_input]

        self._updating_ui = True
        try:
            for spin in spins:
                spin.setValue(round(spin.value() / step) * step)
        finally:
            self._updating_ui = False
    
    def _update_info(self):
        """Update info label with bbox stats."""
        if self._updating_ui:
            return
        try:
            bbox = self.get_bbox()
            if bbox:
                info = f"Area: {bbox.area_km2():.0f} km² | Size: {bbox.width_km():.1f}km × {bbox.height_km():.1f}km | UTM Zone: {bbox.get_utm_zone()}"
                self.info_label.setText(info)
                self.conus_preview.set_bbox(bbox)
        except Exception:
            self.conus_preview.set_bbox(None)
    
    def get_bbox(self) -> BoundingBox:
        """Get BoundingBox from current input."""
        crs = "latlong" if self.crs_latlong.isChecked() else "utm"
        utm_zone = self.utm_zone_input.value() if self.utm_zone_input.value() > 0 else None
        
        if self.mode_corners.isChecked():
            return BoundingBox.from_corners(
                self.lat1_input.value(), self.lon1_input.value(),
                self.lat2_input.value(), self.lon2_input.value(),
                crs=crs, utm_zone=utm_zone
            )
        else:
            return BoundingBox.from_centroid_and_size(
                self.clat_input.value(), self.clon_input.value(),
                self.width_input.value(), self.height_input.value(),
                crs=crs, utm_zone=utm_zone
            )

    def selected_preset_name(self) -> str | None:
        """Return selected city preset name, or None for custom/manual entry."""
        text = self.city_preset_combo.currentText().strip()
        if not text or text == "Custom (manual entry)":
            return None
        return text
