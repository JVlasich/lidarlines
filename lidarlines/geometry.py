# geometry.py
from typing import List, Tuple

from shapely.affinity import rotate
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import substring

#from .routing import route_transits


def generate_flight_lines(
    polygon_proj: Polygon, spacing_m: float, azimuth_deg: float, lead_in_m: float = 0.0
) -> list[LineString]:
    origin = (polygon_proj.centroid.x, polygon_proj.centroid.y)
    rotated = _rotate(polygon_proj, azimuth_deg, origin)
    candidates = _slice_bbox(rotated, spacing_m)
    segments = _clip_to_polygon(candidates, rotated, lead_in_m)
    ordered = _order_segments(segments)
    # rotate back into the projected CRS
    return [rotate(seg, 90 - azimuth_deg, origin=origin) for seg in ordered]


def best_azimuth(polygon_proj: Polygon, spacing_m: float, lead_in_m: float = 0.0) -> float:
    """Azimuth (deg) that gives the shortest path: flight lines plus
    straight transits"""
    def cost(az): # helper Function used as key to sort the different azimuths
        """The cost of one Azimuth is the total lenght of the flight lines + transits"""
        lines = generate_flight_lines(polygon_proj, spacing_m, az, lead_in_m)
        if not lines:
            return float("inf")
            # its a best effort guess so no extra compute from restricted
        transits = route_transits(lines, polygon_proj, restricted=False)
        return sum(line.length for line in lines) + sum(t.length for t in transits)

    # sweep with 10 deg steps
    coarse = min(range(0, 180, 10), key=cost)
    # +-10 fine sweep around coarse result
    fine = float(min(range(coarse - 10, coarse + 11, 2), key=cost))
    return fine


def _rotate(geom, azimuth_deg, origin):
    """Rotate so the flight direction lands on the x-axis."""
    # Shapely rotates counterclockwise, azimuth is clockwise from north -> -90
    return rotate(geom, azimuth_deg - 90, origin=origin)


def _slice_bbox(polygon, spacing_m) -> list[LineString]:
    """Horizontal lines across the bbox, spaced spacing_m apart."""
    minx, miny, maxx, maxy = polygon.bounds
    lines = []
    # first offset by spacing_m/2 -> none sits exactly on the edge.
    y = miny + spacing_m / 2
    while y < maxy:
        # adds lines until height is above the polygon
        lines.append(LineString([(minx - 1, y), (maxx + 1, y)]))
        y += spacing_m
    return lines


def _clip_to_polygon(lines: list[LineString], polygon, lead_in_m) -> list[LineString]:
    """Keep the parts inside the polygon, left-to-right,
    extended by lead_in_m on both ends."""
    segments = []
    for line in lines:
        clipped = line.intersection(polygon)
        parts = getattr(clipped, "geoms", [clipped])
        for part in parts:
            if not isinstance(part, LineString) or part.length == 0:
                continue
            (x0, y), (x1, _) = part.coords[0], part.coords[-1] # same y
            if x0 > x1:
                x0, x1 = x1, x0
            segments.append(LineString([(x0 - lead_in_m, y), (x1 + lead_in_m, y)]))
    return segments


def _order_segments(segments) -> list[LineString]:
    """Sweep each connected cell of the AOI completely before moving to
    the next. Direction alternates every row within a cell."""
    rows = _group_rows(segments)
    cells = _build_cells(rows)
    # decide which cell to fly first
    # c[0] first line of the cell, bottom
    # sorts by y coordinate of cell, if y's equal then x decides (left first)
    cells.sort(key=lambda c: (c[0].coords[0][1], c[0].coords[0][0]))

    ordered = []
    for cell in cells:
        # invert each second line so drone path is coherent
        for i, seg in enumerate(cell):
            ordered.append(LineString(seg.coords[::-1]) if i % 2 else seg)
    return ordered


def _group_rows(segments) -> list[list[LineString]]:
    """Segments bucketed by sweep offset, bottom row first, each row
    sorted along the flight direction."""
    rows: dict[float, list[LineString]] = {} # key = y coord, value = list of line segments
    for seg in segments:
        # creates an entry for the y coord if not exist otherwhise returns the value
        # after that appends the segment
        rows.setdefault(round(seg.coords[0][1], 6), []).append(seg)
    # sorts by y and then by x again
    # returns a nested list, outside: sorted by y, inside: segments left to right
    return [sorted(rows[y], key=lambda s: s.coords[0][0]) for y in sorted(rows)]


class ActiveCell:
    """A helper class to keep track of a cell as it grows upward."""
    def __init__(self, initial_segment: LineString):
        self.segments: List[LineString] = [initial_segment]
        self.top_span: Tuple[float, float] = self._get_x_span(initial_segment)

    def add_segment(self, segment: LineString):
        self.segments.append(segment)
        # Update the span so the next row checks against this new top layer
        self.top_span = self._get_x_span(segment)

    @staticmethod # no self parameter, basically normal function
    def _get_x_span(segment: LineString) -> Tuple[float, float]:
        """Extracts the Left and Right X-coordinates of a segment."""
        start_x = segment.coords[0][0]
        end_x = segment.coords[-1][0]
        return (start_x, end_x)

    def overlaps_with(self, segment_span: Tuple[float, float]) -> bool:
        """True if the cell's top overlaps the given segment."""
        cell_left, cell_right = self.top_span
        seg_left, seg_right = segment_span
        return cell_left <= seg_right and seg_left <= cell_right


