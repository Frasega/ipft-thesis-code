"""Raccoglie i dati necessari per i preset Rotterdam:
- numero di persone nei plans
- coordinate dei link hub/terminal (174131, 121963)
- censimento dei valori osm:way:highway
- ID esatti dei veicoli della transitLine 99437 (linea 44)
Scrive line44_vehicle_ids.txt e stampa il resto.
"""
import gzip
import json
import re
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent

# 1) persone nei plans
n_persons = 0
with gzip.open(HERE / "planExternalProcessed_lowerCase.xml.gz", "rt", encoding="utf-8") as f:
    for line in f:
        if "<person " in line:
            n_persons += 1
print("persons in plans:", n_persons)

# 2+3) rete: coordinate nodi dei link target + censimento highway
targets = {"174131", "121963"}
target_links = {}
node_coords = {}
needed_nodes = set()
highway_census = Counter()
nolink_attr = 0

with gzip.open(HERE / "networkWithRideAndBike.xml.gz", "rt", encoding="utf-8") as f:
    current_link = None
    has_hw = False
    for line in f:
        if "<node " in line:
            m = re.search(r'<node id="([^"]+)" x="([^"]+)" y="([^"]+)"', line)
            if m:
                node_coords[m.group(1)] = (float(m.group(2)), float(m.group(3)))
        elif "<link " in line:
            m = re.search(r'<link id="([^"]+)" from="([^"]+)" to="([^"]+)"', line)
            if m:
                current_link = m.group(1)
                has_hw = False
                if current_link in targets:
                    target_links[current_link] = (m.group(2), m.group(3))
                    needed_nodes.update((m.group(2), m.group(3)))
        elif "osm:way:highway" in line and current_link is not None:
            m = re.search(r">([^<]+)</attribute>", line)
            if m:
                highway_census[m.group(1)] += 1
                has_hw = True
        elif "</link>" in line and current_link is not None:
            if not has_hw:
                nolink_attr += 1
            current_link = None

print("\nhighway value census:")
for hw, c in highway_census.most_common():
    print(f"  {hw}: {c}")
print("links WITHOUT osm:way:highway:", nolink_attr)

print("\ntarget link node coords (EPSG:28992):")
for lid, (fr, to) in target_links.items():
    fx, fy = node_coords.get(fr, (None, None))
    tx, ty = node_coords.get(to, (None, None))
    mx = (fx + tx) / 2 if fx is not None and tx is not None else None
    my = (fy + ty) / 2 if fy is not None and ty is not None else None
    print(f"  link {lid}: from {fr} ({fx},{fy}) to {to} ({tx},{ty}) midpoint ({mx},{my})")

# 4) ID veicoli linea 99437
with gzip.open(HERE / "ptSchedule36Hour.xml.gz", "rt", encoding="utf-8") as f:
    sched = f.read()
block = re.search(r'<transitLine id="99437".*?</transitLine>', sched, re.S).group(0)
veh_ids = sorted(set(re.findall(r'vehicleRefId="([^"]+)"', block)))
out = HERE / "line44_vehicle_ids.txt"
out.write_text("\n".join(veh_ids) + "\n", encoding="utf-8")
print(f"\nline 99437 vehicle ids: {len(veh_ids)} -> {out.name}")
print("first/last:", veh_ids[0], veh_ids[-1])
