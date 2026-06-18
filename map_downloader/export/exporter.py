"""Export processed GIS outputs to standard file formats."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from html import escape
from pathlib import Path
from shutil import copy2
from typing import Dict, List, Optional, Sequence
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import geopandas as gpd
import numpy as np
import rasterio
from shapely.geometry import box

from map_downloader.core.bbox import BoundingBox
from map_downloader.processing.reproject import get_utm_epsg


DRIVER_MAP = {
    "GeoJSON": (".geojson", "GeoJSON"),
    "Shapefile": (".shp", "ESRI Shapefile"),
    "GeoPackage": (".gpkg", "GPKG"),
}


@dataclass
class ExportResult:
    """Summary of one export run."""

    success: bool
    files: List[str] = field(default_factory=list)
    messages: List[str] = field(default_factory=list)


class Exporter:
    """Export project artifacts into user-selected GIS formats."""

    def __init__(self, output_root: Path):
        self.output_root = Path(output_root)
        self.export_dir = self.output_root / "exports"
        self.export_dir.mkdir(parents=True, exist_ok=True)

    def export(
        self,
        terrain_formats: Sequence[str],
        buildings_formats: Sequence[str],
        landuse_formats: Sequence[str],
        water_formats: Sequence[str],
        big_streets_formats: Sequence[str],
        small_streets_formats: Sequence[str],
        reference_formats: Sequence[str],
        bbox_formats: Sequence[str],
        qgis_project_formats: Sequence[str],
        bbox: Optional[BoundingBox] = None,
    ) -> ExportResult:
        """Run export using current UI selections."""
        result = ExportResult(success=True)

        terrain_formats = self._normalize_formats(terrain_formats)
        buildings_formats = self._normalize_formats(buildings_formats)
        landuse_formats = self._normalize_formats(landuse_formats)
        water_formats = self._normalize_formats(water_formats)
        big_streets_formats = self._normalize_formats(big_streets_formats)
        small_streets_formats = self._normalize_formats(small_streets_formats)
        reference_formats = self._normalize_formats(reference_formats)
        bbox_formats = self._normalize_formats(bbox_formats)
        qgis_project_formats = self._normalize_formats(qgis_project_formats)

        if terrain_formats:
            self._export_terrain(result, terrain_formats)

        if buildings_formats:
            self._export_buildings(result, buildings_formats)

        if landuse_formats:
            self._export_landuse(result, landuse_formats)

        if water_formats:
            self._export_water(result, water_formats)

        if big_streets_formats:
            self._export_big_streets(result, big_streets_formats)

        if small_streets_formats:
            self._export_small_streets(result, small_streets_formats)

        if reference_formats:
            self._export_reference(result, reference_formats)

        if bbox_formats:
            self._export_bbox(result, bbox_formats, bbox)

        if qgis_project_formats:
            self._export_qgis_project(result, qgis_project_formats, bbox)

        if not result.files:
            result.success = False
            result.messages.append("No matching source files were found to export.")

        return result

    def _normalize_formats(self, formats: Sequence[str] | str | None) -> List[str]:
        if formats is None:
            return []
        if isinstance(formats, str):
            values = [formats]
        else:
            values = list(formats)

        seen = set()
        out: List[str] = []
        for value in values:
            if not value or value in seen:
                continue
            seen.add(value)
            out.append(value)
        return out

    def _candidate_dirs(self) -> List[Path]:
        return [
            self.output_root / "processed",
            self.output_root / "downloads",
            self.output_root,
        ]

    def _find_first(self, patterns: List[str]) -> Optional[Path]:
        for folder in self._candidate_dirs():
            if not folder.exists():
                continue
            for pattern in patterns:
                matches = sorted(folder.glob(pattern))
                if matches:
                    return matches[0]
        return None

    def _copy_raster(self, src: Path, stem: str, result: ExportResult) -> None:
        dst = self._next_available_path(stem, ".tif")
        copy2(src, dst)
        result.files.append(str(dst))
        result.messages.append(f"Exported raster: {dst.name}")

    def _export_raster_image(self, src: Path, stem: str, image_format: str, result: ExportResult) -> None:
        suffix = ".png" if image_format == "PNG" else ".jpg"
        dst = self._next_available_path(stem, suffix)
        try:
            from PIL import Image
        except Exception:
            result.messages.append(
                f"Skipped {stem} {image_format}: Pillow is not available in this environment."
            )
            return

        with rasterio.open(src) as ds:
            bands = ds.count
            if bands <= 0:
                result.messages.append(f"Skipped {stem} {image_format}: source raster has no bands.")
                return

            if bands == 1:
                arr = ds.read(1)
                arr_u8 = self._to_uint8(arr)
                image = Image.fromarray(arr_u8, mode="L")
            else:
                read_count = min(3, bands)
                arr = ds.read(list(range(1, read_count + 1)))
                arr_u8 = np.stack([self._to_uint8(arr[i]) for i in range(read_count)], axis=-1)
                if read_count == 1:
                    image = Image.fromarray(arr_u8[:, :, 0], mode="L")
                else:
                    image = Image.fromarray(arr_u8, mode="RGB")

            if image_format == "JPG":
                if image.mode not in ("RGB", "L"):
                    image = image.convert("RGB")
                image.save(dst, format="JPEG", quality=95)
            else:
                image.save(dst, format="PNG")

        result.files.append(str(dst))
        result.messages.append(f"Exported image: {dst.name}")

    def _to_uint8(self, arr: np.ndarray) -> np.ndarray:
        arr = np.asarray(arr)
        finite = np.isfinite(arr)
        if not finite.any():
            return np.zeros(arr.shape, dtype=np.uint8)
        vals = arr[finite]
        vmin = float(vals.min())
        vmax = float(vals.max())
        if vmax <= vmin:
            out = np.zeros(arr.shape, dtype=np.uint8)
            out[finite] = 255
            return out
        scaled = (arr - vmin) / (vmax - vmin)
        scaled = np.clip(scaled, 0.0, 1.0)
        scaled[~finite] = 0.0
        return (scaled * 255.0).astype(np.uint8)

    def _convert_vector(
        self,
        src: Path,
        stem: str,
        target_format: str,
        result: ExportResult,
    ) -> None:
        suffix, driver = DRIVER_MAP[target_format]
        dst = self._next_available_path(stem, suffix)
        gdf = gpd.read_file(src)
        gdf.to_file(dst, driver=driver)
        result.files.append(str(dst))
        result.messages.append(f"Exported vector: {dst.name}")

    def _export_vector_kml_like(self, src: Path, stem: str, as_kmz: bool, result: ExportResult) -> None:
        try:
            gdf = gpd.read_file(src)
        except Exception as exc:
            result.messages.append(f"Skipped {stem}: failed to read source for KML/KMZ ({exc}).")
            return

        if len(gdf) == 0 or "geometry" not in gdf.columns:
            result.messages.append(f"Skipped {stem}: no geometries available for KML/KMZ.")
            return

        try:
            if gdf.crs is not None and str(gdf.crs) != "EPSG:4326":
                gdf = gdf.to_crs(epsg=4326)
        except Exception:
            pass

        placemarks: List[str] = []
        for idx, row in gdf.iterrows():
            geom = row.geometry
            kml_geom = self._geometry_to_kml(geom)
            if not kml_geom:
                continue
            name_val = ""
            if "name" in row and row["name"] not in (None, ""):
                name_val = str(row["name"])
            else:
                name_val = f"{stem}_{idx + 1}"
            placemarks.append(
                "    <Placemark>\n"
                f"      <name>{escape(name_val)}</name>\n"
                f"      {kml_geom}\n"
                "    </Placemark>"
            )

        if not placemarks:
            result.messages.append(f"Skipped {stem}: no supported geometries for KML/KMZ.")
            return

        kml_text = (
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
            "<kml xmlns=\"http://www.opengis.net/kml/2.2\">\n"
            "  <Document>\n"
            f"    <name>{escape(stem)}</name>\n"
            + "\n".join(placemarks)
            + "\n  </Document>\n"
            "</kml>\n"
        )

        if as_kmz:
            dst = self._next_available_path(stem, ".kmz")
            with ZipFile(dst, "w", ZIP_DEFLATED) as zf:
                zf.writestr("doc.kml", kml_text)
            result.files.append(str(dst))
            result.messages.append(f"Exported vector: {dst.name}")
            return

        dst = self._next_available_path(stem, ".kml")
        dst.write_text(kml_text, encoding="utf-8")
        result.files.append(str(dst))
        result.messages.append(f"Exported vector: {dst.name}")

    def _coords_to_kml(self, coords) -> str:
        return " ".join(f"{x},{y},0" for x, y in coords)

    def _polygon_to_kml(self, geom) -> str:
        outer = self._coords_to_kml(list(geom.exterior.coords))
        inner_xml = ""
        for ring in geom.interiors:
            inner = self._coords_to_kml(list(ring.coords))
            inner_xml += (
                "<innerBoundaryIs><LinearRing><coordinates>"
                f"{inner}"
                "</coordinates></LinearRing></innerBoundaryIs>"
            )
        return (
            "<Polygon><outerBoundaryIs><LinearRing><coordinates>"
            f"{outer}"
            "</coordinates></LinearRing></outerBoundaryIs>"
            f"{inner_xml}"
            "</Polygon>"
        )

    def _geometry_to_kml(self, geom) -> Optional[str]:
        if geom is None or geom.is_empty:
            return None
        gtype = geom.geom_type

        if gtype == "Point":
            x, y = geom.x, geom.y
            return f"<Point><coordinates>{x},{y},0</coordinates></Point>"
        if gtype == "LineString":
            return f"<LineString><coordinates>{self._coords_to_kml(list(geom.coords))}</coordinates></LineString>"
        if gtype == "Polygon":
            return self._polygon_to_kml(geom)
        if gtype == "MultiPoint":
            parts = [self._geometry_to_kml(part) for part in geom.geoms]
            parts = [p for p in parts if p]
            return f"<MultiGeometry>{''.join(parts)}</MultiGeometry>" if parts else None
        if gtype == "MultiLineString":
            parts = [self._geometry_to_kml(part) for part in geom.geoms]
            parts = [p for p in parts if p]
            return f"<MultiGeometry>{''.join(parts)}</MultiGeometry>" if parts else None
        if gtype == "MultiPolygon":
            parts = [self._geometry_to_kml(part) for part in geom.geoms]
            parts = [p for p in parts if p]
            return f"<MultiGeometry>{''.join(parts)}</MultiGeometry>" if parts else None
        return None

    def _write_vector_gdf(
        self,
        gdf: gpd.GeoDataFrame,
        stem: str,
        target_format: str,
        result: ExportResult,
    ) -> None:
        suffix, driver = DRIVER_MAP[target_format]
        dst = self._next_available_path(stem, suffix)
        gdf.to_file(dst, driver=driver)
        result.files.append(str(dst))
        result.messages.append(f"Exported vector: {dst.name}")

    def _next_available_path(self, stem: str, suffix: str) -> Path:
        """Return a writable export path, preserving existing files."""
        base = self.export_dir / f"{stem}{suffix}"
        if not base.exists():
            return base

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        candidate = self.export_dir / f"{stem}_{stamp}{suffix}"
        if not candidate.exists():
            return candidate

        counter = 2
        while True:
            candidate = self.export_dir / f"{stem}_{stamp}_{counter}{suffix}"
            if not candidate.exists():
                return candidate
            counter += 1

    def _export_terrain(self, result: ExportResult, formats: Sequence[str]) -> None:
        if "GeoTIFF" not in formats:
            result.messages.append("Skipped terrain: only GeoTIFF format is supported.")
            return

        src = self._find_first(["terrain*.tif", "*terrain*.tif"])
        if src is None:
            result.messages.append("Skipped terrain: source file not found.")
            return
        self._copy_raster(src, "terrain", result)

    def _export_buildings(self, result: ExportResult, target_formats: Sequence[str]) -> None:
        src = self._find_first(["buildings*.geojson", "*buildings*.geojson", "buildings*.gpkg", "*.shp"])
        if src is None:
            result.messages.append("Skipped buildings: source file not found.")
            return

        for target_format in target_formats:
            if target_format == "KML":
                self._export_vector_kml_like(src, "buildings", as_kmz=False, result=result)
                continue
            if target_format == "KMZ":
                self._export_vector_kml_like(src, "buildings", as_kmz=True, result=result)
                continue
            if target_format not in DRIVER_MAP:
                result.messages.append(f"Skipped buildings: unsupported format {target_format}.")
                continue
            self._convert_vector(src, "buildings", target_format, result)

    def _export_landuse(self, result: ExportResult, target_formats: Sequence[str]) -> None:
        raster_src = self._find_first(["nlcd*.tif", "landuse*.tif", "*landuse*raster*.tif"])
        vector_src = self._find_first(["landuse*.geojson", "*landuse*vector*.geojson", "*.gpkg", "*.shp"])

        for target_format in target_formats:
            if target_format == "GeoTIFF":
                if raster_src is not None:
                    self._copy_raster(raster_src, "landuse", result)
                else:
                    result.messages.append("Skipped land use raster: source file not found.")
                continue

            if target_format in DRIVER_MAP:
                if vector_src is not None:
                    self._convert_vector(vector_src, "landuse", target_format, result)
                else:
                    result.messages.append("Skipped land use vector: source file not found.")
                continue

            result.messages.append(f"Skipped land use: unsupported format {target_format}.")

    def _export_reference(self, result: ExportResult, formats: Sequence[str]) -> None:
        src_main = self._find_first(["reference.tif", "*reference.tif"])
        src_context = self._find_first(["reference_context.tif", "*reference_context.tif"])

        if src_main is None and src_context is None:
            result.messages.append("Skipped reference: source file not found.")
            return

        for target_format in formats:
            if target_format == "GeoTIFF":
                if src_main is not None:
                    self._copy_raster(src_main, "reference", result)
                if src_context is not None:
                    self._copy_raster(src_context, "reference_context", result)
                continue

            if target_format in ("PNG", "JPG"):
                if src_main is not None:
                    self._export_raster_image(src_main, "reference", target_format, result)
                if src_context is not None:
                    self._export_raster_image(src_context, "reference_context", target_format, result)
                continue

            result.messages.append(f"Skipped reference: unsupported format {target_format}.")

    def _export_water(self, result: ExportResult, target_formats: Sequence[str]) -> None:
        src = self._find_first(["water*.geojson", "*water*.geojson", "water*.gpkg", "water*.shp"])
        if src is None:
            result.messages.append("Skipped water: source file not found.")
            return

        for target_format in target_formats:
            if target_format == "KML":
                self._export_vector_kml_like(src, "water", as_kmz=False, result=result)
                continue
            if target_format == "KMZ":
                self._export_vector_kml_like(src, "water", as_kmz=True, result=result)
                continue
            if target_format not in DRIVER_MAP:
                result.messages.append(f"Skipped water: unsupported format {target_format}.")
                continue
            self._convert_vector(src, "water", target_format, result)

    def _export_big_streets(self, result: ExportResult, target_formats: Sequence[str]) -> None:
        src = self._find_first(["big_streets*.geojson", "*big_streets*.geojson", "roads_major*.geojson", "*roads_major*.geojson"])
        if src is None:
            result.messages.append("Skipped big streets: source file not found.")
            return

        for target_format in target_formats:
            if target_format == "KML":
                self._export_vector_kml_like(src, "big_streets", as_kmz=False, result=result)
                continue
            if target_format == "KMZ":
                self._export_vector_kml_like(src, "big_streets", as_kmz=True, result=result)
                continue
            if target_format not in DRIVER_MAP:
                result.messages.append(f"Skipped big streets: unsupported format {target_format}.")
                continue
            self._convert_vector(src, "big_streets", target_format, result)

    def _export_small_streets(self, result: ExportResult, target_formats: Sequence[str]) -> None:
        src = self._find_first(["small_streets*.geojson", "*small_streets*.geojson", "roads_minor*.geojson", "*roads_minor*.geojson"])
        if src is None:
            result.messages.append("Skipped small streets: source file not found.")
            return

        for target_format in target_formats:
            if target_format == "KML":
                self._export_vector_kml_like(src, "small_streets", as_kmz=False, result=result)
                continue
            if target_format == "KMZ":
                self._export_vector_kml_like(src, "small_streets", as_kmz=True, result=result)
                continue
            if target_format not in DRIVER_MAP:
                result.messages.append(f"Skipped small streets: unsupported format {target_format}.")
                continue
            self._convert_vector(src, "small_streets", target_format, result)

    def _export_bbox(
        self,
        result: ExportResult,
        target_formats: Sequence[str],
        bbox: Optional[BoundingBox],
    ) -> None:
        if bbox is None:
            result.messages.append("Skipped bounding box: project bbox is unavailable.")
            return

        geom = box(bbox.min_lon, bbox.min_lat, bbox.max_lon, bbox.max_lat)
        gdf = gpd.GeoDataFrame(
            [{"name": "bounding_box"}],
            geometry=[geom],
            crs="EPSG:4326",
        )

        for target_format in target_formats:
            if target_format == "KML":
                self._write_bbox_kml(result, geom, as_kmz=False)
                continue
            if target_format == "KMZ":
                self._write_bbox_kml(result, geom, as_kmz=True)
                continue
            if target_format not in DRIVER_MAP:
                result.messages.append(f"Skipped bounding box: unsupported format {target_format}.")
                continue
            self._write_vector_gdf(gdf, "bounding_box", target_format, result)

    def _write_bbox_kml(self, result: ExportResult, geom, as_kmz: bool) -> None:
        coords = list(geom.exterior.coords)
        coord_text = " ".join(f"{x},{y},0" for x, y in coords)
        kml_text = (
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
            "<kml xmlns=\"http://www.opengis.net/kml/2.2\">\n"
            "  <Document>\n"
            "    <Placemark>\n"
            "      <name>bounding_box</name>\n"
            "      <Polygon>\n"
            "        <outerBoundaryIs><LinearRing><coordinates>"
            f"{coord_text}"
            "</coordinates></LinearRing></outerBoundaryIs>\n"
            "      </Polygon>\n"
            "    </Placemark>\n"
            "  </Document>\n"
            "</kml>\n"
        )

        if as_kmz:
            dst = self._next_available_path("bounding_box", ".kmz")
            with ZipFile(dst, "w", ZIP_DEFLATED) as zf:
                zf.writestr("doc.kml", kml_text)
            result.files.append(str(dst))
            result.messages.append(f"Exported vector: {dst.name}")
            return

        dst = self._next_available_path("bounding_box", ".kml")
        dst.write_text(kml_text, encoding="utf-8")
        result.files.append(str(dst))
        result.messages.append(f"Exported vector: {dst.name}")

    def _export_qgis_project(
        self,
        result: ExportResult,
        formats: Sequence[str],
        bbox: Optional[BoundingBox],
    ) -> None:
        wants_qgs = "QGS" in formats
        wants_qgz = "QGZ" in formats
        if not wants_qgs and not wants_qgz:
            result.messages.append("Skipped QGIS project: unsupported format selection.")
            return

        layer_candidates = [Path(p) for p in result.files if Path(p).exists() and Path(p).suffix.lower() != ".qgs"]
        if not layer_candidates:
            # Fallback to core processed outputs if user requested only QGS.
            for name in [
                "terrain.tif",
                "buildings.geojson",
                "landuse.tif",
                "landuse.geojson",
                "water.geojson",
                "big_streets.geojson",
                "small_streets.geojson",
                "reference.tif",
            ]:
                p = self.output_root / "processed" / name
                if p.exists():
                    layer_candidates.append(p)

        if not layer_candidates:
            result.messages.append("Skipped QGIS project: no layers available to include.")
            return

        authid = self._infer_project_authid(layer_candidates, bbox)
        qgs_text = self._build_qgs_text(layer_candidates, authid)
        if not qgs_text:
            result.messages.append("Skipped QGIS project: no supported layer files found.")
            return

        if wants_qgs:
            dst_qgs = self._next_available_path("region3d_project", ".qgs")
            dst_qgs.write_text(qgs_text, encoding="utf-8")
            result.files.append(str(dst_qgs))
            result.messages.append(f"Exported QGIS project: {dst_qgs.name}")

        if wants_qgz:
            dst_qgz = self._next_available_path("region3d_project", ".qgz")
            with ZipFile(dst_qgz, "w", ZIP_DEFLATED) as zf:
                zf.writestr("project.qgs", qgs_text)
            result.files.append(str(dst_qgz))
            result.messages.append(f"Exported QGIS project: {dst_qgz.name}")

    def _infer_project_authid(self, files: Sequence[Path], bbox: Optional[BoundingBox]) -> str:
        for path in files:
            suffix = path.suffix.lower()
            try:
                if suffix in (".tif", ".tiff"):
                    with rasterio.open(path) as ds:
                        if ds.crs is not None:
                            epsg = ds.crs.to_epsg()
                            if epsg:
                                return f"EPSG:{epsg}"
                            crs_text = str(ds.crs)
                            if crs_text.startswith("EPSG:"):
                                return crs_text
                elif suffix in (".geojson", ".gpkg", ".shp", ".kml", ".kmz"):
                    gdf = gpd.read_file(path)
                    if gdf.crs is not None:
                        epsg = gdf.crs.to_epsg()
                        if epsg:
                            return f"EPSG:{epsg}"
                        crs_text = str(gdf.crs)
                        if crs_text.startswith("EPSG:"):
                            return crs_text
            except Exception:
                continue

        if bbox is not None:
            try:
                return get_utm_epsg(bbox)
            except Exception:
                pass
        return "EPSG:4326"

    def _build_qgs_text(self, files: Sequence[Path], authid: str) -> str:
        selected = self._select_qgis_layers(files)
        if not selected:
            return ""

        map_layers: List[str] = []
        group_entries: List[str] = []
        authid_xml = escape(authid)

        for layer in selected:
            path = layer["path"]
            layer_id = uuid4().hex
            layer_name = layer["label"]
            checked = "Qt::Checked" if layer["checked"] else "Qt::Unchecked"
            data_source = escape(path.resolve().as_posix())
            name_xml = escape(layer_name)
            id_xml = escape(layer_id)
            group_name = escape(layer["group"])

            group_entries.append(
                "    <layer-tree-group checked=\"Qt::Checked\" expanded=\"1\" "
                f"name=\"{group_name}\">\n"
                f"      <layer-tree-layer id=\"{id_xml}\" checked=\"{checked}\" name=\"{name_xml}\" expanded=\"1\"/>\n"
                "    </layer-tree-group>"
            )

            map_layers.append(
                "  <maplayer "
                f"type=\"{layer['layer_type']}\" autoRefreshTime=\"0\" hasScaleBasedVisibilityFlag=\"0\">\n"
                f"    <id>{id_xml}</id>\n"
                f"    <layername>{name_xml}</layername>\n"
                f"    <datasource>{data_source}</datasource>\n"
                f"    <provider encoding=\"UTF-8\">{layer['provider']}</provider>\n"
                "    <srs>\n"
                "      <spatialrefsys>\n"
                f"        <authid>{authid_xml}</authid>\n"
                "      </spatialrefsys>\n"
                "    </srs>\n"
                "  </maplayer>"
            )

        authid_xml = escape(authid)
        return (
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
            "<qgis projectname=\"Region3D Export\" version=\"3.34.0\">\n"
            "  <homePath path=\".\"/>\n"
            "  <title>Region3D Export</title>\n"
            "  <projectCrs>\n"
            "    <spatialrefsys>\n"
            f"      <authid>{authid_xml}</authid>\n"
            "    </spatialrefsys>\n"
            "  </projectCrs>\n"
            "  <layer-tree-group checked=\"Qt::Checked\" expanded=\"1\" name=\"Datatypes\">\n"
            + "\n".join(group_entries)
            + "\n  </layer-tree-group>\n"
            "  <projectlayers>\n"
            + "\n".join(map_layers)
            + "\n  </projectlayers>\n"
            "</qgis>\n"
        )

    def _select_qgis_layers(self, files: Sequence[Path]) -> List[Dict[str, object]]:
        """Select one preferred file format per datatype for cleaner QGIS projects."""
        datatype_order = [
            "bounding_box",
            "buildings",
            "big_streets",
            "small_streets",
            "water",
            "landuse",
            "reference",
            "reference_context",
            "terrain",
        ]
        datatype_labels = {
            "bounding_box": "Bounding Box",
            "buildings": "Buildings",
            "big_streets": "Big Streets",
            "small_streets": "Small Streets",
            "water": "Water",
            "landuse": "Land Use",
            "reference": "Reference",
            "reference_context": "Reference Context",
            "terrain": "Terrain",
        }
        format_preference = {
            "terrain": [".tif", ".tiff"],
            "landuse": [".tif", ".tiff", ".gpkg", ".geojson", ".shp", ".kmz", ".kml"],
            "buildings": [".gpkg", ".geojson", ".shp", ".kmz", ".kml"],
            "water": [".gpkg", ".geojson", ".shp", ".kmz", ".kml"],
            "big_streets": [".gpkg", ".geojson", ".shp", ".kmz", ".kml"],
            "small_streets": [".gpkg", ".geojson", ".shp", ".kmz", ".kml"],
            "bounding_box": [".gpkg", ".geojson", ".shp", ".kmz", ".kml"],
            "reference": [".tif", ".tiff", ".png", ".jpg", ".jpeg"],
            "reference_context": [".tif", ".tiff", ".png", ".jpg", ".jpeg"],
        }

        grouped: Dict[str, Dict[str, Path]] = {k: {} for k in datatype_order}

        for path in files:
            dtype = self._infer_qgis_datatype(path)
            if dtype is None:
                continue
            suffix = path.suffix.lower()
            if suffix not in grouped[dtype]:
                grouped[dtype][suffix] = path

        out: List[Dict[str, object]] = []
        for dtype in datatype_order:
            options = grouped.get(dtype, {})
            if not options:
                continue

            chosen = None
            for ext in format_preference.get(dtype, []):
                if ext in options:
                    chosen = options[ext]
                    break
            if chosen is None:
                chosen = next(iter(options.values()))

            suffix = chosen.suffix.lower()
            if suffix in (".tif", ".tiff", ".png", ".jpg", ".jpeg"):
                provider = "gdal"
                layer_type = "raster"
            elif suffix in (".geojson", ".gpkg", ".shp", ".kml", ".kmz"):
                provider = "ogr"
                layer_type = "vector"
            else:
                continue

            checked = dtype not in ("reference", "reference_context")
            out.append(
                {
                    "path": chosen,
                    "group": datatype_labels[dtype],
                    "label": chosen.stem,
                    "checked": checked,
                    "provider": provider,
                    "layer_type": layer_type,
                }
            )

        return out

    def _infer_qgis_datatype(self, path: Path) -> Optional[str]:
        stem = path.stem.lower()
        if stem.startswith("terrain"):
            return "terrain"
        if stem.startswith("buildings"):
            return "buildings"
        if stem.startswith("landuse") or stem.startswith("nlcd"):
            return "landuse"
        if stem.startswith("water"):
            return "water"
        if stem.startswith("big_streets") or stem.startswith("roads_major"):
            return "big_streets"
        if stem.startswith("small_streets") or stem.startswith("roads_minor"):
            return "small_streets"
        if stem.startswith("bounding_box"):
            return "bounding_box"
        if stem.startswith("reference_context"):
            return "reference_context"
        if stem.startswith("reference"):
            return "reference"
        return None
