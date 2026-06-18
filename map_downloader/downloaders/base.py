"""Abstract base class for all data downloaders."""

from abc import ABC, abstractmethod
from typing import Callable, Optional
from pathlib import Path
import tempfile

from map_downloader.core.bbox import BoundingBox


class DownloaderBase(ABC):
    """
    Abstract base class for all data downloaders.
    
    Defines the interface and common functionality for downloading GIS data.
    """
    
    def __init__(
        self,
        name: str,
        cache_manager=None,
        progress_callback: Optional[Callable[[int, str], None]] = None
    ):
        """
        Initialize downloader.
        
        Args:
            name: Downloader name (e.g., 'terrain', 'buildings')
            cache_manager: CacheManager instance for caching downloads
            progress_callback: Optional callback for progress updates: callback(percent, status_text)
        """
        self.name = name
        self.cache_manager = cache_manager
        self.progress_callback = progress_callback
        self.temp_dir = Path(tempfile.gettempdir())
    
    def _report_progress(self, percent: int, status: str):
        """Report progress via callback if registered."""
        if self.progress_callback:
            self.progress_callback(percent, status)
    
    @abstractmethod
    def download(
        self,
        bbox: BoundingBox,
        resolution_m: int,
        output_path: Path,
        **kwargs
    ) -> dict:
        """
        Download data for the given bounding box.
        
        Args:
            bbox: BoundingBox instance defining area of interest
            resolution_m: Target resolution in meters
            output_path: Path where to save output files
            **kwargs: Downloader-specific parameters
        
        Returns:
            dict with keys:
                - 'success': bool
                - 'files': list of output file paths (GeoTIFF, GeoJSON, etc.)
                - 'message': status message
                - 'cache_hit': bool (was this from cache?)
        
        Raises:
            Exception: If download fails
        """
        pass
    
    def _build_cache_key(self, bbox: BoundingBox, resolution_m: int) -> str:
        """Build cache key from bbox, resolution, and optional downloader settings."""
        if not self.cache_manager:
            return None
        bbox_hash = bbox.hash() if callable(getattr(bbox, "hash", None)) else str(bbox)

        source_id = self.name
        options = getattr(self, "_cache_options", None)
        if isinstance(options, dict) and options:
            parts = [f"{k}={options[k]}" for k in sorted(options)]
            source_id = f"{self.name}:{'|'.join(parts)}"

        return self.cache_manager.make_key(source_id, bbox_hash, resolution_m)

    def _set_cache_options(self, **options):
        """Set per-call options that should participate in cache key generation."""
        normalized = {}
        for key, value in options.items():
            if isinstance(value, bool):
                normalized[key] = int(value)
            else:
                normalized[key] = value
        self._cache_options = normalized

    def _get_cached_files_if_valid(self, cache_key):
        """Return cached file list only when all cached paths still exist on disk."""
        if not self.cache_manager or not cache_key:
            return None
        cached_files = self.cache_manager.get(cache_key)
        if not isinstance(cached_files, list) or not cached_files:
            return None
        for path_str in cached_files:
            if not isinstance(path_str, str) or not Path(path_str).exists():
                return None
        return cached_files
