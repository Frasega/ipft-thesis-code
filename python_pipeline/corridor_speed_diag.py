"""
One-off diagnostic: corridor speed distribution from the LONGBASE run.
Answers: is the 7.7 km/h uniform slowness, a few jammed links, or low free-flow?
Outputs free-flow vs actual speeds, per-link distribution, peak vs off-peak.
"""
import io
import xml.etree.ElementTree as ET

import numpy as np
import zstandard as zstd

BASE = "D:/TesiOutputs/ipft_rotterdam_longbase"
EVENTS = f"{BASE}/MRDH_10pct.output_events.xml.zst"
LINKS_CSV = f"{BASE}/MRDH_10pct.output_links.csv.zst"
CORR = "scenarios/ipft_rotterdam/corridor_links.txt"

MS2KMH = 3.6

# ── corridor link set ────────────────────────────────────────────────────────
with open(CORR) as f:
    corridor = set(line.strip() for line in f if line.strip())
print(f"corridor links: {len(corridor)}")

# ── link length / freespeed / volume from output_links.csv ───────────────────
length, freespeed, vol = {}, {}, {}
with open(LINKS_CSV, "rb") as f:
    r = io.TextIOWrapper(zstd.ZstdDecompressor().stream_reader(f), encoding="utf-8")
    header = r.readline().strip().split(";")
    iL, iFs, iLen, iVol = (header.index("link"), header.index("freespeed"),
                           header.index("length"), header.index("vol_car"))
    for line in r:
        p = line.rstrip("\n").split(";")
        lid = p[iL]
        if lid in corridor:
            length[lid] = float(p[iLen])
            freespeed[lid] = float(p[iFs])
            vol[lid] = float(p[iVol]) if p[iVol] else 0.0

print(f"corridor links found in links.csv: {len(length)}")

# free-flow context (length-weighted and volume-weighted)
lids = [l for l in corridor if l in length]
L = np.array([length[l] for l in lids])
FS = np.array([freespeed[l] for l in lids])
V = np.array([vol[l] for l in lids])
ff_lw = (FS * L).sum() / L.sum() * MS2KMH
ff_vw = (FS * V).sum() / V.sum() * MS2KMH if V.sum() > 0 else float("nan")
print(f"\nFREE-FLOW corridor speed (length-weighted): {ff_lw:.1f} km/h")
print(f"FREE-FLOW corridor speed (volume-weighted): {ff_vw:.1f} km/h")
print(f"free-flow per-link km/h percentiles [10/25/50/75/90]: "
      f"{np.percentile(FS*MS2KMH,[10,25,50,75,90]).round(1)}")

# ── stream events: per-link actual speeds for BACKGROUND cars on corridor ─────
entry = {}            # (vid,lid) -> t
# accumulate per link: total distance, total time  (km/hours aggregate)
sum_dist = {}         # lid -> meters
sum_time = {}         # lid -> seconds
# per-traversal speeds for distribution + hour split
trav_speed = []       # (lid, v_ms, hour, dt)
n_ev = 0

with open(EVENTS, "rb") as f:
    reader = zstd.ZstdDecompressor().stream_reader(f)
    for _, elem in ET.iterparse(reader, events=["end"]):
        if elem.tag != "event":
            elem.clear(); continue
        et = elem.get("type")
        if et == "entered link":
            lid = elem.get("link")
            if lid in corridor:
                vid = elem.get("vehicle")
                if vid and not vid.startswith("veh_") and not vid.startswith("backup_van_"):
                    entry[(vid, lid)] = float(elem.get("time", 0))
        elif et == "left link":
            lid = elem.get("link")
            if lid in corridor:
                vid = elem.get("vehicle")
                k = (vid, lid)
                t0 = entry.pop(k, None)
                if t0 is not None and lid in length:
                    t1 = float(elem.get("time", 0))
                    dt = t1 - t0
                    if dt > 0:
                        v = length[lid] / dt
                        fs = freespeed.get(lid)
                        if fs and v > 1.5 * fs:
                            v = fs; dt = length[lid] / v
                        sum_dist[lid] = sum_dist.get(lid, 0.0) + length[lid]
                        sum_time[lid] = sum_time.get(lid, 0.0) + dt
                        trav_speed.append((lid, v, int(t0 // 3600), dt))
                        n_ev += 1
        elem.clear()

print(f"\nbackground corridor traversals: {n_ev:,}")

# ── overall actual speed (km total / hours total) ────────────────────────────
tot_d = sum(sum_dist.values())
tot_t = sum(sum_time.values())
print(f"ACTUAL corridor speed (total km / total h): {tot_d/tot_t*MS2KMH:.1f} km/h")

arr = np.array([s[1] for s in trav_speed]) * MS2KMH
dt_arr = np.array([s[3] for s in trav_speed])
hr = np.array([s[2] for s in trav_speed])
print(f"per-traversal speed km/h percentiles [10/25/50/75/90]: "
      f"{np.percentile(arr,[10,25,50,75,90]).round(1)}")
print(f"share of traversals < 5 km/h: {(arr<5).mean()*100:.0f}%")
print(f"share of traversals < 10 km/h: {(arr<10).mean()*100:.0f}%")

# per-link median actual speed → is slowness uniform or concentrated?
import collections
by_link = collections.defaultdict(list)
for lid, v, h, dt in trav_speed:
    by_link[lid].append(v * MS2KMH)
link_med = np.array([np.median(vs) for vs in by_link.values()])
print(f"\nper-LINK median speed km/h percentiles [10/25/50/75/90]: "
      f"{np.percentile(link_med,[10,25,50,75,90]).round(1)}")
print(f"links with median < 5 km/h: {(link_med<5).mean()*100:.0f}% of {len(link_med)}")

# ── peak vs off-peak (time/dist weighted) ────────────────────────────────────
def windowed(hours):
    d = sum(length[l] for (l, v, h, dt) in trav_speed if h in hours for _ in [0])
    # recompute weighted properly
    dd = sum(s[3]*0 for s in trav_speed)  # placeholder
    return None

def wspeed(hours):
    d = 0.0; t = 0.0
    for lid, v, h, dt in trav_speed:
        if h in hours:
            d += length[lid]; t += dt
    return d / t * MS2KMH if t > 0 else float("nan")

peak = wspeed({7, 8, 16, 17})
offpeak = wspeed({10, 11, 12, 13, 14})
night = wspeed({1, 2, 3, 4})
print(f"\nPEAK (7-9,16-18)   speed: {peak:.1f} km/h")
print(f"OFF-PEAK (10-15)   speed: {offpeak:.1f} km/h")
print(f"NIGHT (1-5)        speed: {night:.1f} km/h")
print("\nDONE")
