"""Find the pathologically stuck corridor links: who dominates total vehicle-hours
and who has the lowest median speed. Joins OSM name + highway type."""
import io, collections
import xml.etree.ElementTree as ET
import numpy as np
import zstandard as zstd

BASE = "D:/TesiOutputs/ipft_rotterdam_longbase"
EVENTS = f"{BASE}/MRDH_10pct.output_events.xml.zst"
LINKS_CSV = f"{BASE}/MRDH_10pct.output_links.csv.zst"
CORR = "scenarios/ipft_rotterdam/corridor_links.txt"
MS2 = 3.6

with open(CORR) as f:
    corridor = {l.strip() for l in f if l.strip()}

length, freespeed, name, hw = {}, {}, {}, {}
with open(LINKS_CSV, "rb") as f:
    r = io.TextIOWrapper(zstd.ZstdDecompressor().stream_reader(f), encoding="utf-8")
    h = r.readline().strip().split(";")
    iL, iFs, iLen = h.index("link"), h.index("freespeed"), h.index("length")
    iNm, iHw = h.index("osm:way:name"), h.index("osm:way:highway")
    for line in r:
        p = line.rstrip("\n").split(";")
        if p[iL] in corridor:
            length[p[iL]] = float(p[iLen]); freespeed[p[iL]] = float(p[iFs])
            name[p[iL]] = p[iNm]; hw[p[iL]] = p[iHw]

entry = {}
tt = collections.defaultdict(float)   # total time per link
td = collections.defaultdict(float)   # total dist per link
spd = collections.defaultdict(list)   # speeds per link
with open(EVENTS, "rb") as f:
    for _, e in ET.iterparse(zstd.ZstdDecompressor().stream_reader(f), events=["end"]):
        if e.tag != "event":
            e.clear(); continue
        ty = e.get("type")
        if ty == "entered link":
            lid = e.get("link")
            if lid in corridor:
                v = e.get("vehicle")
                if v and not v.startswith("veh_") and not v.startswith("backup_van_"):
                    entry[(v, lid)] = float(e.get("time", 0))
        elif ty == "left link":
            lid = e.get("link")
            if lid in corridor:
                v = e.get("vehicle"); t0 = entry.pop((v, lid), None)
                if t0 is not None and lid in length:
                    dt = float(e.get("time", 0)) - t0
                    if dt > 0:
                        vv = length[lid] / dt
                        fs = freespeed.get(lid)
                        if fs and vv > 1.5 * fs:
                            vv = fs; dt = length[lid] / vv
                        tt[lid] += dt; td[lid] += length[lid]; spd[lid].append(vv * MS2)
        e.clear()

tot_h = sum(tt.values()) / 3600
print(f"total corridor vehicle-hours: {tot_h:,.0f}")

rows = []
for lid in tt:
    s = np.array(spd[lid])
    rows.append((lid, tt[lid] / 3600, len(s), np.median(s),
                 td[lid] / tt[lid] * MS2, freespeed[lid] * MS2,
                 (name.get(lid) or "")[:28], hw.get(lid) or ""))

print("\n===== TOP 20 links by TOTAL HOURS (these sink the km/h average) =====")
print(f"{'link':>8} {'hours':>7} {'%tot':>5} {'n':>6} {'med':>5} {'mean':>5} {'free':>5}  name / type")
cum = 0
for lid, hrs, n, med, mean, fs, nm, ht in sorted(rows, key=lambda x: -x[1])[:20]:
    cum += hrs
    print(f"{lid:>8} {hrs:7.0f} {hrs/tot_h*100:4.0f}% {n:6d} {med:5.1f} {mean:5.1f} {fs:5.0f}  {nm} [{ht}]")
topN = sorted(rows, key=lambda x: -x[1])
for k in (5, 10, 20):
    print(f"top {k:2d} links = {sum(r[1] for r in topN[:k])/tot_h*100:4.1f}% of all corridor vehicle-hours")

print("\n===== TOP 15 links by LOWEST MEDIAN speed (min 50 traversals) =====")
busy = [r for r in rows if r[2] >= 50]
for lid, hrs, n, med, mean, fs, nm, ht in sorted(busy, key=lambda x: x[3])[:15]:
    print(f"{lid:>8} med={med:5.1f} mean={mean:5.1f} free={fs:5.0f} n={n:5d} hours={hrs:6.0f}  {nm} [{ht}]")
print("\nDONE")
