"""Confronta ptVehicleExtended.xml (file di Jingjun) con ptSchedule36Hour.xml.gz."""
import gzip
import re
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent

veh = (HERE / "ptVehicleExtended.xml").read_text(encoding="utf-8")
types = re.findall(r'<vehicleType id="([^"]+)"', veh)
vehs = re.findall(r'<vehicle id="([^"]+)" type="([^"]+)"', veh)
print("vehicleTypes:", types)
print("total vehicles:", len(vehs))
print(Counter(t for _, t in vehs))

vset = {v for v, _ in vehs}
with gzip.open(HERE / "ptSchedule36Hour.xml.gz", "rt", encoding="utf-8") as f:
    sched = f.read()
refs = set(re.findall(r'vehicleRefId="([^"]+)"', sched))
print("schedule unique vehicleRefIds:", len(refs))
print("refs missing from vehicle file:", len(refs - vset))
print("vehicles not referenced by schedule:", len(vset - refs))
print("sample missing:", sorted(refs - vset)[:10])

nums = sorted(
    int(m.group(1)) for v in vset if (m := re.fullmatch(r"veh_(\d+)_bus", v))
)
in_range = [n for n in nums if 18265 <= n <= 18461]
print("bus ids in line-44 range 18265-18461 present:", len(in_range))
