"""
Gate check: did the inserted vans actually drive the tour they were given?

Run this on the alpha=0 cell of a new line BEFORE launching its full battery.
A van that is routed onto a link no car can enter, or that takes the fastest road
and skips the lockers, produces a run that completes without any error and whose
numbers mean nothing. That failure mode is why line 44's van depot is a car link
79 m from the bus terminus rather than the terminus itself, and it is the single
check that has to pass before a 13-hour battery is worth starting.

Three things are verified, and all three have to hold:

  1. the expected number of van tours appears in the events;
  2. every locker link in the preset is traversed by at least one van;
  3. each van reaches the terminal link.

Exit code 0 = safe to launch the battery, 1 = do not launch.

Usage (from project root):
    python python_pipeline/check_van_routing.py --scenario rotterdam_L87 \
        --run D:/TesiOutputs/ipft_rotterdam_L87_dwell_blocking_runs/alpha000_peak_medium_seed4711
"""

from __future__ import annotations

import argparse
import collections
import glob
import io
import re
import sys
from pathlib import Path

import zstandard

sys.path.insert(0, str(Path(__file__).parent))

from parameters import WEIGHT_REGIMES, c_van
from scenario_presets import get_preset

_RX = re.compile(r'link="([^"]+)"\s+vehicle="([^"]+)"|vehicle="([^"]+)"\s+link="([^"]+)"')


def van_links(events: Path) -> tuple[dict, collections.Counter]:
    """{van_id: ordered list of links}, and how many vans touched each link."""
    seen: dict = collections.defaultdict(list)
    per_link = collections.Counter()
    with open(events, "rb") as f:
        r = io.TextIOWrapper(zstandard.ZstdDecompressor().stream_reader(f), encoding="utf-8")
        for line in r:
            if "backup_van_" not in line or 'type="entered link"' not in line:
                continue
            m = _RX.search(line)
            if not m:
                continue
            lid = m.group(1) or m.group(4)
            veh = m.group(2) or m.group(3)
            if not veh.startswith("backup_van_"):
                continue
            if not seen[veh] or seen[veh][-1] != lid:
                seen[veh].append(lid)
            per_link[lid] += 1
    return seen, per_link


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--run", required=True, help="the alpha=0 run directory")
    ap.add_argument("--weight", default="medium")
    args = ap.parse_args()

    preset = get_preset(args.scenario)
    hits = sorted(glob.glob(f"{args.run}/*output_events.xml.zst"))
    if not hits:
        print(f"FAIL: no events file in {args.run}")
        return 1

    lockers = [l for l, _, _ in preset.van_locker_stops]
    expected_tours = -(-preset.n_freight_units_sim // c_van(WEIGHT_REGIMES[args.weight]))

    print(f"scenario   : {preset.name}")
    print(f"run        : {Path(args.run).name}")
    print(f"expected   : {expected_tours} van tours, {len(lockers)} lockers, "
          f"terminal {preset.terminal_link}")

    seen, per_link = van_links(Path(hits[0]))
    ok = True

    print(f"\n1) van tours found: {len(seen)}")
    if len(seen) != expected_tours:
        print(f"   FAIL: expected {expected_tours}")
        ok = False
    else:
        print("   ok")

    missed = [l for l in lockers if per_link[l] == 0]
    print(f"\n2) lockers touched: {len(lockers) - len(missed)} of {len(lockers)}")
    for l in lockers:
        mark = "ok " if per_link[l] else "MISSED"
        print(f"   {l:>8} {mark} ({per_link[l]} van traversals)")
    if missed:
        print(f"   FAIL: {len(missed)} lockers never visited: {missed}")
        ok = False

    reached = sum(1 for v, path in seen.items() if preset.terminal_link in path)
    print(f"\n3) vans reaching the terminal: {reached} of {len(seen)}")
    if seen and reached != len(seen):
        print("   FAIL: some vans never reach the terminal link")
        ok = False
    else:
        print("   ok")

    lens = [len(p) for p in seen.values()]
    if lens:
        print(f"\ntour length: {min(lens)}-{max(lens)} distinct links per van")

    print("\n" + ("PASS - safe to launch the battery" if ok
                  else "FAIL - do NOT launch the battery"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
