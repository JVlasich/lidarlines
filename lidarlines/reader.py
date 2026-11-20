import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from osgeo import gdal

gdal.UseExceptions()

from shapely.geometry import Polygon, shape

_readers: dict[str, Callable] = {}  # ".geojson" -> reader fn


def register_reader(*extensions: str):
    def decorator(fn):
        for ext in extensions:
            _readers[ext.lower()] = fn
        return fn

    return decorator


def read_polygon(path: str):
    """Dispatch on extension. Always returns a WGS84 (lon, lat) polygon."""
    ext = Path(path).suffix.lower()
    try:
        return _readers[ext](path)
    except KeyError:
        raise ValueError(f"no reader for {ext}; have {list(_readers)}")


@register_reader(".geojson", ".json")
def read_geojson(path: str):

    with open(path, "r") as f:
        data = json.load(f)

    geom = shape(data["features"][0]["geometry"])
    # QGIS layers are often MultiPolygon even for a single polygon. unwrap
    if geom.geom_type == "MultiPolygon":
        polys = list(geom.geoms)
        if len(polys) != 1:
            raise ValueError(
                f"expected a single polygon; got a MultiPolygon with {len(polys)} parts")
        geom = polys[0]
    return geom


# TODO: Space here for future readers (.shp, txt, ...)
@register_reader(".txt")
def read_txt(path: str):
    """Reads a text file with coordinates: lon,lat per line"""
    coords = []

    with open(path, "r") as f:
        for line in f:
            line = line.strip()

            if not line:
                continue

            if line.startswith("#"):
                continue

            lon_str, lat_str = line.split(",")

            lon = float(lon_str.strip())
            lat = float(lat_str.strip())

            coords.append((lon, lat))

    if coords[0] != coords[-1]:
        coords.append(coords[0])

    return Polygon(coords)

# not a polygon reader no decorator
@dataclass
class Raster:
    band: gdal.Band
    cols: int
    rows: int
    transform: tuple[float, float, float, float, float, float]
    x_origin: float
    y_origin: float
    pixel_width: float
    pixel_height: float
    data: np.ndarray
    nodata: float
    crs: int # epsg


def read_tif(path: str | Path) -> Raster:
    dataset = gdal.Open(str(path))
    band = dataset.GetRasterBand(1)
    transform = dataset.GetGeoTransform()

    data = band.ReadAsArray()
    nodata = band.GetNoDataValue()
    data[data==nodata]=np.nan

    srs = dataset.GetSpatialRef()
    if srs is None:
        raise ValueError("No CRS defined")
    else:
        code = srs.GetAuthorityCode(None)
        if code is None:
            try:
                srs.AutoIdentifyEPSG()
                code = srs.GetAuthorityCode(None)
            except RuntimeError:
                pass
        # print(srs.GetAuthorityName(None))
        # print(code)
        # print(srs.GetName())

    return Raster(
        band=band,
        cols=dataset.RasterXSize,
        rows=dataset.RasterYSize,
        transform=transform,
        x_origin=transform[0],
        y_origin=transform[3],
        pixel_width=transform[1],
        pixel_height=-transform[5],
        data=data,
        nodata=nodata,
        crs=code
    )