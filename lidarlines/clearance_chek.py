import warnings

import numpy as np
from pyproj import Transformer


def check_clearance(plan, r, min_agl, max_agl=120):
    """Warn where the path (sampled every ~1 m) leaves [min_agl, max_agl] above the DTM."""
    wps = plan.waypoints()
    to_proj = Transformer.from_crs(4326, r.crs, always_xy=True)
    x, y = to_proj.transform([wp.lon for wp in wps], [wp.lat for wp in wps])
    z = [wp.alt_m for wp in wps]

    for i in range(len(wps) - 1):
        n = int(np.hypot(x[i + 1] - x[i], y[i + 1] - y[i])) + 2
        xs, ys, zs = (np.linspace(v[i], v[i + 1], n) for v in (x, y, z))

        rows = ((r.y_origin - ys) / r.pixel_height).astype(int)
        cols = ((xs - r.x_origin) / r.pixel_width).astype(int)
        rows = rows.clip(0, r.data.shape[0] - 1)
        cols = cols.clip(0, r.data.shape[1] - 1)
        agl = zs - r.data[rows, cols]

        if np.nanmin(agl) < min_agl or np.nanmax(agl) > max_agl:
            msg = (f"wp {i + 1}->{i + 2}: {np.nanmin(agl):.1f}-{np.nanmax(agl):.1f} m "
                   f"above ground, outside [{min_agl}, {max_agl}]")
            warnings.warn(msg, stacklevel=2)
            plan.metrics.warnings.append(msg)