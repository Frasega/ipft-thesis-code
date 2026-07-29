"""Does the line-44 H->B bus traverse the pathologically-stuck corridor links?
Compares bus own speed vs background speed on each bus-route link."""
import io, collections
import xml.etree.ElementTree as ET
import numpy as np
import zstandard as zstd

BASE = "D:/TesiOutputs/ipft_rotterdam_longbase"
EVENTS = f"{BASE}/MRDH_10pct.output_events.xml.zst"
LINKS_CSV = f"{BASE}/MRDH_10pct.output_links.csv.zst"
SC = "scenarios/ipft_rotterdam"
MS2 = 3.6

bus_ids = {l.strip() for l in open(f"{SC}/line44_hb_vehicle_ids.txt") if l.strip()}
corridor = {l.strip() for l in open(f"{SC}/corridor_links.txt") if l.strip()}

length, freespeed, name, hw = {}, {}, {}, {}
with open(LINKS_CSV, "rb") as f:
    r = io.TextIOWrapper(zstd.ZstdDecompressor().stream_reader(f), encoding="utf-8")
    h = r.readline().strip().split(";")
    iL, iFs, iLen, iNm, iHw = (h.index("link"), h.index("freespeed"),
        h.index("length"), h.index("osm:way:name"), h.index("osm:way:highway"))
    for line in r:
        p = line.rstrip("\n").split(";")
        length[p[iL]] = float(p[iLen]); freespeed[p[iL]] = float(p[iFs])
        name[p[iL]] = p[iNm]; hw[p[iL]] = p[iHw]

entry = {}
bg_t = collections.defaultdict(float); bg_spd = collections.defaultdict(list)
bus_spd = collections.defaultdict(list)
bus_links = set()
with open(EVENTS, "rb") as f:
    for _, e in ET.iterparse(zstd.ZstdDecompressor().stream_reader(f), events=["end"]):
        if e.tag != "event":
            e.clear(); continue
        ty = e.get("type"); lid = e.get("link")
        if ty == "entered link":
            v = e.get("vehicle")
            if v and lid:
                if v in bus_ids or (lid in corridor and not v.startswith("veh_")
                                    and not v.startswith("backup_van_")):
                    entry[(v, lid)] = float(e.get("time", 0))
        elif ty == "left link":
            v = e.get("vehicle"); t0 = entry.pop((v, lid), None)
            if t0 is not None and lid in length:
                dt = float(e.get("time", 0)) - t0
                if dt > 0:
                    vv = length[lid] / dt
                    fs = freespeed.get(lid)
                    if fs and vv > 1.5 * fs:
                        vv = fs; dt = length[lid] / vv
                    if v in bus_ids:
                        bus_spd[lid].append(vv * MS2); bus_links.add(lid)
                    else:
                        bg_t[lid] += dt; bg_spd[lid].append(vv * MS2)
        e.clear()

print(f"links traversed by the line-44 H->B bus: {len(bus_links)}")

# stuck = background median < 5 km/h with meaningful hours
stuck = {l for l in bg_spd if np.median(bg_spd[l]) < 5 and bg_t[l] / 3600 > 5}
print(f"stuck background links (median<5 km/h, >5 vehicle-hours): {len(stuck)}")

hit = sorted(bus_links & stuck, key=lambda l: -bg_t[l] / 3600)
print(f"\n>>> stuck links the BUS actually traverses: {len(hit)} <<<\n")
print(f"{'link':>8} {'bg_med':>7} {'bg_hrs':>7} {'BUS_med':>8} {'BUS_n':>6} {'free':>5}  name [type]")
for l in hit:
    bm = np.median(bus_spd[l]); bn = len(bus_spd[l])
    print(f"{l:>8} {np.median(bg_spd[l]):7.1f} {bg_t[l]/3600:7.0f} {bm:8.1f} {bn:6d} "
          f"{freespeed[l]*MS2:5.0f}  {(name.get(l) or '')[:26]} [{hw.get(l)}]")

# bus speed summary over ALL its route links
allbus = np.array([np.median(s) for s in bus_spd.values()])
print(f"\nBUS per-link median speed over all {len(allbus)} route links: "
      f"percentiles[10/25/50/75/90] = {np.percentile(allbus,[10,25,50,75,90]).round(1)}")
print(f"bus route links with median < 5 km/h: {(allbus<5).sum()} of {len(allbus)}")
print("\nDONE")
