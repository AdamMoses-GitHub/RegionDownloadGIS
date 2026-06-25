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
    # Optional strict UTM rectangle bounds in meters (preserved for UTM input mode)
    utm_min_e: Optional[float] = None
    utm_min_n: Optional[float] = None
    utm_max_e: Optional[float] = None
    utm_max_n: Optional[float] = None
    
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
            min_n, max_n = min(lat1, lat2), max(lat1, lat2)
            min_e, max_e = min(lon1, lon2), max(lon1, lon2)

            # Convert UTM rectangle corners to WGS84 envelope for API-friendly storage
            centroid_n = (min_n + max_n) / 2
            transformer = Transformer.from_crs(
                CRS.from_epsg(_utm_zone_to_epsg(utm_zone, centroid_n)),
                "EPSG:4326", always_xy=True
            )

            wgs_points = [
                transformer.transform(min_e, min_n),
                transformer.transform(max_e, min_n),
                transformer.transform(max_e, max_n),
                transformer.transform(min_e, max_n),
            ]
            lons = [p[0] for p in wgs_points]
            lats = [p[1] for p in wgs_points]
            min_lat, max_lat = min(lats), max(lats)
            min_lon, max_lon = min(lons), max(lons)
            return cls(min_lon=min_lon, min_lat=min_lat,
                      max_lon=max_lon, max_lat=max_lat, utm_zone_override=utm_zone,
                      utm_min_e=min_e, utm_min_n=min_n, utm_max_e=max_e, utm_max_n=max_n)
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

            # Inputs in UTM mode are northing/easting meters.
            center_n = centroid_lat
            center_e = centroid_lon
            half_width = width_m / 2.0
            half_height = height_m / 2.0
            min_e, max_e = center_e - half_width, center_e + half_width
            min_n, max_n = center_n - half_height, center_n + half_height

            # Create UTM rectangle corners, convert to WGS84 envelope.
            transformer = Transformer.from_crs(
                CRS.from_epsg(_utm_zone_to_epsg(utm_zone, center_n)),
                "EPSG:4326", always_xy=True
            )

            wgs_points = [
                transformer.transform(min_e, min_n),
                transformer.transform(max_e, min_n),
                transformer.transform(max_e, max_n),
                transformer.transform(min_e, max_n),
            ]
            lons = [p[0] for p in wgs_points]
            lats = [p[1] for p in wgs_points]

            return cls(
                min_lon=min(lons),
                max_lon=max(lons),
                min_lat=min(lats),
                max_lat=max(lats),
                utm_zone_override=utm_zone,
                utm_min_e=min_e,
                utm_min_n=min_n,
                utm_max_e=max_e,
                utm_max_n=max_n,
            )
        else:
            raise ValueError(f"Unknown crs: {crs}")

    def has_strict_utm_bounds(self) -> bool:
        """True when this bbox includes preserved axis-aligned UTM min/max bounds."""
        return all(v is not None for v in [self.utm_min_e, self.utm_min_n, self.utm_max_e, self.utm_max_n])

    def get_utm_bounds(self) -> tuple[float, float, float, float]:
        """Get UTM bounds as (min_e, min_n, max_e, max_n)."""
        if self.has_strict_utm_bounds():
            return (
                float(self.utm_min_e),
                float(self.utm_min_n),
                float(self.utm_max_e),
                float(self.utm_max_n),
            )

        utm_poly = self.get_utm_polygon()
        min_e, min_n, max_e, max_n = utm_poly.bounds
        return (float(min_e), float(min_n), float(max_e), float(max_n))
    
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
        if self.has_strict_utm_bounds():
            min_e, min_n, max_e, max_n = self.get_utm_bounds()
            return box(min_e, min_n, max_e, max_n)

        transformer = Transformer.from_crs("EPSG:4326", 
                                          CRS.from_epsg(self.get_utm_epsg()),
                                          always_xy=True)
        wgs_poly = self.get_wgs84_polygon()
        utm_poly = Polygon([transformer.transform(lon, lat) for lon, lat in wgs_poly.exterior.coords])
        return utm_poly

    def to_polygon_in_crs(self, target_crs) -> Polygon:
        """Return bbox polygon in target CRS, preferring strict UTM rectangle when available."""
        target = CRS.from_user_input(target_crs)

        if self.has_strict_utm_bounds():
            source = CRS.from_epsg(self.get_utm_epsg())
            min_e, min_n, max_e, max_n = self.get_utm_bounds()
            strict_poly = box(min_e, min_n, max_e, max_n)
            if source == target:
                return strict_poly

            transformer = Transformer.from_crs(source, target, always_xy=True)
            return Polygon([transformer.transform(x, y) for x, y in strict_poly.exterior.coords])

        source = CRS.from_epsg(4326)
        if source == target:
            return self.get_wgs84_polygon()

        transformer = Transformer.from_crs(source, target, always_xy=True)
        wgs_poly = self.get_wgs84_polygon()
        return Polygon([transformer.transform(lon, lat) for lon, lat in wgs_poly.exterior.coords])

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
            "utm_min_e": self.utm_min_e,
            "utm_min_n": self.utm_min_n,
            "utm_max_e": self.utm_max_e,
            "utm_max_n": self.utm_max_n,
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
            utm_min_e=data.get("utm_min_e"),
            utm_min_n=data.get("utm_min_n"),
            utm_max_e=data.get("utm_max_e"),
            utm_max_n=data.get("utm_max_n"),
        )


def _utm_zone_to_epsg(zone: int, lat: float) -> int:
    """Convert UTM zone to EPSG code."""
    # Southern hemisphere: 327xx, Northern: 326xx
    if lat < 0:
        return 32700 + zone
    else:
        return 32600 + zone
