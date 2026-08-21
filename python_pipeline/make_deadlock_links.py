"""
Build the deadlock-link exclusion list for the vehicle-hours metrics.

WHY (2026-08-19). The supplied Rotterdam network contains a handful of links —
all in the Zuidplein bus-station area — where background vehicles are not slow
but *stuck*: `244671` (Metroplein, 29.8 m) records 229 traversals totalling 619
background vehicle-hours, i.e. 2.7 HOURS to cover 30 metres, 0.01 km/h. That is
a queue that never clears, not congestion.

The defect predates the IPFT pipeline: it is measured on the two LONGBASE runs,
which contain no freight and no vans, so nothing in this pipeline can cause it.
But those links sit INSIDE the reported link sets, and because vehicle-hours are
a time integral they dominate the totals:

    set                measured on LONGBASE peak      share from deadlock links
    corridor_links     1,194.5 vehicle-hours          54.5%
    bus_stop_links        91.1                         0.0%   (clean)
    khop_ring1           325.1                        28.5%
    khop_ring2           972.7                        47.5%
    khop_ring3           543.4                         7.3%

CO2 is NOT affected the same way: with the `average` HBEFA lookup the emission
factor is a step function of speed, so below the stop&go threshold a vehicle is
charged 281.23 g/km whether it moves at 5 km/h or at 0.01, and the per-link CO2
is therefore identical in baseline and scenario and cancels in the difference.
The vehicle-hours do not cancel — the waiting time moves even though the
traversal COUNT stays fixed (229 in every cell). Hence: exclude these links from
the vehicle-hours rows only, and leave the kg rows untouched.

THE CRITERION, and why it is shaped this way. Four conditions, all measured on
the LONGBASE — that is, BEFORE and INDEPENDENTLY of any scenario:

  1. median background speed < MEDIAN_SPEED_KMH_MAX (default 1.0 km/h)
  2. median background speed < SPEED_RATIO_MAX of the link's own free speed
     (default 0.02, i.e. under a fiftieth of what the link is built for)
  3. mean background time per traversal > SECONDS_PER_TRAVERSAL_MIN (default 60 s)
  4. at least MIN_TRAVERSALS background traversals (default 5)

Each condition removes a different false positive, and all four are needed.
Condition 1 alone catches any link that is merely slow. Condition 2 makes the
threshold scale-free: 1 km/h means something different on a 15 km/h alley and on
an 80 km/h trunk, and a ratio to the link's own free speed does not. Condition 3
requires the slowness to cost real time, which separates a deadlock from a short
crawl over a few metres. Condition 4 is the one that matters most in practice:
without it the criterion selects 8,963 links network-wide, nearly all of them
crossed ONCE by a single slow vehicle and holding 0.0 vehicle-hours — a deadlock
is a queue, and a queue needs vehicles in it.

Measuring on the LONGBASE is the point: the exclusion cannot be accused of having
been chosen on the results, because the runs used to define it contain no
treatment at all.

The output is a plain link list, one id per line, with a header comment carrying
the criterion and the run it was measured on, so a reader of the thesis can
reproduce the set.

Usage (from project root):
    python python_pipeline/make_deadlock_links.py
    python python_pipeline/make_deadlock_links.py --congestion offpeak
    python python_pipeline/make_deadlock_links.py --median-speed 1.0 --seconds 60
"""

from __future__ import annotations

import argparse
import collections
import io
import re
import statistics
import sys
from pathlib import Path

import zstandard

sys.path.insert(0, str(Path(__file__).parent))

from scenario_presets import OUTPUT_ROOT, get_preset

MEDIAN_SPEED_KMH_MAX = 1.0
SPEED_RATIO_MAX = 0.02
SECONDS_PER_TRAVERSAL_MIN = 60.0
MIN_TRAVERSALS = 5

_RX = re.compile(r'link="([^"]+)"\s+vehicle="([^"]+)"|vehicle="([^"]+)"\s+link="([^"]+)"')


def load_link_geometry(links_csv: Path) -> tuple[dict, dict, dict, dict]:
    """{link: length}, {link: freespeed}, {link: name}, {link: capacity}."""
    length, freespeed, name, capacity = {}, {}, {}, {}
    with open(links_csv, "rb") as f:
        r = io.TextIOWrapper(zstandard.ZstdDecompressor().stream_reader(f), encoding="utf-8")
        head = r.readline().strip().split(";")
        i_l, i_len, i_fs = head.index("link"), head.index("length"), head.index("freespeed")
        i_nm, i_cap = head.index("osm:way:name"), head.index("capacity")
        for line in r:
            p = line.split(";")
            lid = p[i_l]
            length[lid] = float(p[i_len])
            freespeed[lid] = float(p[i_fs])
            name[lid] = p[i_nm]
            capacity[lid] = float(p[i_cap])
    return length, freespeed, name, capacity


