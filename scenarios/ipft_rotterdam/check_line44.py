"""Identifica la transitLine della linea 44 RET e verifica i vehicleRefId,
e censisce i modes della rete."""
import gzip
import re
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent

with gzip.open(HERE / "ptSchedule36Hour.xml.gz", "rt", encoding="utf-8") as f:
    sched = f.read()

# 1) censimento modes rete
with gzip.open(HERE / "networkWithRideAndBike.xml.gz", "rt", encoding="utf-8") as f:
    net = f.read()
all_modes = Counter(re.findall(r'<link id="[^"]+"[^>]*modes="([^"]+)"', net))
print("=== network link modes (top 20) ===")
for m, c in all_modes.most_common(20):
    print(f"  {m}: {c}")
has_bike = [m for m in all_modes if "bike" in m or "ride" in m]
print("modes containing bike/ride:", has_bike)

# 2) trova la transitLine che usa i veicoli veh_18265_bus..veh_18461_bus
print("\n=== line-44 vehicle mapping ===")
line_blocks = re.split(r"(?=<transitLine )", sched)
target = {f"veh_{i}_bus" for i in range(18265, 18462)}
for block in line_blocks:
    m = re.match(r'<transitLine id="([^"]+)"', block)
    if not m:
        continue
    vrefs = set(re.findall(r'vehicleRefId="([^"]+)"', block))
    overlap = vrefs & target
    if overlap:
        lid = m.group(1)
        name = re.search(r'<attribute name="gtfs_route_short_name"[^>]*>([^<]*)</attribute>', block)
        nameany = re.findall(r'<attribute name="([^"]+)"[^>]*>([^<]*)</attribute>', block)[:8]
        nroutes = block.count("<transitRoute ")
        ndeps = block.count("<departure ")
        stops = re.findall(r'<stopFacility', block)
        print(f"line {lid}: overlap {len(overlap)}/{len(vrefs)} vehicles, "
              f"{nroutes} routes, {ndeps} departures")
        print("  attrs:", nameany)

# 3) stop facilities di Rotterdam Centraal / Zuidplein
print("\n=== stop facilities matching names ===")
for pat in ("Centraal", "Zuidplein"):
    hits = re.findall(r'<stopFacility id="([^"]+)"[^>]*name="([^"]*' + pat + r'[^"]*)"', sched)
    print(pat, len(hits), hits[:6])
