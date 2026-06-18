"""Project state management (save/load)."""

import json
import re
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any
from pathlib import Path
from enum import Enum
from datetime import datetime
from map_downloader.core.bbox import BoundingBox


class HeightMode(str, Enum):
    """Building height handling mode."""
    FLAT = "flat"
    ESTIMATE = "estimate"
    MEAN = "mean"


class BuildingSource(str, Enum):
    """Building data source."""
    OSM = "osm"
    MICROSOFT = "microsoft"
    MERGED = "merged"


class TimestampMode(str, Enum):
    """Project folder timestamp placement mode."""

    NONE = "none"
    PREPEND = "prepend"
    APPEND = "append"


@dataclass
class LayerConfig:
    """Configuration for a single data layer."""
    enabled: bool = True
    
    # Terrain-specific
    terrain_source: str = "3dep"  # "3dep", "srtm", "auto"
    
    # Building-specific
    building_source: str = "merged"  # "osm", "microsoft", "merged"
    building_height_mode: HeightMode = HeightMode.ESTIMATE
    
    # Land use-specific
    landuse_raster: bool = True  # Include NLCD raster
    landuse_vector: bool = True  # Include OSM vector

    # Water-specific
    water_vector: bool = True  # Include OSM water polygons
    
    # Reference map-specific
    reference_zoom: Optional[int] = None  # None = auto
    
    def as_dict(self) -> dict:
        """Serialize to dict."""
        return {
            "enabled": self.enabled,
            "terrain_source": self.terrain_source,
            "building_source": self.building_source,
            "building_height_mode": self.building_height_mode.value,
            "landuse_raster": self.landuse_raster,
            "landuse_vector": self.landuse_vector,
            "water_vector": self.water_vector,
            "reference_zoom": self.reference_zoom,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "LayerConfig":
        """Deserialize from dict."""
        return cls(
            enabled=data.get("enabled", True),
            terrain_source=data.get("terrain_source", "3dep"),
            building_source=data.get("building_source", "merged"),
            building_height_mode=HeightMode(data.get("building_height_mode", "estimate")),
            landuse_raster=data.get("landuse_raster", True),
            landuse_vector=data.get("landuse_vector", True),
            water_vector=data.get("water_vector", True),
            reference_zoom=data.get("reference_zoom"),
        )


@dataclass
class Project:
    """Complete project state."""
    
    # Project metadata
    name: str = "Untitled Project"
    
    # Bounding box (stored as dict for JSON serialization)
    bbox_dict: dict = field(default_factory=dict)
    
    # Layer configurations
    layers: dict = field(default_factory=lambda: {
        "terrain": LayerConfig(enabled=True),
        "buildings": LayerConfig(enabled=True),
        "landuse": LayerConfig(enabled=True),
        "water": LayerConfig(enabled=True),
        "big_streets": LayerConfig(enabled=False),
        "small_streets": LayerConfig(enabled=False),
        "reference": LayerConfig(enabled=True),
    })
    
    # Output settings
    output_folder: str = ""
    resolution_m: float = 5.0
    utm_zone_override: Optional[int] = None
    timestamp_mode: str = TimestampMode.APPEND.value
    # Legacy compatibility field. New code should prefer timestamp_mode.
    append_timestamp_to_name: bool = True

    # Resolved runtime output folder for this project run/session
    output_run_folder: str = ""
    output_run_key: str = ""
    
    # Derived/runtime
    created_timestamp: str = ""
    modified_timestamp: str = ""
    
    def get_bbox(self) -> Optional[BoundingBox]:
        """Get BoundingBox from stored dict."""
        if self.bbox_dict:
            return BoundingBox.from_dict(self.bbox_dict)
        return None
    
    def set_bbox(self, bbox: BoundingBox):
        """Store BoundingBox as dict."""
        self.bbox_dict = bbox.as_dict()
    
    def get_layer_config(self, layer_name: str) -> Optional[LayerConfig]:
        """Get config for a layer."""
        return self.layers.get(layer_name)
    
    def as_dict(self) -> dict:
        """Serialize to dict for JSON."""
        mode = self._normalized_timestamp_mode()
        return {
            "name": self.name,
            "bbox": self.bbox_dict,
            "layers": {k: v.as_dict() for k, v in self.layers.items()},
            "output_folder": self.output_folder,
            "resolution_m": self.resolution_m,
            "utm_zone_override": self.utm_zone_override,
            "timestamp_mode": mode,
            "append_timestamp_to_name": (mode != TimestampMode.NONE.value),
            "output_run_folder": self.output_run_folder,
            "output_run_key": self.output_run_key,
            "created_timestamp": self.created_timestamp,
            "modified_timestamp": self.modified_timestamp,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "Project":
        """Deserialize from dict."""
        raw_mode = str(data.get("timestamp_mode", "")).lower().strip()
        if raw_mode in {
            TimestampMode.NONE.value,
            TimestampMode.PREPEND.value,
            TimestampMode.APPEND.value,
        }:
            timestamp_mode = raw_mode
        else:
            timestamp_mode = (
                TimestampMode.APPEND.value
                if data.get("append_timestamp_to_name", True)
                else TimestampMode.NONE.value
            )

        project = cls(
            name=data.get("name", "Untitled Project"),
            bbox_dict=data.get("bbox", {}),
            output_folder=data.get("output_folder", ""),
            resolution_m=data.get("resolution_m", 5.0),
            utm_zone_override=data.get("utm_zone_override"),
            timestamp_mode=timestamp_mode,
            append_timestamp_to_name=(timestamp_mode != TimestampMode.NONE.value),
            output_run_folder=data.get("output_run_folder", ""),
            output_run_key=data.get("output_run_key", ""),
            created_timestamp=data.get("created_timestamp", ""),
            modified_timestamp=data.get("modified_timestamp", ""),
        )
        if "layers" in data:
            merged_layers = dict(project.layers)
            for k, v in data["layers"].items():
                merged_layers[k] = LayerConfig.from_dict(v)
            project.layers = merged_layers
        return project
    
    def save(self, path: str):
        """Save project to .r3d.json file."""
        from datetime import datetime
        self.modified_timestamp = datetime.now().isoformat()
        with open(path, "w") as f:
            json.dump(self.as_dict(), f, indent=2)
    
    @classmethod
    def load(cls, path: str) -> "Project":
        """Load project from .r3d.json file."""
        with open(path, "r") as f:
            data = json.load(f)
        return cls.from_dict(data)

    @staticmethod
    def sanitize_name(name: str) -> str:
        """Convert project name into a filesystem-safe folder name."""
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", (name or "").strip())
        cleaned = cleaned.strip("._-")
        return cleaned or "untitled_project"

    def output_base_folder(self) -> Path:
        """Return configured output base folder or default workspace-local folder."""
        if self.output_folder:
            return Path(self.output_folder)
        return Path.cwd() / "region3d_output"

    def resolve_output_root(self, force_refresh: bool = False) -> Path:
        """Resolve and memoize per-project output folder for the current project settings."""
        mode = self._normalized_timestamp_mode()
        key = "|".join([
            str(self.output_base_folder()),
            str(self.name),
            mode,
        ])

        if force_refresh or not self.output_run_folder or self.output_run_key != key:
            folder_name = self.sanitize_name(self.name)
            stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            if mode == TimestampMode.APPEND.value:
                folder_name = f"{folder_name}_{stamp}"
            elif mode == TimestampMode.PREPEND.value:
                folder_name = f"{stamp}_{folder_name}"

            resolved = self.output_base_folder() / folder_name
            self.output_run_folder = str(resolved)
            self.output_run_key = key

        return Path(self.output_run_folder)

    def _normalized_timestamp_mode(self) -> str:
        mode = str(self.timestamp_mode or "").lower().strip()
        if mode in {
            TimestampMode.NONE.value,
            TimestampMode.PREPEND.value,
            TimestampMode.APPEND.value,
        }:
            self.append_timestamp_to_name = mode != TimestampMode.NONE.value
            return mode

        # Fallback for legacy projects that only used append_timestamp_to_name.
        mode = TimestampMode.APPEND.value if self.append_timestamp_to_name else TimestampMode.NONE.value
        self.timestamp_mode = mode
        return mode