def scan_background(events: Path, length: dict, freespeed: dict,
                    watch: frozenset[str] | None = None):
    """Per-link background vehicle-hours, per-traversal speeds and counts.

    Background = neither transit (`veh_` prefix) nor inserted vans
    (`backup_van_` prefix), matching parse_events.classify_vehicle.
    """
    entry: dict = {}
    hours: dict = collections.defaultdict(float)
    speeds: dict = collections.defaultdict(list)
    with open(events, "rb") as f:
        r = io.TextIOWrapper(zstandard.ZstdDecompressor().stream_reader(f), encoding="utf-8")
        for line in r:
            if ' link"' not in line:            # "entered link" / "left link"
                continue
            m = _RX.search(line)
            if not m:
                continue
            lid = m.group(1) or m.group(4)
            veh = m.group(2) or m.group(3)
            if lid not in length:
                continue
            if watch is not None and lid not in watch:
                continue
            if veh.startswith("veh_") or veh.startswith("backup_van_"):
                continue
            t = float(line.split('time="', 1)[1].split('"', 1)[0])
            if 'type="entered link"' in line:
                entry[(veh, lid)] = t
                continue
            t0 = entry.pop((veh, lid), None)
            if t0 is None:
                continue
            dt = t - t0
            if dt <= 0:
                continue
            v = length[lid] / dt
            fs = freespeed.get(lid)
            if fs and v > 1.5 * fs:             # same clamp as corridor_metrics
                v = fs
                dt = length[lid] / v
            hours[lid] += dt / 3600.0
            speeds[lid].append(v * 3.6)
    return hours, speeds


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="rotterdam")
    ap.add_argument("--congestion", choices=["peak", "offpeak"], default="peak")
    ap.add_argument("--median-speed", type=float, default=MEDIAN_SPEED_KMH_MAX,
                    help="km/h; a link qualifies below this median background speed")
    ap.add_argument("--seconds", type=float, default=SECONDS_PER_TRAVERSAL_MIN,
                    help="s; a link qualifies above this mean time per traversal")
    ap.add_argument("--speed-ratio", type=float, default=SPEED_RATIO_MAX,
                    help="fraction of the link's own free speed; scale-free version of --median-speed")
    ap.add_argument("--min-traversals", type=int, default=MIN_TRAVERSALS,
                    help="a deadlock is a queue, and a queue needs vehicles in it")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    preset = get_preset(args.scenario)
    root = Path(__file__).resolve().parent.parent
    base = OUTPUT_ROOT / ("ipft_rotterdam_longbase"
                          + ("_offpeak" if args.congestion == "offpeak" else ""))
    events = base / "MRDH_10pct.output_events.xml.zst"
    links_csv = base / "MRDH_10pct.output_links.csv.zst"
    for p in (events, links_csv):
        if not p.exists():
            raise FileNotFoundError(f"{p} not found — run the {args.congestion} LONGBASE first")

    print(f"[deadlock] LONGBASE {args.congestion}: {base.name}")
    length, freespeed, name, capacity = load_link_geometry(links_csv)
    print(f"[deadlock] network: {len(length)} links")
    hours, speeds = scan_background(events, length, freespeed)
    print(f"[deadlock] links with at least one background traversal: {len(speeds)}")

    rows = []
    for lid, sp in speeds.items():
        med = statistics.median(sp)
        spt = hours[lid] * 3600.0 / len(sp)
        fs_kmh = freespeed.get(lid, 0.0) * 3.6
        if (med < args.median_speed
                and fs_kmh > 0 and med < args.speed_ratio * fs_kmh
                and spt > args.seconds
                and len(sp) >= args.min_traversals):
            rows.append((lid, hours[lid], med, spt, len(sp)))
    rows.sort(key=lambda r: -r[1])

    print(f"\n[deadlock] criterion: median background speed < {args.median_speed} km/h "
          f"AND mean time per traversal > {args.seconds:.0f} s")
    print(f"[deadlock] {len(rows)} links qualify\n")
    print(f"{'link':>8} {'veh-hours':>10} {'median km/h':>12} {'s/traversal':>12} "
          f"{'n':>6} {'len m':>7} {'cap':>7}  name")
    for lid, h, med, spt, n in rows:
        print(f"{lid:>8} {h:10.1f} {med:12.3f} {spt:12.0f} {n:>6} "
              f"{length[lid]:7.1f} {capacity[lid]:7.0f}  {name.get(lid, '')[:28]}")

    out = Path(args.out) if args.out else (root / Path(preset.base_config).parent
                                           / "deadlock_links.txt")
    header = (f"# deadlock links excluded from the vehicle-hours metrics\n"
              f"# criterion: median background speed < {args.median_speed} km/h "
              f"AND < {args.speed_ratio:.0%} of the link free speed "
              f"AND mean time per traversal > {args.seconds:.0f} s "
              f"AND at least {args.min_traversals} background traversals\n"
              f"# measured on: {base.name} (LONGBASE, no freight, no vans)\n"
              f"# {len(rows)} links, {sum(r[1] for r in rows):.1f} background vehicle-hours\n")
    out.write_text(header + "\n".join(r[0] for r in rows) + "\n", encoding="utf-8")
    print(f"\n[deadlock] wrote {len(rows)} links -> {out}")

    # how much each reported set loses
    scen_dir = root / Path(preset.base_config).parent
    bad = {r[0] for r in rows}
    print(f"\n{'set':>18} {'links':>6} {'veh-hours':>10} {'excluded':>9} {'share':>7}")
    for fname in ("corridor_links", "bus_stop_links", "khop_ring1", "khop_ring2",
                  "khop_ring3", "khop_cum1", "khop_cum2", "khop_cum3"):
        p = scen_dir / f"{fname}.txt"
        if not p.exists():
            continue
        S = {l.strip() for l in p.read_text(encoding="utf-8").splitlines()
             if l.strip() and not l.startswith("#")}
        tot = sum(hours[l] for l in S)
        exc = sum(hours[l] for l in S & bad)
        print(f"{fname:>18} {len(S):>6} {tot:10.1f} {exc:9.1f} "
              f"{100 * exc / tot if tot else 0:6.1f}%")


if __name__ == "__main__":
    main()
