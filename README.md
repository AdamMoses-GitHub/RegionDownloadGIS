# [PROJECT_NAME]

<!-- TODO: Replace [PROJECT_NAME] with your final project name -->

*Because hand-assembling GIS layers from six sources is a great way to spend a weekend you never get back.*

![Version](https://img.shields.io/badge/version-local--dev-blue)
![Language](https://img.shields.io/badge/language-Python%203.12-3776AB)
![License](https://img.shields.io/badge/license-MIT-green)

![App Screenshot](INSERT_IMAGE_URL_HERE)

## About

Working with regional GIS data usually means juggling terrain, vector layers, coordinate systems, and export formats across multiple tools. It is easy to lose time on setup friction, format mismatches, and reprojection surprises instead of actual analysis.

This project wraps that workflow into a guided desktop wizard: define your area, pick layers, run download and processing, inspect results, and export deliverables for QGIS or downstream tools. It keeps practical defaults while still giving you control over CRS and output formats.

Repository: [GITHUB_URL]

<!-- TODO: Replace [GITHUB_URL] with the repository URL -->

## What It Does

### The Main Features
- Guided 6-step desktop workflow from area definition to export.
- Bounding box input in either Lat/Long (WGS84) or UTM, with strict UTM rectangle preservation.
- Layer-by-layer data acquisition for terrain, buildings, land use, water, major streets, minor streets, and reference imagery.
- Background download and processing pipeline with per-layer progress, run status, and cancellation.
- Preview page with raster and vector overlays, including vector-only fallback when terrain is off.
- Multi-format export with optional export CRS mode, plus QGS/QGZ project generation and run-level README export.

### The Nerdy Stuff
- Reprojection and clipping pipeline for both raster and vector datasets.
- CRS-aware export strategy with source-preserving mode, EPSG:4326 mode, and project UTM mode.
- KML/KMZ handling that always outputs EPSG:4326-compatible geometry.
- Per-layer CRS detection for QGIS project writing to avoid mixed-layer misplacement.
- Cache-backed download flow for faster repeat runs.

## Quick Start (TL;DR)

Full setup and workflow guide: [INSTALL_AND_USAGE.md](INSTALL_AND_USAGE.md)

```bash
git clone [GITHUB_URL]
cd RegionDownloadGIS
conda env create -f environment.yml
conda activate region3d
python -m map_downloader.main
```

## Tech Stack

| Component | Purpose | Why This One |
|---|---|---|
| Python 3.12 | Core runtime | Broad geospatial ecosystem support and fast iteration speed. |
| PySide6 | Desktop GUI wizard | Native-feeling cross-platform UI with Qt widgets. |
| Rasterio + GDAL | Raster I/O, reprojection, and warping | Reliable geospatial raster processing with mature CRS handling. |
| GeoPandas + Fiona + Shapely | Vector read/write and geometry operations | Practical high-level APIs over battle-tested geospatial primitives. |
| PyProj | CRS transforms | Accurate coordinate transformations and EPSG interoperability. |
| NumPy | Raster and array operations | Efficient numeric processing for image/raster workflows. |
| Pillow | PNG/JPG image export | Simple and dependable raster-to-image conversion path. |
| Requests + OverPy + Mercantile + py3dep | Data acquisition helpers | Direct support for web, OSM, tiles, and elevation sources. |
| DiskCache | Download/output caching | Reduces redundant fetches and improves rerun speed. |

## License

MIT (default). Update this section if your repo uses a different license.

## Contributing

Pull requests are welcome. Keep changes focused, include tests for behavior changes, and document any new workflow steps.

<sub>gis, geospatial, region downloader, map data, pyqt, pyside6, qgis, geojson, geopackage, shapefile, kml, kmz, geotiff, raster processing, vector processing, reprojection, utm, epsg4326, terrain, openstreetmap</sub>
