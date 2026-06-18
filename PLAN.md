# Region3D Map Data Downloader — Build Plan

## Overview

A wizard-style PySide6 GUI application for downloading, processing, and exporting public GIS data
(terrain, buildings, land use, reference map) for a user-defined geographic bounding box.
Initially targeting CONUS; designed for future global expansion.

---

## Decisions Log

| Decision | Choice |
|---|---|
| GUI framework | PySide6 (Qt) |
| Output CRS | UTM (auto zone from centroid), user-overridable |
| Building heights | User chooses: flat / rough estimate; OS + Microsoft intelligent merge default |
| Land use sources | Both NLCD raster (30m) + OSM vector polygons; user selects at export |
| Caching | File-based (`diskcache`), keyed by source + bbox hash + resolution |
| Large area warning | Warn + confirm when any side > 50km or estimated output > 500MB |
| Project save/load | `.r3d.json` at any wizard page |
| UTM zone | Auto-detect from centroid, overridable on output settings page |
| Building source overlap | Intelligent merge (prefer OSM geometry; MS footprint where OSM absent) |
| Default resolution | 5 meters |
| NLCD upsampling | Nearest-neighbor for categorical data; users are informed of 30m native limit |
| Wizard style | Linear QWizard; flexible layout kept for future refactor to dockable panels |
| Python environment | `.conda/` (Python 3.12.9) |

---

## Data Sources

| Layer | Source | Format | Notes |
|---|---|---|---|
| Terrain | USGS 3DEP via `py3dep` | GeoTIFF | 1m/3m/10m CONUS; SRTM 30m global fallback |
| Buildings | OSM via Overpass API | GeoJSON | Footprints + height/levels tags |
| Buildings (alt) | Microsoft US Building Footprints | GeoJSON | ~130M footprints; partial height estimates |
| Land Use (raster) | NLCD via MRLC WCS | GeoTIFF | 30m native, 20 land cover classes |
| Land Use (vector) | OSM land use via Overpass | GeoJSON | Resolution-independent polygons |
| Reference Map | OpenStreetMap XYZ tiles | GeoTIFF (stitched RGB) | No API key; auto zoom from bbox size |

---

## Project Structure

```
Region3DModelCreator/
├── .conda/                            # existing Python 3.12.9 env
├── map_downloader/
│   ├── __init__.py
│   ├── main.py                        # entry point — QApplication launch
│   ├── core/
│   │   ├── __init__.py
│   │   ├── bbox.py                    # BoundingBox dataclass + coord conversion
│   │   ├── project.py                 # project save/load (.r3d.json)
│   │   └── cache.py                   # disk cache manager (diskcache)
│   ├── downloaders/
│   │   ├── __init__.py
│   │   ├── base.py                    # abstract DownloaderBase + progress callback
│   │   ├── terrain.py                 # USGS 3DEP (primary), SRTM (fallback)
│   │   ├── buildings.py               # OSM Overpass + Microsoft Footprints + merge
│   │   ├── landuse.py                 # NLCD raster + OSM land use polygons
│   │   └── reference.py              # OSM XYZ tile fetcher + stitcher
│   ├── processing/
│   │   ├── __init__.py
│   │   ├── reproject.py               # reproject all layers to target UTM
│   │   ├── resample.py                # crop to bbox + resample to target resolution
│   │   └── merge.py                   # building deduplication + attribute merge
│   ├── export/
│   │   ├── __init__.py
│   │   └── exporter.py                # GeoTIFF / GeoJSON / Shapefile / GeoPackage
│   └── gui/
│       ├── __init__.py
│       ├── app.py                     # QApplication setup + theme
│       ├── wizard.py                  # QWizard shell + page routing + save/load
│       ├── pages/
│       │   ├── __init__.py
│       │   ├── p1_bbox.py             # Step 1: Define bounding box
│       │   ├── p2_layers.py           # Step 2: Configure layers
│       │   ├── p3_output.py           # Step 3: Output settings
│       │   ├── p4_download.py         # Step 4: Download + progress
│       │   ├── p5_preview.py          # Step 5: 2D preview + 3D placeholder
│       │   └── p6_export.py           # Step 6: Export
│       └── widgets/
│           ├── __init__.py
│           ├── bbox_widget.py         # reusable bbox input (corners / centroid+size)
│           ├── layer_card.py          # per-layer enable toggle + config sub-panel
│           └── progress_panel.py      # per-layer labeled progress bars
├── requirements.txt
├── environment.yml
└── PLAN.md                            # this file
```

