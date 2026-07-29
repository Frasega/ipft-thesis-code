"""Trova link car adatti come hub/terminal dei van vicino ai capolinea linea 44.

Requisiti: modes contiene 'car', il link appartiene alla componente car
principale (euristica: capacita' >= 600 e freespeed >= 8 evitano vicoli),
distanza minima dal capolinea bus.
Mostra anche i modes dei link incriminati 174131 e 748974 per conferma diagnosi.
"""
import gzip
import math
import re
from pathlib import Path

HERE = Path(__file__).parent
HUB_XY = (91797.66, 437662.70)       # Centraal perron BB
TERM_XY = (93114.29, 433575.08)      # Zuidplein Hoog

node_coords = {}
links = []  # (id, from, to, modes, capacity, freespeed)

with gzip.open(HERE / "networkWithRideAndBike.xml.gz", "rt", encoding="utf-8") as f:
    for line in f:
        if "<node " in line:
            m = re.search(r'<node id="([^"]+)" x="([^"]+)" y="([^"]+)"', line)
            if m:
                node_coords[m.group(1)] = (float(m.group(2)), float(m.group(3)))
        elif "<link " in line:
            m = re.search(
                r'<link id="([^"]+)" from="([^"]+)" to="([^"]+)" length="[^"]+" '
                r'freespeed="([^"]+)" capacity="([^"]+)" permlanes="[^"]+" '
                r'oneway="[^"]+" modes="([^"]+)"', line)
            if m:
                links.append((m.group(1), m.group(2), m.group(3),
                              m.group(6), float(m.group(5)), float(m.group(4))))

print(f"nodes: {len(node_coords)}, links: {len(links)}")

bylink = {l[0]: l for l in links}
for lid in ("174131", "748974", "121963"):
    l = bylink.get(lid)
    if l:
        print(f"link {lid}: modes={l[3]} capacity={l[4]} freespeed={l[5]:.1f}")


def midpoint(l):
    fx, fy = node_coords[l[1]]
    tx, ty = node_coords[l[2]]
    return ((fx + tx) / 2, (fy + ty) / 2)


def nearest_car_links(target, k=5):
    cands = []
    for l in links:
        modes = l[3].split(",")
        if "car" not in modes:
            continue
        if l[4] < 600 or l[5] < 8.0:   # evita vicoli/living street
            continue
        if l[1] not in node_coords or l[2] not in node_coords:
            continue
        mx, my = midpoint(l)
        d = math.hypot(mx - target[0], my - target[1])
        cands.append((d, l, (mx, my)))
    cands.sort(key=lambda x: x[0])
    return cands[:k]


for name, target in (("HUB (Centraal)", HUB_XY), ("TERMINAL (Zuidplein)", TERM_XY)):
    print(f"\n{name} — nearest robust car links:")
    for d, l, mid in nearest_car_links(target):
        print(f"  link {l[0]}: d={d:.0f} m  modes={l[3]}  cap={l[4]}  "
              f"v={l[5]:.1f} m/s  mid=({mid[0]:.1f},{mid[1]:.1f})")
