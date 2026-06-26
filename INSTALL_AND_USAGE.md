# INSTALL_AND_USAGE

This guide takes you from clone to exported GIS deliverables.

## Feature Recap

- Define project extent by corners or centroid+size in Lat/Long or UTM.
- Configure layer-specific options (sources, height mode, raster/vector toggles).
- Run threaded download and processing with per-layer status and logs.
- Preview outputs with layer toggles and statistics before exporting.
- Export terrain, vectors, bounding box, QGIS project files, and a run README.
- Choose export CRS mode: keep source, EPSG:4326, or project UTM zone.

## Installation Guide

### Method A (Recommended): Conda Environment

Use this method if you want the most reliable geospatial dependency setup on Windows.

```bash
conda env create -f environment.yml
conda activate region3d
python -m map_downloader.main
```

Notes:
- The environment file pins the geospatial stack and includes GDAL/PROJ/GEOS dependencies.
- Python version in environment: 3.12.

### Method B (Quick): venv + pip

Use this if you already manage native geospatial dependencies on your system.

```bash
python -m venv .venv
. .venv/Scripts/activate
pip install -r requirements.txt
python -m map_downloader.main
```

Notes:
- On Windows PowerShell, activate using `.\.venv\Scripts\Activate.ps1`.
- If raster/vector libraries fail to import, prefer Method A.

## Usage: Execution

Launch the app:

```bash
python -m map_downloader.main
```

The app opens directly to Step 1 in the wizard.

## Usage: Workflows

### 1) Define Area Precisely

Scenario: You need a strict 5x5 km area in a specific UTM zone and do not want warped edges after reprojection.

Workflow:
1. Go to Step 1: Define Area.
2. Choose input mode: Four Corners or Centroid + Size.
3. Switch coordinate system to UTM.
4. Set the UTM zone (or keep Auto).
5. Enter coordinates/dimensions and proceed.

Example use case:
You are preparing a city block study area and need axis-aligned UTM edges for clean clipping and export boundaries.

### 2) Configure Layer Strategy

Scenario: You want detailed buildings and roads, but only raster land use.

Workflow:
1. Go to Step 2: Configure Layers.
2. Enable desired layers (terrain, buildings, land use, water, streets, reference).
3. For buildings, select source and height mode.
4. For land use, choose raster and/or vector.
5. Continue to Step 3 and set output name/folder/resolution.

Example use case:
You are generating a lightweight planning dataset with footprints and roads, while skipping nonessential layers.

### 3) Run Download + Processing

Scenario: You need a reproducible pipeline run with status visibility and cancellation.

Workflow:
1. Go to Step 4: Download Data.
2. Click Run Download + Processing.
3. Watch overall progress and per-layer panels.
4. Review log messages for cache hits, processing steps, and issues.
5. If needed, click Cancel to stop gracefully.

Example use case:
You start a full-area download, notice wrong settings, cancel the run, adjust Step 2/3, and rerun.

### 4) Preview and Export Deliverables

Scenario: You want to verify layers before exporting a QGIS-ready package.

Workflow:
1. Go to Step 5 and toggle terrain/vector overlays to inspect results.
2. Move to Step 6 and select formats per datatype.
3. Pick Export CRS mode (source, EPSG:4326, or project UTM).
4. Run export and review generated files in the summary.
5. Use Open Export Folder or Open Project Folder buttons.

Example use case:
You export GeoPackage for vector editing, GeoTIFF for terrain, KMZ for quick map sharing, and QGZ for QGIS handoff.

## Development

### Project Structure

```text
RegionDownloadGIS/
  environment.yml
  requirements.txt
  PLAN.md
  map_downloader/
    main.py
    core/
      bbox.py
      cache.py
      project.py
    downloaders/
      base.py
      terrain.py
      buildings.py
      landuse.py
      water.py
      roads.py
      reference.py
    processing/
      reproject.py
      resample.py
      merge.py
    export/
      exporter.py
    gui/
      app.py
      wizard.py
      main_window.py
      viewer_interface.py
      pages/
        p1_bbox.py
        p2_layers.py
        p3_output.py
        p4_download.py
        p5_preview.py
        p6_export.py
      widgets/
        bbox_widget.py
        layer_card.py
        progress_panel.py
  tests/
    test_hardening.py
```

### Key Directories

- map_downloader/core: data models, bbox logic, project serialization, cache helpers.
- map_downloader/downloaders: layer-specific acquisition logic for terrain, vectors, roads, and reference imagery.
- map_downloader/processing: reprojection, clipping/resampling, and merge/augmentation routines.
- map_downloader/gui: wizard pages and reusable widgets for end-user interaction.
- map_downloader/export: final output conversion, format routing, QGIS project generation, and export README generation.
- tests: hardening and regression coverage for bbox/project/export behavior.

### Tests and Style

Run tests:

```bash
python -m unittest tests.test_hardening
```

Run a focused test:

```bash
python -m unittest tests.test_hardening.HardeningTests.test_exporter_can_export_bbox_geojson
```

Style/lint:
- No dedicated linter config is currently committed in this repository.

## Requirements (Core Dependencies)

From environment.yml / requirements.txt:

- Python 3.12
- PySide6 6.7.0
- rasterio 1.3.9
- geopandas 0.14.1
- pyproj 3.6.1
- shapely 2.1.2
- fiona 1.9.6
- numpy 2.4.6
- requests 2.31.0
- mercantile 1.2.1
- Pillow 10.1.0
- py3dep 0.18.0
- overpy 0.4.3
- diskcache 5.6.3
- elevation 1.1.9
- gdal / proj / geos (via conda in recommended setup)