---

## Build Phases

### Phase 1 — Project scaffold + dependencies

- Create all directories and `__init__.py` stubs
- Write `environment.yml` + `requirements.txt`
- Install packages into `.conda`
- Smoke-test all key imports

**Key packages:**
`PySide6`, `rasterio`, `geopandas`, `pyproj`, `shapely`, `fiona`, `numpy`,
`requests`, `mercantile`, `Pillow`, `py3dep`, `overpy`, `diskcache`, `elevation`

---

### Phase 2 — Bounding box + coordinate engine

**`core/bbox.py`**
- `BoundingBox` dataclass — stores internally in WGS84; converts on request
- Input mode A: four corners (lat/long or UTM with zone)
- Input mode B: centroid + width/height in meters or degrees
- UTM zone auto-detect from centroid (EPSG:326xx N / 327xx S), override-able
- Outputs: WGS84 polygon, UTM polygon, area m², side lengths, size warning flag

**`core/project.py`**
- `Project` dataclass wrapping all wizard state
- `save(path)` → `.r3d.json` (JSON-serializable)
- `load(path)` → `Project` instance

**`core/cache.py`**
- `CacheManager` wrapping `diskcache.Cache`
- Key: `(source_id, bbox_wkt_hash, resolution_m)`
- Methods: `get()`, `put()`, `has()`, `invalidate()`

---

### Phase 3 — Wizard UI skeleton

**`gui/wizard.py`** — `QWizard` with 6 registered pages; toolbar with Save/Load project buttons.

**Page 1 — Define Area (`p1_bbox.py`)**
- Radio: corners mode / centroid+size mode
- Coordinate system toggle: lat/long ↔ UTM
- Input fields (dynamic show/hide based on mode)
- Live display: area in km², side lengths, UTM zone (auto label + override button)
- Warning label (red) when area exceeds 50km side

**Page 2 — Configure Layers (`p2_layers.py`)**
- Layer cards (enable/disable toggle + collapsible options):
  - **Terrain**: source selector (3DEP / SRTM / auto)
  - **Buildings**: source selector (OSM / Microsoft / Merge), height handling (flat / rough estimate)
  - **Land Use**: NLCD raster toggle, OSM vector toggle, note about 30m native res
  - **Reference Map**: zoom level selector (auto / manual), tile count estimate

**Page 3 — Output Settings (`p3_output.py`)**
- Project name field
- Output folder picker
- Resolution spinner (meters, default 5.0, min 1.0, max 100.0)
- Output CRS display (auto UTM EPSG code + override dropdown)
- Estimated output size display (updates live)
- Large-area confirmation widget (shown when threshold exceeded)

**Page 4 — Download (`p4_download.py`)**
- Per-layer status rows: icon + label + progress bar + cache-hit badge
- Overall progress bar
- Download log (scrollable QTextEdit)
- Cancel button; Resume-from-cache on re-run

**Page 5 — Preview (`p5_preview.py`)**
- Tab 1 "2D Map": QLabel/QGraphicsView showing stitched reference tiles with bbox overlay; layer toggle checkboxes
- Tab 2 "3D View": disabled tab, grayed label "3D viewer — coming in a future release"

**Page 6 — Export (`p6_export.py`)**
- Checkboxes per available layer
- Format selector per layer type (raster: GeoTIFF; vector: GeoJSON / Shapefile / GeoPackage)
- Output path summary
- Export button + progress

---

### Phase 4 — Download backends

**`downloaders/terrain.py`**
- Primary: `py3dep.get_map()` for CONUS (auto-selects 1m/3m/10m based on area)
- Fallback: `elevation` library for SRTM 30m (global)
- Writes raw GeoTIFF to cache; returns cache path

