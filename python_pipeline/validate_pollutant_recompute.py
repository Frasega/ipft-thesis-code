"""
Validate the offline pollutant recompute against MATSim's own emission events.

Why this is possible at all. Rotterdam runs with isWritingEmissionsEvents=false
(the full log is ~8 GB/run), so there is nothing to compare against there. The
TOY runs were written with it TRUE, and their events carry the whole HBEFA
attribute set - CO2_TOTAL, NOx, NO2, PM2_5, ... - one attribute per pollutant
per warmEmissionEvent. So the toy gives a free, exact reference for the very
thing recompute_link_co2.py reconstructs from linkEnter/linkLeave.

What is compared. For every background link traversal, MATSim's own per-link
pollutant grams (summed from warmEmissionEvent) against what
`emissions_by_set` computes from the same run's speeds and the HBEFA table.
Warm only, background only:
  - cold start is excluded (coldEmissionEvent is a separate event type, and the
    recompute does not model it);
  - transit is excluded (MatsimModelImplementation maps transit vehicles to
    NON_HBEFA_VEHICLE, so their events are all zeros);
  - vans are excluded (they emit in MATSim but Term B recomputes them from
    longitudinal dynamics, so the background recompute must not count them).

THE PARKING LINK, and why the raw totals cannot match. The recompute needs a
matched 'entered link' + 'left link' pair to get a traversal time, and the LAST
link of a leg never has one: the vehicle parks there, so MATSim emits
'vehicle leaves traffic' instead of 'left link' and parse_events drops the
pending entry (its counter "legs ended on a link (parked, entry dropped)").
MATSim does emit a warmEmissionEvent for that link. Measured on the toy:
970,507 entered-link events, 36,753 parked, 933,754 records - exact. Background
only, that is 34,391 traversals, 3.8% of them, worth 3.1% of the CO2.

So this script reports TWO numbers, and they answer different questions:

  like-for-like  the same population on both sides (parking-link warm events
                 removed from the reference). This is the real test of the
                 LOOKUP - whether reading NOx out of the HBEFA table reproduces
                 what MATSim computed. It should agree to well under a percent.

  coverage       how much of MATSim's warm total the recompute can see at all.
                 A property of the CO2 path that predates this file and is
                 unchanged by it; every background CO2 figure already carries
                 it. It largely cancels in a baseline-scenario delta, because
                 the plans are frozen and the same agents park on the same
                 links in both runs - the same argument already made for cold
                 start in recompute_link_co2.py.

The toy ran AverageSpeed, so the matching column is the `_average_kg` one. That
is the whole point of run_computation_method(): the reference and the recompute
have to be asked the same question.

Usage (from project root):
    python python_pipeline/validate_pollutant_recompute.py
    python python_pipeline/validate_pollutant_recompute.py --run <toy run dir>
"""

from __future__ import annotations

import argparse
import glob
import io
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd

from parse_events import load_link_attributes, parse_events
from recompute_link_co2 import (
    COMPONENTS,
    emissions_by_set,
    load_hbefa,
    method_column,
    run_computation_method,
)
from scenario_presets import OUTPUT_ROOT, get_preset

# HBEFA `Component` string -> MATSim event attribute name. MATSim flattens the
# punctuation: "CO2(total)" becomes CO2_TOTAL and "PM2.5" becomes PM2_5. Note
# that PM2_5 is exhaust only; PM2_5_non_exhaust is a separate attribute and a
# separate table row, and neither this file nor the recompute touches it.
EVENT_ATTR = {
    "CO2(total)": "CO2_TOTAL",
    "NOx": "NOx",
    "NO2": "NO2",
    "PM2.5": "PM2_5",
}

TOL_PCT = 1.0  # the recompute reconstructs speeds from link enter/leave, so it
# will never be bit-identical; anything under a percent means the lookup agrees


def open_events(path: str):
    if path.endswith(".zst"):
        import zstandard as zstd
        return io.TextIOWrapper(
            zstd.ZstdDecompressor().stream_reader(open(path, "rb")), encoding="utf-8")
    if path.endswith(".gz"):
        import gzip
        return gzip.open(path, "rt", encoding="utf-8")
    return open(path, encoding="utf-8")


