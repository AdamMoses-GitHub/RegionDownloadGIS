"""Disk-based cache for downloaded data."""

import hashlib
import os
from pathlib import Path
import diskcache
from typing import Optional, Any


class CacheManager:
    """
    File-based cache for downloaded GIS data.
    Keys are (source_id, bbox_hash, resolution_m).
    """
    
    def __init__(self, cache_dir: str = ".cache", fallback_cache: Optional["CacheManager"] = None):
        """
        Initialize cache manager.
        
        Args:
            cache_dir: Directory to store cache files
        """
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache = diskcache.Cache(str(self.cache_dir))
        self.fallback_cache = fallback_cache

    @staticmethod
    def default_global_cache_dir() -> Path:
        """Return application-wide cache directory path."""
        local_app_data = os.getenv("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "Region3DModelCreator" / "cache"
        return Path.home() / ".cache" / "Region3DModelCreator"
    
    def make_key(self, source_id: str, bbox_hash: str, resolution_m: float) -> str:
        """Create cache key from source, bbox, and resolution."""
        key_str = f"{source_id}_{bbox_hash}_{resolution_m:.1f}"
        return hashlib.md5(key_str.encode()).hexdigest()
    
    def _normalize_key(self, source_or_key: str, bbox_hash: Optional[str], resolution_m: Optional[float]) -> str:
        """Support both raw key and legacy (source, bbox_hash, resolution) calls."""
        if bbox_hash is None and resolution_m is None:
            return source_or_key
        if bbox_hash is None or resolution_m is None:
            raise ValueError("Both bbox_hash and resolution_m are required for source-based cache access")
        return self.make_key(source_or_key, bbox_hash, resolution_m)

    def get(self, source_or_key: str, bbox_hash: str = None, resolution_m: float = None) -> Optional[Any]:
        """
        Retrieve cached data.
        
        Args:
            source_id: Data source identifier (e.g., "terrain", "buildings_osm")
            bbox_hash: Hash of bounding box
            resolution_m: Target resolution in meters
            
        Returns:
            Cached value or None if not found/expired
        """
        key = self._normalize_key(source_or_key, bbox_hash, resolution_m)
        try:
            value = self.cache.get(key)
            if value is not None:
                return value
        except (KeyError, diskcache.Timeout, ValueError):
            value = None

        # Backward-compatibility path: read from fallback cache and promote.
        if self.fallback_cache is not None:
            fallback_value = self.fallback_cache.get(key)
            if fallback_value is not None:
                self.cache.set(key, fallback_value)
                return fallback_value

        return None
    
    def put(self, source_or_key: str, *args, expire_days: int = 30):
        """
        Store data in cache.
        
        Args:
            source_id: Data source identifier
            bbox_hash: Hash of bounding box
            resolution_m: Target resolution in meters
            value: Data to cache (typically a file path)
            expire_days: Cache expiry in days (0 = never)
        """
        if len(args) == 1:
            key = source_or_key
            value = args[0]
        elif len(args) == 3:
            bbox_hash, resolution_m, value = args
            key = self.make_key(source_or_key, bbox_hash, resolution_m)
        else:
            raise ValueError("put expects (key, value) or (source, bbox_hash, resolution_m, value)")

        expire_seconds = None if expire_days == 0 else (expire_days * 24 * 3600)
        self.cache.set(key, value, expire=expire_seconds)
    
    def has(self, source_or_key: str, bbox_hash: str = None, resolution_m: float = None) -> bool:
        """Check if data is cached."""
        key = self._normalize_key(source_or_key, bbox_hash, resolution_m)
        if key in self.cache:
            return True
        if self.fallback_cache is not None:
            return self.fallback_cache.has(key)
        return False
    
    def invalidate(self, source_id: str = None, bbox_hash: str = None, resolution_m: float = None):
        """
        Invalidate cache entries.
        
        If no args, clears entire cache.
        If source_id only, clears all entries for that source.
        If all args, clears specific entry.
        """
        if source_id is None:
            self.cache.clear()
        elif bbox_hash is None:
            # Clear all entries for this source (pattern matching)
            keys_to_delete = []
            for key in self.cache:
                if key.startswith(f"{source_id}_"):
                    keys_to_delete.append(key)
            for key in keys_to_delete:
                del self.cache[key]
        else:
            # Clear specific entry
            key = self.make_key(source_id, bbox_hash, resolution_m)
            if key in self.cache:
                del self.cache[key]
    
    def cleanup(self):
        """Close cache connection."""
        self.cache.close()
