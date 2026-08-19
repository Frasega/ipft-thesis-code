"""Corridor map of RET line 44 (Centraal -> Zuidplein, H->B) for the Patrick deck.
Draws: the 1,360-link corridor mask (light grey context), the ordered H->B route
polyline (blue), the stops along it (dots), and labelled hub/terminal endpoints.
Coordinates are EPSG:28992 (RD New, metres) -> plotted with equal aspect.
"""
import gzip
import os
import re
from pathlib import Path

import matplotlib.pyplot as plt

HERE = Path(__file__).parent
OUT = Path(os.environ.get(
    "IPFT_FIGURES_DIR",
    r"c:\Users\frare\OneDrive\Desktop\Tesi documents\Tesi Regazzoni\figures",
)) / "corridor_line44_map.png"
OUT.parent.mkdir(parents=True, exist_ok=True)
HB_FIRST = "2522467.link:174131"  # Centraal perron BB (first H->B stop)

# ── network: node coords + link endpoints ──────────────────────────────────
node = {}
link_ends = {}
with gzip.open(HERE / "networkWithRideAndBike.xml.gz", "rt", encoding="utf-8") as f:
    for line in f:
        if "<node " in line:
            m = re.search(r'<node id="([^"]+)" x="([^"]+)" y="([^"]+)"', line)
            if m:
                node[m.group(1)] = (float(m.group(2)), float(m.group(3)))
        elif "<link " in line:
            m = re.search(r'<link id="([^"]+)" from="([^"]+)" to="([^"]+)"', line)
            if m:
                link_ends[m.group(1)] = (m.group(2), m.group(3))


def seg(link_id):
    """Return ((x0,y0),(x1,y1)) for a link id, or None."""
    e = link_ends.get(link_id)
    if not e or e[0] not in node or e[1] not in node:
        return None
    return node[e[0]], node[e[1]]


# ── schedule: stop facilities + the ordered H->B route ─────────────────────
sched = gzip.open(HERE / "ptSchedule36Hour.xml.gz", "rt", encoding="utf-8").read()
stop_xy = {}
stop_name = {}
for m in re.finditer(r'<stopFacility id="([^"]+)"[^>]*x="([^"]+)" y="([^"]+)"[^>]*name="([^"]+)"', sched):
    stop_xy[m.group(1)] = (float(m.group(2)), float(m.group(3)))
    stop_name[m.group(1)] = m.group(4)

block = re.search(r'<transitLine id="99437".*?</transitLine>', sched, re.S).group(0)
hb_links, hb_stops = [], []
for body in re.findall(r'<transitRoute id="[^"]+">(.*?)</transitRoute>', block, re.S):
    stops = re.findall(r'<stop refId="([^"]+)"', body)
    if stops and stops[0] == HB_FIRST:
        hb_links = re.findall(r'<link refId="([^"]+)"/>', body)
        hb_stops = stops
        break

# ── plot ───────────────────────────────────────────────────────────────────
fig, ax = plt.subplots(figsize=(7.5, 8.0))

# context: corridor mask in light grey
for lid in (HERE / "corridor_links.txt").read_text().split():
    s = seg(lid)
    if s:
        ax.plot([s[0][0], s[1][0]], [s[0][1], s[1][1]], color="#d9d9d9", lw=0.6, zorder=1)

# the H->B route polyline
first = True
for lid in hb_links:
    s = seg(lid)
    if s:
        ax.plot([s[0][0], s[1][0]], [s[0][1], s[1][1]], color="#1f4e79", lw=2.4,
                zorder=3, label="Line 44 route (one-to-many)" if first else None)
        first = False

# stops
xs = [stop_xy[s][0] for s in hb_stops if s in stop_xy]
ys = [stop_xy[s][1] for s in hb_stops if s in stop_xy]
ax.scatter(xs, ys, s=28, color="#c00", zorder=4, label="Stops")

# label endpoints
if hb_stops:
    h, t = hb_stops[0], hb_stops[-1]
    if h in stop_xy:
        ax.annotate("HUB: Rotterdam Centraal", stop_xy[h], textcoords="offset points",
                    xytext=(8, 8), fontsize=9, fontweight="bold", color="#1f4e79")
    if t in stop_xy:
        ax.annotate("TERMINAL: Zuidplein", stop_xy[t], textcoords="offset points",
                    xytext=(8, -14), fontsize=9, fontweight="bold", color="#1f4e79")

ax.set_aspect("equal")
ax.set_title("IPFT corridor: RET line 44, Rotterdam Centraal to Zuidplein\n"
             "98 one-to-many departures/day, crosses the river Maas", fontsize=10)
ax.set_xlabel("Easting [m, EPSG:28992]")
ax.set_ylabel("Northing [m]")
ax.legend(loc="upper right", fontsize=8, framealpha=0.9)
ax.grid(alpha=0.15)
fig.tight_layout()
fig.savefig(OUT, dpi=150)
print(f"saved {OUT}  | route links drawn: {sum(1 for l in hb_links if seg(l))}, "
      f"stops: {len(xs)}")
