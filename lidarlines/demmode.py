import numpy as np
from osgeo import gdal
from pyproj import Transformer
from rdp import rdp
from uavplanner import FlightPlan
from uavplanner.crs import point_to_wgs84
from uavplanner.planner_types import FlightLine, Waypoint
from uavplanner.reader import read_tif

from .crs import point_to_projected
from .reader import Raster


def _sample_tif(r: Raster, x: float, y: float, dsm=False) -> float:
    """x;y is from Waypoint lon lat"""
    row = int((r.y_origin - y ) / r.pixel_height)
    col = int((x - r.x_origin) / r.pixel_width)
    #centervalue = r.data[row][col]
    i = 1 if not dsm else 2
    window = r.data[row-i:row+i+1, col-i:col+i+1]

    return np.nanmax(window)


def _profile_polyline(vertices, r, clearance, epsilon, dsm=False) -> np.ndarray:
    """vertices: list of (x, y) in r.crs -> (m, 3) array of x, y, z"""
    out = []
    for (x0, y0), (x1, y1) in zip(vertices[:-1], vertices[1:]):
        length = np.hypot(x1 - x0, y1 - y0)          # line length in metres
        n = int(np.ceil(length)) + 1
        pts = np.linspace([x0, y0], [x1, y1], n)     # shape (n, 2)
        xs, ys = pts[:, 0], pts[:, 1]
        s = np.linspace(0, length, n)                # lenght along the line
        altitudes = np.zeros(len(xs))
        for i,point in enumerate(zip(xs,ys)):
            x,y = point
            altitudes[i] = _sample_tif(r,x,y,dsm)
        altitudes += clearance # height above ground, can happen at any point tbh

        profile = np.column_stack([s, altitudes])   # shape (n, 2)
        mask = rdp(profile, epsilon=epsilon, return_mask=True)

        seg = np.column_stack([pts[mask], altitudes[mask]]) # (x, y, z)
        # drop the duplicated shared vertex, then collect
        if out:
            seg = seg[1:]
        out.append(seg)
    return np.vstack(out)


def add_heights_to_plan(plan: FlightPlan, r: Raster, dsm=False, epsilon: float = 5) -> FlightPlan:

    assert epsilon>=0

    lines = plan.lines
    clearance = lines[0].start.alt_m + epsilon
    velocity = lines[0].start.velocity
    
    newlines = []

    # set up transformers
    trans_to_projected = Transformer.from_crs(4326, r.crs, always_xy=True)
    trans_to_wgs84 = Transformer.from_crs(r.crs, 4326, always_xy=True)

    def xy(wp: Waypoint) -> tuple:
        x, y = trans_to_projected.transform(wp.lon, wp.lat)
        return x,y
    
    def to_waypoints(arr: np.ndarray) -> list[Waypoint]:
        return_list = []
        for x,y,z in arr:
            lon,lat = trans_to_wgs84.transform(x,y)

            return_list.append(Waypoint(
                lon,lat,round(float(z),2),velocity
            ))
        
        return return_list

    for line, nextline in zip(lines, lines[1:]+[None]):
        mainline_xyz = _profile_polyline([xy(line.start), xy(line.end)], r, clearance, epsilon, dsm)

        if nextline is not None:
            verts = [xy(line.end)] + [xy(wp) for wp in line.transit_to_next] + [xy(nextline.start)]
            transit_xyz = _profile_polyline(verts, r, clearance, epsilon, dsm)
        # which rows of transit_xyz belong in transit_to_next?

        mainline_wps = to_waypoints(mainline_xyz)
        transit_wps = to_waypoints(transit_xyz)

        newlines.append(FlightLine(
            index=line.index,
            start=mainline_wps[0],
            end=mainline_wps[-1],
            between=mainline_wps[1:-1],
            transit_to_next=transit_wps[1:-1]
        ))

    plan.lines = newlines # replace the old lines with new lines
    plan.altitude_mode = "abs" # set the flag so the writer knows
    return plan
        



        




