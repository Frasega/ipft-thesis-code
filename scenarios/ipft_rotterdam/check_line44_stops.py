"""Elenca fermate e link della transitLine 99437 (RET bus 44)."""
import gzip
import re
from pathlib import Path

HERE = Path(__file__).parent

with gzip.open(HERE / "ptSchedule36Hour.xml.gz", "rt", encoding="utf-8") as f:
    sched = f.read()

# stopFacility id -> (name, linkRefId)
fac = {
    m.group(1): (m.group(3), m.group(2))
    for m in re.finditer(
        r'<stopFacility id="([^"]+)" [^>]*linkRefId="([^"]+)"[^>]*name="([^"]*)"', sched
    )
}
if not fac:
    # attribute order may differ
    for m in re.finditer(r'<stopFacility ([^>]+)>', sched):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
        if "id" in attrs:
            fac[attrs["id"]] = (attrs.get("name", "?"), attrs.get("linkRefId", "?"))

block = re.search(r'<transitLine id="99437".*?</transitLine>', sched, re.S).group(0)
routes = re.findall(r'<transitRoute id="([^"]+)">(.*?)</transitRoute>', block, re.S)
print(f"line 99437: {len(routes)} routes")
for rid, body in routes:
    stops = re.findall(r'<stop refId="([^"]+)"', body)
    ndeps = body.count("<departure ")
    first = fac.get(stops[0], ("?", "?"))
    last = fac.get(stops[-1], ("?", "?"))
    print(f"route {rid}: {len(stops)} stops, {ndeps} departures")
    print(f"   first: {stops[0]} {first}")
    print(f"   last : {stops[-1]} {last}")

# tutte le fermate uniche della linea con i loro link
print("\nAll unique stops of line 99437:")
allstops = []
seen = set()
for rid, body in routes:
    for s in re.findall(r'<stop refId="([^"]+)"', body):
        if s not in seen:
            seen.add(s)
            allstops.append(s)
for s in allstops:
    name, link = fac.get(s, ("?", "?"))
    print(f"  {s}  link={link}  {name}")