**`downloaders/buildings.py`**
- OSM: Overpass API query for `building=*`; extracts `height`, `building:height`, `building:levels`
- Microsoft: Azure Blob quad-key tile index lookup → download GeoJSON tiles → clip to bbox
- Merge strategy:
  - Spatial join; where OSM and MS footprints overlap (IoU > 0.5), prefer OSM
  - Where only MS footprint exists, use MS
  - Height priority: explicit tag (m) → levels × 3.5m → user default (flat or dataset mean)

**`downloaders/landuse.py`**
- NLCD: WCS endpoint `https://www.mrlc.gov/geoserver/wcs` → clip to bbox → return GeoTIFF
- OSM: Overpass query for `landuse=*`, `natural=*`, `water=*` → GeoJSON

**`downloaders/reference.py`**
- Auto zoom level: 100km→z12, 10km→z14, 1km→z16
- Fetch tiles from `tile.openstreetmap.org/{z}/{x}/{y}.png` with User-Agent header and rate limiting (1 req/s)
- Stitch with `Pillow`; georeference with `rasterio`
- Cap at 1000 tiles with user warning

---

### Phase 5 — Crop/resample pipeline

**`processing/reproject.py`**
- `reproject_raster(src_path, target_epsg)` → temp GeoTIFF
- `reproject_vector(gdf, target_epsg)` → GeoDataFrame

**`processing/resample.py`**
- `crop_and_resample(src_path, bbox_utm, resolution_m, resampling)` → GeoTIFF
- Resampling methods: Lanczos (terrain/reference), nearest-neighbor (land use categorical)
- Writes to cache; skips if valid cache hit exists

**`processing/merge.py`**
- `merge_building_sources(osm_gdf, ms_gdf, height_mode)` → GeoDataFrame
- Deduplication via spatial index (STRtree) + IoU threshold
- Height assignment logic with `height_mode` enum: `FLAT`, `ESTIMATE`, `MEAN`

---

### Phase 6 — Export

**`export/exporter.py`**
- `export_terrain(path, fmt)` → GeoTIFF (float32 elevation metres, UTM CRS, embedded WKT)
- `export_buildings(path, fmt)` → GeoJSON / Shapefile / GeoPackage with `height_m` attribute
- `export_landuse_raster(path)` → GeoTIFF (int16 categorical + embedded colormap)
- `export_landuse_vector(path, fmt)` → GeoPackage / GeoJSON / Shapefile
- `export_reference(path)` → GeoTIFF (RGB uint8, georeferenced, UTM CRS)
- All outputs include `.prj` sidecar or embedded WKT

---

### Phase 7 — 3D viewer placeholder

- Disabled "3D View" tab in `p5_preview.py`
- `ViewerInterface` ABC in `gui/viewer_interface.py`:
  - `load_terrain(path: str) -> None`
  - `load_buildings(gdf) -> None`
  - `load_landuse(path: str) -> None`
  - `render() -> None`
- Stub `NullViewer(ViewerInterface)` that no-ops all methods (used until real viewer is implemented)
- Intended future backend: PyVista (VTK-based)

---

## Dependency Rationale

| Package | Purpose |
|---|---|
| `PySide6` | GUI framework (Qt6, LGPL) |
| `rasterio` | Raster I/O, reprojection, resampling (wraps GDAL) |
| `geopandas` | Vector data I/O and spatial operations |
| `pyproj` | Coordinate system transforms, UTM zone detection |
| `shapely` | Geometry operations (bbox polygon, IoU calc) |
| `fiona` | Vector file format I/O backend for geopandas |
| `numpy` | Array operations |
| `requests` | HTTP tile fetching |
| `mercantile` | XYZ tile math (bbox → tile list) |
| `Pillow` | Tile image stitching |
| `py3dep` | USGS 3DEP DEM download wrapper |
| `elevation` | SRTM DEM download (global fallback) |
| `overpy` | Overpass API client for OSM queries |
| `diskcache` | Persistent file-based cache |
