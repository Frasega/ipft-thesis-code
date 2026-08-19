"""
How much of the lay-by saving belongs to IPFT.

The bay battery measures what a lay-by saves on the bus-stop links: the
blocking battery minus the bay battery, at the same load rate. That figure is
NOT the freight operation's own saving. Two things sit inside it:

  - the ordinary passenger stop, which happens with or without IPFT;
  - ten other transit lines, because isBlocking is a property of the stop
    facility and not of the line, and the 9 flagged facilities are shared.

This script splits the measured figure by blocking seconds. For every scenario
it reads the events, sums the time each line stands at the 9 blocking
facilities, and takes IPFT to be what line 44 stands there ABOVE its own
alpha=0 stop time. That difference is the surviving freight dwell of that
scenario, so the split uses the seconds of each cell and never a single
headline number.

    share   = (line44 seconds at alpha - line44 seconds at alpha=0)
              -------------------------------------------------------
              (all lines, all 9 facilities, seconds at alpha)

    saving  = share x measured blocking-minus-bay CO2 of that cell

The one assumption is global proportionality: a second of blocking is worth the
same kilograms wherever and whenever it happens. It is stated rather than
tested, and it is what makes a single share meaningful.

Everything here is post-processing on runs that already exist.

Usage (from project root):
    python python_pipeline/bay_ipft_share.py
    python python_pipeline/bay_ipft_share.py --congestion peak --weights light
    python python_pipeline/bay_ipft_share.py --deltas-csv my_deltas.csv
"""

from __future__ import annotations

import argparse
import collections
import glob
import gzip
import io
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import zstandard as zstd

LINE44 = "99437"
ALPHAS = [0.25, 0.50, 0.75, 1.00]

# Measured blocking-minus-bay CO2 on the 14 bus-stop links, kg per day at real
# scale, mean over the two seeds. These are the numbers of the bay table in
# Chapter 6. Override with --deltas-csv (columns: congestion,weight,alpha,kg).
DELTAS_KG = {
    ("peak", "light"):    {0.00: 319, 0.25: 327, 0.50: 325, 0.75: 341, 1.00: 370},
    ("peak", "medium"):   {0.00: 334, 0.25: 322, 0.50: 335, 0.75: 347, 1.00: 370},
    ("peak", "heavy"):    {0.00: 345, 0.25: 350, 0.50: 340, 0.75: 357, 1.00: 370},
    ("offpeak", "light"): {0.00: 71,  0.25: 67,  0.50: 72,  0.75: 72,  1.00: 80},
    ("offpeak", "medium"): {0.00: 70, 0.25: 69,  0.50: 68,  0.75: 73,  1.00: 80},
    ("offpeak", "heavy"): {0.00: 73,  0.25: 68,  0.50: 74,  0.75: 72,  1.00: 80},
}


def _open_maybe_zst(path: Path):
    """Return a decompressed binary stream for .zst, .gz or plain XML."""
    name = str(path)
    if name.endswith(".zst"):
        fh = open(path, "rb")
        return zstd.ZstdDecompressor().stream_reader(fh)
    if name.endswith(".gz"):
        return gzip.open(path, "rb")
    return open(path, "rb")


def cell_dir(runs_dir: str, alpha: float, congestion: str, weight: str, seed: int) -> Path | None:
    tag = f"alpha{int(round(alpha * 100)):03d}_{congestion}_{weight}_seed{seed}"
    hits = sorted(glob.glob(f"{runs_dir}/{tag}"))
    return Path(hits[0]) if hits else None


def one_file(cell: Path, suffix: str) -> Path | None:
    hits = sorted(cell.glob(f"*{suffix}"))
    return hits[0] if hits else None


def blocking_facilities(schedule_path: Path) -> frozenset[str]:
    """The stop facilities carrying isBlocking=true. Nine of them in this design."""
    with _open_maybe_zst(schedule_path) as reader:
        root = ET.fromstring(reader.read())
    return frozenset(f.get("id") for f in root.iter("stopFacility")
                     if f.get("isBlocking") == "true")