def _build_cells(rows: List[List[LineString]]) -> List[List[LineString]]:
    """Group segments into cells: walk the rows bottom-to-top, linking each segment
    to the one below it when their along-line spans overlap.

    - If a segment perfectly overlaps one cell below it, the cell continues.
    - If a row splits into multiple segments new cells start.
    - If multiple segments merge into one, the old cells finish and a new one starts.
    """

    finished_cells: List[List[LineString]] = []
    open_cells: List[ActiveCell] = []

    for current_row in rows:
        # Calculate spans for all segments in the current row
        current_row_spans = [ActiveCell._get_x_span(seg) for seg in current_row]

        # For a specific cell, which segments in the row above it does it touch
        cell_to_overlapping_segments = {cell_idx: [] for cell_idx in range(len(open_cells))}
        # For a specific segment, which cells in the row below it does it touch
        segment_to_overlapping_cells = {seg_idx: [] for seg_idx in range(len(current_row))}

        # check every active cell against every new segment.
        for cell_idx, active_cell in enumerate(open_cells):
            for seg_idx, segment_span in enumerate(current_row_spans):
                # for each open cell: for each segment of the current row: does it overlap?
                if active_cell.overlaps_with(segment_span):
                    cell_to_overlapping_segments[cell_idx].append(seg_idx)
                    segment_to_overlapping_cells[seg_idx].append(cell_idx)

        next_open_cells: List[ActiveCell] = []
        segments_already_added_to_a_cell = set()

        # Process existing open cells (Continue or Finish)
        for cell_idx, active_cell in enumerate(open_cells): # for every cell that is currently growing upward
            overlapping_segments = cell_to_overlapping_segments[cell_idx]

            # check if cell overlaps with exactly one segment above it,
            # and that segment overlaps with exactly one cell below it.
            # if both true: add segment to cell and keep it open
            if len(overlapping_segments) == 1:
                seg_idx = overlapping_segments[0]
                cells_overlapping_this_segment = segment_to_overlapping_cells[seg_idx]

                if len(cells_overlapping_this_segment) == 1:
                    segment_to_add = current_row[seg_idx]
                    active_cell.add_segment(segment_to_add)

                    next_open_cells.append(active_cell)
                    segments_already_added_to_a_cell.add(seg_idx)
                    continue

            # if not continue it hit a split, merge, or dead end
            # -> cell is dont growing
            finished_cells.append(active_cell.segments)

        # start new cells
        for seg_idx, segment in enumerate(current_row):
            if seg_idx not in segments_already_added_to_a_cell:
                new_cell = ActiveCell(segment)
                next_open_cells.append(new_cell)

        # Move up to the next row
        open_cells = next_open_cells

    # move all remaining open cells to the finished pile
    for cell in open_cells:
        finished_cells.append(cell.segments)

    return finished_cells


def route_transits(lines, polygon, restricted) -> list[LineString]:
    """One transit leg per consecutive line pair, in the projected CRS.
    Straight hop unless restricted and the hop leaves the polygon,
    then walk along the boundary instead."""
    safe = polygon.buffer(0.5)
    transits = []
    #loops through the ordered flight lines and builds the connecting paths
    for prev, nxt in zip(lines, lines[1:]): # always 1-2, 2-3 ....
        hop = LineString([prev.coords[-1], nxt.coords[0]]) # try straight path
        if restricted and not hop.covered_by(safe):
            ring = _hop_ring(polygon, hop)
            hop = _boundary_path(ring, hop.coords[0], hop.coords[-1])
            if not hop.covered_by(safe):
                raise ValueError(
                    "restricted transit still leaves the AOI; the boundary "
                    "walk cannot route around holes")
        transits.append(hop)
    return transits


def _hop_ring(polygon, hop):
    """Exterior ring for the boundary walk. For a MultiPolygon, the
    sub-polygon nearest the hop."""
    if polygon.geom_type == "MultiPolygon":
        return min(polygon.geoms, key=lambda p: p.distance(hop)).exterior
    return polygon.exterior


def _boundary_path(ring, p0, p1) -> LineString:
    """Path from p0 to p1 along the ring, shorter of the two directions."""
    # project -> how far along the perimeter the points sit eg 10m, 40m
    d0 = ring.project(Point(p0))
    d1 = ring.project(Point(p1))

    direct = abs(d1 - d0)
    # if direct route is shorter than rest of the ring use it
    if direct <= ring.length - direct:
        path = substring(ring, d0, d1)
    else:
        # ring starts at 0 and ends at ring.lenght
        # the shorter way goes through the ring closure point
        if d0 < d1:
            a = substring(ring, d0, 0)
            b = substring(ring, ring.length, d1)
        else:
            a = substring(ring, d0, ring.length)
            b = substring(ring, 0, d1)
        path = LineString(list(a.coords) + list(b.coords)[1:])
    # keep the exact endpoints, projection may have snapped them
    return LineString([p0, *path.coords[1:-1], p1])
