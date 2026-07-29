"""Conta partenze per direzione e finestra oraria della linea 99437."""
import gzip
import re
from pathlib import Path

HERE = Path(__file__).parent
with gzip.open(HERE / "ptSchedule36Hour.xml.gz", "rt", encoding="utf-8") as f:
    sched = f.read()

block = re.search(r'<transitLine id="99437".*?</transitLine>', sched, re.S).group(0)
routes = re.findall(r'<transitRoute id="([^"]+)">(.*?)</transitRoute>', block, re.S)

HB_FIRST = "2522467.link:174131"  # Centraal perron BB → Zuidplein
hb_deps, bh_deps = [], []
for rid, body in routes:
    stops = re.findall(r'<stop refId="([^"]+)"', body)
    deps = re.findall(r'departureTime="(\d+):(\d+):(\d+)"', body)
    times = [int(h) * 3600 + int(m) * 60 + int(s) for h, m, s in deps]
    if stops[0] == HB_FIRST:
        hb_deps += times
    else:
        bh_deps += times


def rng(ts):
    if not ts:
        return "n/a"
    return f"{min(ts)/3600:.2f}h – {max(ts)/3600:.2f}h"


print("H→B (Centraal→Zuidplein):", len(hb_deps), "departures,", rng(hb_deps))
print("B→H / altro:", len(bh_deps), "departures,", rng(bh_deps))
print("oltre le 24h: H→B", sum(t >= 86400 for t in hb_deps), "| B→H", sum(t >= 86400 for t in bh_deps))