def stop_seconds_by_line(events_path: Path, facilities: frozenset[str]) -> dict[str, float]:
    """Seconds each transit line stands at the given facilities, over the whole day.

    Streams the events. TransitDriverStarts maps a vehicle to its line;
    VehicleArrivesAtFacility and VehicleDepartsAtFacility bracket one stop.
    """
    veh_line: dict[str, str] = {}
    arrived: dict[tuple[str, str], float] = {}
    seconds: dict[str, float] = collections.defaultdict(float)

    with _open_maybe_zst(events_path) as reader:
        for _, elem in ET.iterparse(io.BufferedReader(reader), events=("end",)):
            if elem.tag != "event":
                continue
            etype = elem.get("type")
            if etype == "TransitDriverStarts":
                veh_line[elem.get("vehicleId")] = elem.get("transitLineId")
            elif etype == "VehicleArrivesAtFacility":
                fac = elem.get("facility")
                if fac in facilities:
                    arrived[(elem.get("vehicle"), fac)] = float(elem.get("time"))
            elif etype == "VehicleDepartsAtFacility":
                fac = elem.get("facility")
                if fac in facilities:
                    key = (elem.get("vehicle"), fac)
                    t0 = arrived.pop(key, None)
                    if t0 is not None:
                        line = veh_line.get(elem.get("vehicle"), "unknown")
                        seconds[line] += float(elem.get("time")) - t0
            elem.clear()
    return dict(seconds)


def load_deltas(path: str | None) -> dict:
    if not path:
        return DELTAS_KG
    df = pd.read_csv(path)
    out: dict = collections.defaultdict(dict)
    for r in df.itertuples():
        out[(r.congestion, r.weight)][float(r.alpha)] = float(r.kg)
    return dict(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--runs-dir", default="D:/TesiOutputs/ipft_rotterdam_dwell_blocking_runs")
    ap.add_argument("--weights", nargs="*", default=["light", "medium", "heavy"])
    ap.add_argument("--congestion", nargs="*", default=["peak", "offpeak"])
    ap.add_argument("--seeds", nargs="*", type=int, default=[4711, 9876])
    ap.add_argument("--line", default=LINE44, help="the transit line under study")
    ap.add_argument("--deltas-csv", default=None,
                    help="measured blocking-minus-bay kg per cell; defaults to the chapter table")
    ap.add_argument("--out", default="output/bay_ipft_share.csv")
    args = ap.parse_args()

    deltas = load_deltas(args.deltas_csv)
    rows = []

    for cong in args.congestion:
        for weight in args.weights:
            for seed in args.seeds:
                base = cell_dir(args.runs_dir, 0.0, cong, weight, seed)
                if base is None:
                    print(f"[skip] no alpha=0 run for {cong}/{weight}/{seed}", flush=True)
                    continue
                sched = one_file(base, "output_transitSchedule.xml.zst")
                facs = blocking_facilities(sched)
                if not facs:
                    print(f"[skip] no blocking facilities in {base.name}", flush=True)
                    continue

                base_sec = stop_seconds_by_line(one_file(base, "output_events.xml.zst"), facs)
                line_base = base_sec.get(args.line, 0.0)
                print(f"{cong}/{weight}/{seed}: {len(facs)} facilities, "
                      f"line {args.line} stands {line_base/60:.1f} min at alpha=0", flush=True)

                for alpha in ALPHAS:
                    cell = cell_dir(args.runs_dir, alpha, cong, weight, seed)
                    if cell is None:
                        continue
                    sec = stop_seconds_by_line(one_file(cell, "output_events.xml.zst"), facs)
                    line_sec = sec.get(args.line, 0.0)
                    total_sec = sum(sec.values())
                    ipft_sec = line_sec - line_base
                    share = ipft_sec / total_sec if total_sec else float("nan")
                    kg = deltas.get((cong, weight), {}).get(alpha)
                    rows.append(dict(
                        congestion=cong, weight=weight, seed=seed, alpha=alpha,
                        line_seconds=line_sec, line_seconds_alpha0=line_base,
                        ipft_seconds=ipft_sec, all_lines_seconds=total_sec,
                        other_lines_seconds=total_sec - line_sec,
                        ipft_share=share,
                        measured_kg=kg,
                        ipft_kg=share * kg if kg is not None else None))
                    print(f"   alpha={alpha:.2f}  IPFT {ipft_sec/60:7.1f} min of "
                          f"{total_sec/60:8.1f} min  share {share:6.2%}"
                          + (f"  -> {share*kg:6.1f} kg of {kg}" if kg is not None else ""),
                          flush=True)

    if not rows:
        print("nothing computed", file=sys.stderr)
        return

    df = pd.DataFrame(rows)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out, index=False)

    print(f"\n{len(df)} rows -> {args.out}")
    print("\nmean over seeds, kg per day attributable to IPFT:")
    piv = df.groupby(["congestion", "weight", "alpha"])[["ipft_share", "ipft_kg"]].mean()
    print(piv.to_string(float_format=lambda v: f"{v:.3f}"))


if __name__ == "__main__":
    main()
