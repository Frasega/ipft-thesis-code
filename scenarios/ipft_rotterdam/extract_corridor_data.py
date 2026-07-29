"""Estrae dalla linea 44 (transitLine 99437):
1. line44_hb_vehicle_ids.txt  — SOLO i veicoli della direzione H->B
   (Centraal perron BB -> Zuidplein Hoog): servono a Term C, pax timeline
   e alpha_max. La direzione di ritorno non trasporta merci.
2. [DEPRECATO 2026-07-28] La generazione di corridor_links.txt come "route linea 44
   + buffer car 250 m" era SBAGLIATA per S_cong: i van (car-routed) prendono la
   strada auto piu' veloce, che diverge dal corridoio bus. Verificato dagli eventi:
   i van percorrono 163 link, di cui solo 76 finivano nel buffer (1360 link) e 87
   ne restavano fuori. Il corridoio per S_cong / indicatori di traffico DEVE essere
   i link dove i van passano davvero -> ora generato da make_van_corridor.py dagli
   eventi di un run alpha=0. Qui il buffer viene solo salvato come file diagnostico
   (corridor_links_BUS_BUFFER.txt), NON piu' come corridor_links.txt.
"""
import gzip
import math
import re
from pathlib import Path

HERE = Path(__file__).parent
BUFFER_M = 250.0
HB_FIRST = "2522467.link:174131"  # Centraal perron BB

with gzip.open(HERE / "ptSchedule36Hour.xml.gz", "rt", encoding="utf-8") as f:
    sched = f.read()

block = re.search(r'<transitLine id="99437".*?</transitLine>', sched, re.S).group(0)
routes = re.findall(r'<transitRoute id="[^"]+">(.*?)</transitRoute>', block, re.S)

hb_vehicles = set()
line_links = set()
for body in routes:
    stops = re.findall(r'<stop refId="([^"]+)"', body)
    line_links.update(re.findall(r'<link refId="([^"]+)"/>', body))
    if stops and stops[0] == HB_FIRST:
        hb_vehicles.update(re.findall(r'vehicleRefId="([^"]+)"', body))

out_hb = HERE / "line44_hb_vehicle_ids.txt"
out_hb.write_text("\n".join(sorted(hb_vehicles)) + "\n", encoding="utf-8")
print(f"H->B vehicles: {len(hb_vehicles)} -> {out_hb.name}")
print(f"line-44 route links (both directions): {len(line_links)}")

# ── rete: coordinate e selezione buffer ────────────────────────────────────
node_coords = {}
links = []
with gzip.open(HERE / "networkWithRideAndBike.xml.gz", "rt", encoding="utf-8") as f:
    for line in f:
        if "<node " in line:
            m = re.search(r'<node id="([^"]+)" x="([^"]+)" y="([^"]+)"', line)
            if m:
                node_coords[m.group(1)] = (float(m.group(2)), float(m.group(3)))
        elif "<link " in line:
            m = re.search(r'<link id="([^"]+)" from="([^"]+)" to="([^"]+)"'
                          r'[^>]*modes="([^"]+)"', line)
            if m:
                links.append((m.group(1), m.group(2), m.group(3), m.group(4)))


def mid(frm, to):
    fx, fy = node_coords[frm]
    tx, ty = node_coords[to]
    return ((fx + tx) / 2, (fy + ty) / 2)


# punti di riferimento del corridoio = midpoint dei link della linea
byid = {l[0]: l for l in links}
ref_pts = []
for lid in line_links:
    l = byid.get(lid)
    if l and l[1] in node_coords and l[2] in node_coords:
        ref_pts.append(mid(l[1], l[2]))
print(f"corridor reference points: {len(ref_pts)}")

# griglia spaziale per il buffer (evita O(n*m) puro)
CELL = BUFFER_M
grid = {}
for x, y in ref_pts:
    grid.setdefault((int(x // CELL), int(y // CELL)), []).append((x, y))


def near_corridor(x, y):
    cx, cy = int(x // CELL), int(y // CELL)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for px, py in grid.get((cx + dx, cy + dy), ()):
                if math.hypot(x - px, y - py) <= BUFFER_M:
                    return True
    return False


corridor = set(line_links)
n_car_added = 0
for lid, frm, to, modes in links:
    if lid in corridor or "car" not in modes.split(","):
        continue_flag = False
    if lid in corridor:
        continue
    if "car" not in modes.split(","):
        continue
    if frm not in node_coords or to not in node_coords:
        continue
    x, y = mid(frm, to)
    if near_corridor(x, y):
        corridor.add(lid)
        n_car_added += 1

# [DEPRECATO 2026-07-28] Il buffer NON e' piu' scritto come corridor_links.txt
# (era il set sbagliato per S_cong: vedi docstring). Lo salviamo solo come file
# diagnostico. Il corridor_links.txt corretto (link van reali) e' generato da
# make_van_corridor.py dagli eventi di un run alpha=0.
out_corr = HERE / "corridor_links_BUS_BUFFER.txt"
out_corr.write_text("\n".join(sorted(corridor)) + "\n", encoding="utf-8")
print(f"[diagnostic only] bus buffer: {len(corridor)} links ({n_car_added} car links "
      f"within {BUFFER_M:.0f} m) -> {out_corr.name}")
print("NB: corridor_links.txt (per S_cong) va generato con make_van_corridor.py "
      "= link dove i van passano davvero. NON usare questo buffer.")
