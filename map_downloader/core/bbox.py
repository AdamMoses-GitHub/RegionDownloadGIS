"""Bounding box definition and coordinate conversion utilities."""

from dataclasses import dataclass, field
from typing import Literal, Optional, Tuple
import hashlib
import math
from shapely.geometry import box, Point, Polygon
import pyproj
from pyproj import CRS, Transformer


@dataclass
class BoundingBox:
    """
    Bounding box stored internally in WGS84 (EPSG:4326).
    Provides conversions to UTM and other formats on request.
    """
    
    # Internal WGS84 storage (min_lon, min_lat, max_lon, max_lat)
    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float
    
    # Optional user-specified UTM zone override (e.g., 18)
    utm_zone_override: Optional[int] = None
    
    @classmethod
    def from_corners(cls, lat1: float, lon1: float, lat2: float, lon2: float, 
                     crs: Literal["latlong", "utm"] = "latlong", utm_zone: Optional[int] = None) -> "BoundingBox":
        """
        Create bounding box from two corner points.
        
        Args:
            lat1, lon1: First corner (degrees or meters depending on crs)
            lat2, lon2: Second corner (degrees or meters depending on crs)
            crs: "latlong" for WGS84, "utm" for UTM
            utm_zone: UTM zone if crs="utm"
        """
        if crs == "latlong":
            min_lat, max_lat = min(lat1, lat2), max(lat1, lat2)
            min_lon, max_lon = min(lon1, lon2), max(lon1, lon2)
            return cls(min_lon=min_lon, min_lat=min_lat, 
                      max_lon=max_lon, max_lat=max_lat, utm_zone_override=None)
        elif crs == "utm":
            if utm_zone is None:
                raise ValueError("utm_zone required when crs='utm'")
            # Convert from UTM to WGS84
            transformer = Transformer.from_crs(
                CRS.from_epsg(_utm_zone_to_epsg(utm_zone, lat1)),
                "EPSG:4326", always_xy=True
            )
            lon1_wgs, lat1_wgs = transformer.transform(lon1, lat1)
            lon2_wgs, lat2_wgs = transformer.transform(lon2, lat2)
            min_lat, max_lat = min(lat1_wgs, lat2_wgs), max(lat1_wgs, lat2_wgs)
            min_lon, max_lon = min(lon1_wgs, lon2_wgs), max(lon1_wgs, lon2_wgs)
            return cls(min_lon=min_lon, min_lat=min_lat,
                      max_lon=max_lon, max_lat=max_lat, utm_zone_override=utm_zone)
        else:
            raise ValueError(f"Unknown crs: {crs}")
    
    @classmethod
    def from_centroid_and_size(cls, centroid_lat: float, centroid_lon: float,
                               width_m: float, height_m: float,
                               crs: Literal["latlong", "utm"] = "latlong",
                               utm_zone: Optional[int] = None) -> "BoundingBox":
        """
        Create bounding box from centroid + width/height.
        
        Args:
            centroid_lat, centroid_lon: Centroid (degrees or meters)
            width_m: Width in meters (E-W)
            height_m: Height in meters (N-S)
            crs: "latlong" for WGS84, "utm" for UTM
            utm_zone: UTM zone if crs="utm"
        """
        if crs == "latlong":
            # Rough approximation: 1 degree ≈ 111 km at equator
            lat_delta = (height_m / 2) / 111000
            cos_lat = max(math.cos(math.radians(centroid_lat)), 1e-6)
            lon_delta = (width_m / 2) / (111000 * cos_lat)
            return cls(
                min_lon=centroid_lon - lon_delta,
                max_lon=centroid_lon + lon_delta,
                min_lat=centroid_lat - lat_delta,
                max_lat=centroid_lat + lat_delta,
                utm_zone_override=None
            )
        elif crs == "utm":
            if utm_zone is None:
                raise ValueError("utm_zone required when crs='utm'")
            half_width = width_m / 2
            half_height = height_m / 2
            # Create UTM corners, convert to WGS84
            transformer = Transformer.from_crs(
                CRS.from_epsg(_utm_zone_to_epsg(utm_zone, centroid_lat)),
                "EPSG:4326", always_xy=True
            )
            lon_nw, lat_nw = transformer.transform(centroid_lon - half_width, centroid_lat + half_height)
            lon_se, lat_se = transformer.transform(centroid_lon + half_width, centroid_lat - half_height)
            return cls(
                min_lon=min(lon_nw, lon_se),
                max_lon=max(lon_nw, lon_se),
                min_lat=min(lat_nw, lat_se),
                max_lat=max(lat_nw, lat_se),
                utm_zone_override=utm_zone
            )
        else:
            raise ValueError(f"Unknown crs: {crs}")
    
    def get_utm_zone(self) -> int:
        """Get UTM zone, using override if set, otherwise auto-detect from centroid."""
        if self.utm_zone_override is not None:
            return self.utm_zone_override
        centroid_lon = (self.min_lon + self.max_lon) / 2
        return int((centroid_lon + 180) / 6) + 1
    
    def get_utm_epsg(self) -> int:
        """Get EPSG code for UTM zone."""
        zone = self.get_utm_zone()
        centroid_lat = (self.min_lat + self.max_lat) / 2
        return _utm_zone_to_epsg(zone, centroid_lat)
    
    def get_wgs84_polygon(self) -> Polygon:
        """Return bbox as WGS84 Polygon."""
        return box(self.min_lon, self.min_lat, self.max_lon, self.max_lat)

    def to_polygon_wgs84(self) -> Polygon:
        """Compatibility alias used by downloader/processing modules."""
        return self.get_wgs84_polygon()
    
    def get_utm_polygon(self) -> Polygon:
        """Return bbox as UTM Polygon."""
        transformer = Transformer.from_crs("EPSG:4326", 
                                          CRS.from_epsg(self.get_utm_epsg()),
                                          always_xy=True)
        wgs_poly = self.get_wgs84_polygon()
        utm_poly = Polygon([transformer.transform(lon, lat) for lon, lat in wgs_poly.exterior.coords])
        return utm_poly

    def to_polygon_utm(self) -> Polygon:
        """Compatibility alias used by downloader/processing modules."""
        return self.get_utm_polygon()
    
    def area_km2(self) -> float:
        """Approximate area in km²."""
        utm_poly = self.get_utm_polygon()
        return utm_poly.area / 1e6
    
    def width_km(self) -> float:
        """Width (E-W) in kilometers."""
        utm_poly = self.get_utm_polygon()
        bounds = utm_poly.bounds
        return (bounds[2] - bounds[0]) / 1000
    
    def height_km(self) -> float:
        """Height (N-S) in kilometers."""
        utm_poly = self.get_utm_polygon()
        bounds = utm_poly.bounds
        return (bounds[3] - bounds[1]) / 1000
    
    def max_dimension_km(self) -> float:
        """Maximum of width/height in km."""
        return max(self.width_km(), self.height_km())
    
    def exceeds_size_threshold(self, threshold_km: float = 50) -> bool:
        """Check if any dimension exceeds threshold."""
        return self.max_dimension_km() > threshold_km

    def expanded(self, margin_fraction: float) -> "BoundingBox":
        """
        Return a new bbox expanded by a fraction of current width/height on each side.

        Example: margin_fraction=0.25 yields +25% on each side.
        """
        if margin_fraction < 0:
            raise ValueError("margin_fraction must be >= 0")

        lon_span = self.max_lon - self.min_lon
        lat_span = self.max_lat - self.min_lat
        lon_margin = lon_span * margin_fraction
        lat_margin = lat_span * margin_fraction

        min_lon = max(-180.0, self.min_lon - lon_margin)
        max_lon = min(180.0, self.max_lon + lon_margin)
        min_lat = max(-90.0, self.min_lat - lat_margin)
        max_lat = min(90.0, self.max_lat + lat_margin)

        return BoundingBox(
            min_lon=min_lon,
            min_lat=min_lat,
            max_lon=max_lon,
            max_lat=max_lat,
            utm_zone_override=self.utm_zone_override,
        )
    
    def hash(self) -> str:
        """Create hash of bbox for cache keying."""
        key = f"{self.min_lon:.6f}_{self.min_lat:.6f}_{self.max_lon:.6f}_{self.max_lat:.6f}"
        return hashlib.md5(key.encode()).hexdigest()
    
    def as_dict(self) -> dict:
        """Serialize to dict for JSON storage."""
        return {
            "min_lon": self.min_lon,
            "min_lat": self.min_lat,
            "max_lon": self.max_lon,
            "max_lat": self.max_lat,
            "utm_zone_override": self.utm_zone_override,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "BoundingBox":
        """Deserialize from dict."""
        return cls(
            min_lon=data["min_lon"],
            min_lat=data["min_lat"],
            max_lon=data["max_lon"],
            max_lat=data["max_lat"],
            utm_zone_override=data.get("utm_zone_override"),
        )


def _utm_zone_to_epsg(zone: int, lat: float) -> int:
    """Convert UTM zone to EPSG code."""
    # Southern hemisphere: 327xx, Northern: 326xx
    if lat < 0:
        return 32700 + zone
    else:
        return 32600 + zone