def matsim_totals(events_path: str, link_ids: frozenset,
                  transit_prefixes: tuple[str, ...], van_prefix: str) -> dict:
    """
    Sum MATSim's own warm emission grams over background vehicles on link_ids.

    Returns totals twice: `full` (every warm event) and `matched` (parking-link
    warm events removed, i.e. the population the recompute is able to see).
    A vehicle parks on the link named by its 'vehicle leaves traffic' event, and
    the warm event for that traversal carries the same time stamp, so the pair
    (vehicleId, linkId, time) identifies it exactly.
    """
    parked: set[tuple[str, str, str]] = set()
    with open_events(events_path) as f:
        for line in f:
            if 'type="vehicle leaves traffic"' not in line:
                continue
            d = dict(re.findall(r'(\w+)="([^"]*)"', line))
            parked.add((d.get("vehicle", ""), d.get("link", ""), d.get("time", "")))

    full: dict[str, float] = defaultdict(float)
    matched: dict[str, float] = defaultdict(float)
    n_full = n_matched = 0
    with open_events(events_path) as f:
        for line in f:
            if "warmEmissionEvent" not in line:
                continue
            d = dict(re.findall(r'(\w+)="([^"]*)"', line))
            lid = d.get("linkId")
            if lid not in link_ids:
                continue
            vid = d.get("vehicleId", "")
            if vid.startswith(van_prefix) or vid.startswith(transit_prefixes):
                continue
            n_full += 1
            is_parked = (vid, lid, d.get("time", "")) in parked
            if not is_parked:
                n_matched += 1
            for comp, attr in EVENT_ATTR.items():
                g = float(d.get(attr, 0) or 0)
                full[comp] += g
                if not is_parked:
                    matched[comp] += g
    return {"full": full, "matched": matched,
            "n_full": n_full, "n_matched": n_matched, "n_parked_events": len(parked)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--run", default=None,
                    help="toy run directory (default: first toy screening run found)")
    ap.add_argument("--scenario", default="toy")
    args = ap.parse_args()

    run = args.run
    if run is None:
        cands = sorted(glob.glob(str(OUTPUT_ROOT / "ipft_toy_screening_runs" / "*") + "/"))
        if not cands:
            raise SystemExit("no toy screening runs found; pass --run explicitly")
        run = cands[0].rstrip("/\\")
    print(f"run: {run}")

    method = run_computation_method(run)
    col_of = lambda slug: method_column(method, slug)
    print(f"lookup rule this run used: {method} -> comparing '{col_of('co2')}' etc.")

    ev = sorted(glob.glob(f"{run}/*output_events.xml*"))
    ev = [p for p in ev if "output_events" in p]
    if not ev:
        raise SystemExit(f"no events in {run}")

    preset = get_preset(args.scenario)
    root = Path(preset.base_config).parent.parent.parent
    scen = Path(preset.base_config).parent
    hbefa = load_hbefa(scen)
    net = str(root / preset.network_file)
    lengths, freespeeds = load_link_attributes(net)

    # The whole network is the link set: this is a validation of the lookup, so
    # restricting it to a corridor would only shrink the sample.
    all_links = frozenset(lengths.keys())
    print(f"network: {len(all_links)} links")

    df, _ = parse_events(ev[0], net, verbose=False,
                         bus_prefixes=preset.transit_prefixes,
                         pax_bus_ids=preset.term_c_bus_ids)
    mine = emissions_by_set(df, lengths, freespeeds, hbefa,
                            {"network": all_links}).iloc[0]

    ref = matsim_totals(ev[0], all_links, preset.transit_prefixes, "backup_van_")
    n_rec = int(mine["n_traversals"])
    print(f"MATSim warm events, background       : {ref['n_full']}")
    print(f"  of which on a parking link         : {ref['n_full'] - ref['n_matched']}")
    print(f"  comparable population              : {ref['n_matched']}")
    print(f"recompute traversals, background     : {n_rec}")
    if ref["n_matched"] != n_rec:
        print(f"  !! population still differs by {ref['n_matched'] - n_rec}; the "
              f"like-for-like column below is not exactly like-for-like")
    print()

    print(f"{'component':12s} {'MATSim like4like':>18s} {'recompute':>12s} {'gap':>9s}"
          f" {'| MATSim full':>15s} {'coverage':>10s}")
    worst = 0.0
    for comp, slug in COMPONENTS.items():
        a = ref["matched"][comp] / 1000.0
        b = float(mine[col_of(slug)])
        f_all = ref["full"][comp] / 1000.0
        gap = 100 * (b - a) / a if a else float("nan")
        cov = 100 * b / f_all if f_all else float("nan")
        worst = max(worst, abs(gap))
        print(f"{comp:12s} {a:18.4f} {b:12.4f} {gap:+8.2f}% {f_all:15.4f} {cov:9.1f}%")

    print()
    if worst <= TOL_PCT:
        print(f"[OK] on the same population every component agrees with MATSim to "
              f"within {worst:.2f}% (tolerance {TOL_PCT}%). Reading NOx, NO2 and "
              f"PM2.5 out of the HBEFA table reproduces what MATSim itself "
              f"computed, so those pollutants on the Rotterdam events are exactly "
              f"as trustworthy as the CO2 already reported.")
        print(f"     The coverage column is the separate, pre-existing parking-link "
              f"shortfall described in the docstring: it applies identically to the "
              f"CO2 already in the thesis, and it cancels in a frozen-plan delta.")
    else:
        print(f"[FAIL] worst like-for-like gap {worst:.2f}% exceeds the {TOL_PCT}% "
              f"tolerance. Do not report recomputed pollutants until this is "
              f"understood.")
        sys.exit(1)


if __name__ == "__main__":
    main()
