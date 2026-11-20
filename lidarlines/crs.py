# crs.py
import math

from pyproj import Transformer
from shapely.geometry import Polygon
from shapely.ops import transform


def pick_utm_epsg(polygon_wgs84: Polygon) -> int:
    lon, lat = polygon_wgs84.centroid.x, polygon_wgs84.centroid.y
    utm_zone = math.floor((lon + 180) / 6) + 1
    # Old version was an error in QGIS because repeated proj db lookups crashed qgis.
    # Had something to do with threads
    # WGS84 to UTM zone is: 32600+zone (N), 32700+zone (S)
    base = 32700 if lat < 0 else 32600
    return base + utm_zone


def to_projected(polygon_wgs84: Polygon, epsg: int) -> Polygon:
    transformer = Transformer.from_crs(crs_from=4326, crs_to=epsg, always_xy=True)

    return transform(transformer.transform, polygon_wgs84)


def to_wgs84(polygon_projected: Polygon, epsg: int) -> Polygon:
    transformer = Transformer.from_crs(crs_from=epsg, crs_to=4326, always_xy=True)

    return transform(transformer.transform, polygon_projected)


if __name__ == "__main__":
    from shapely.geometry import Polygon

    # this is lat lon
    # Test BB in Austria should yield -> 32633
    poly = Polygon(
        [
            [15.366475129224398, 48.22059344469096],
            [15.406277198189343, 48.22059344469096],
            [15.406277198189343, 48.20615946200746],
            [15.366475129224398, 48.20615946200746],
            [15.366475129224398, 48.22059344469096],
        ]
    )

    epsg = pick_utm_epsg(poly)
    print(f"UTM EPSG code: {epsg}")

    projected = to_projected(poly, epsg)
    print(projected)

    wgs84 = to_wgs84(projected, epsg)
    print(wgs84)

    # lat lon make sad 😞
