"""
Decompose the bus-stop congestion row, and measure its noise floor.

Why: on the dwell runs the bus-stop row (ROW 2) is monotone in alpha for medium
parcels but NOT for light, where it changes sign twice (-0.7, +4.1, +1.9, -3.8)
with both seeds agreeing — so it is reproducible in the model, not seed noise,
and unexplained.

ROW 2 mixes two effects. Of its 18 links, 3 are also van-corridor links, where
removing the vans RELIEVES congestion, while all 18 carry the bus that now
blocks the lane. This script measures the two subsets separately:

  shared   the 3 links that are both bus-stop and van-corridor  (van + bus)
  busonly  the 15 links that only the bus touches               (bus alone)

If busonly is monotone and shared is erratic, the van effect is contaminating
the row and ROW 2 must be redefined as busonly. If busonly is erratic too, the
problem is in the blocking itself and belongs elsewhere.

It also runs the NULL TEST the row still lacks: the two alpha=0 baselines are
physically identical and differ only by the random seed, so whatever they
disagree by on a link set is that set's noise floor. Nothing below it is a
measurement.

Everything here is post-processing on existing runs; no MATSim needed.

Usage (from project root):
    python python_pipeline/busstop_row_decomp.py
    python python_pipeline/busstop_row_decomp.py --weights light medium --congestion peak
"""

from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd

from corridor_metrics import corridor_background_stats, corridor_delta, load_corridor_links
from parse_events import load_link_attributes, parse_events
from scenario_presets import get_preset

ALPHAS = [0.25, 0.50, 0.75, 1.00]


def events_of(runs_dir: str, cell: str) -> str | None:
    hits = sorted(glob.glob(f"{runs_dir}/{cell}/*output_events.xml.zst"))
    return hits[0] if hits else None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--runs-dir", default="D:/TesiOutputs/ipft_rotterdam_dwell_blocking_runs")
    ap.add_argument("--weights", nargs="*", default=["light", "medium", "heavy"])
    ap.add_argument("--congestion", nargs="*", default=["peak", "offpeak"])
    ap.add_argument("--seeds", nargs="*", type=int, default=[4711, 9876])
    ap.add_argument("--out", default="output/busstop_row_decomp.csv")
    args = ap.parse_args()

    preset = get_preset("rotterdam")
    root = Path(preset.base_config).parent.parent.parent
    corridor = load_corridor_links(root / preset.corridor_links_file)
    busstop = load_corridor_links(root / preset.bus_stop_links_file)
    shared = frozenset(busstop & corridor)          # 3 links: van + bus
    busonly = frozenset(busstop - corridor)         # 15 links: bus alone
    print(f"link sets: busstop {len(busstop)} = shared {len(shared)} + busonly {len(busonly)}")

    link_lengths, _ = load_link_attributes(str(root / preset.network_file))
    sets = {"busstop_all": busstop, "shared_3": shared, "busonly_15": busonly}
    keep = frozenset(corridor | busstop)

    def parse(path: str):
        df, _ = parse_events(path, str(root / preset.network_file), verbose=False,
                             bus_prefixes=preset.transit_prefixes,
                             pax_bus_ids=preset.term_c_bus_ids, keep_link_ids=keep)
        return df

    rows = []
    for w in args.weights:
        for c in args.congestion:
            # Parse each baseline ONCE, then every alpha against it: the 2-entry
            # LRU parse cache keeps the baseline resident while scenarios rotate.
            base_df = {}
            for seed in args.seeds:
                bp = events_of(args.runs_dir, f"alpha000_{c}_{w}_seed{seed}")
                if bp is None:
                    print(f"[skip] no baseline {w}/{c}/{seed}")
                    continue
                base_df[seed] = parse(bp)

            # NULL TEST: two physically identical baselines, seed apart.
            if len(base_df) == 2:
                s1, s2 = args.seeds
                for name, ls in sets.items():
                    a = corridor_background_stats(base_df[s1], ls, link_lengths)
                    b = corridor_background_stats(base_df[s2], ls, link_lengths)
                    d = corridor_delta(a, b)
                    rows.append(dict(weight=w, congestion=c, alpha="NULL", seed="4711v9876",
                                     link_set=name, delta_vh=d["delta_vehicle_hours"]))
                    print(f"  NULL {w}/{c} {name:12s} {d['delta_vehicle_hours']:+7.2f} vh")

            for seed, bdf in base_df.items():
                for a in ALPHAS:
                    sp = events_of(args.runs_dir,
                                   f"alpha{int(a*100):03d}_{c}_{w}_seed{seed}")
                    if sp is None:
                        continue
                    sdf = parse(sp)
                    for name, ls in sets.items():
                        bs = corridor_background_stats(bdf, ls, link_lengths)
                        ss = corridor_background_stats(sdf, ls, link_lengths)
                        d = corridor_delta(bs, ss)
                        # n_traversals and vkm decide between the competing
                        # explanations of "more vehicle-hours but less CO2":
                        # if the scenario has FEWER cars on the set, both the
                        # count and the vkm drop while the survivors are slower,
                        # and the CO2 can fall while the hours rise. If the count
                        # is unchanged, the cause is the emission factor, not the
                        # traffic.
                        rows.append(dict(
                            weight=w, congestion=c, alpha=a, seed=seed, link_set=name,
                            delta_vh=d["delta_vehicle_hours"],
                            delta_n=d["delta_n_traversals"], delta_vkm=d["delta_vkm"],
                            n_base=bs["n_traversals"], n_scen=ss["n_traversals"],
                            vkm_base=bs["vkm"], vkm_scen=ss["vkm"],
                            speed_base=bs["mean_speed_ms"], speed_scen=ss["mean_speed_ms"]))
                    got = {r["link_set"]: r for r in rows[-3:]}
                    b = got["busstop_all"]
                    print(f"  {w}/{c}/{seed}/a={a:.2f}  vh {b['delta_vh']:+7.2f}"
                          f"  passaggi {b['n_base']:6d}->{b['n_scen']:6d} ({b['delta_n']:+5.0f})"
                          f"  vkm {b['delta_vkm']:+7.2f}"
                          f"  v {b['speed_base']:.2f}->{b['speed_scen']:.2f} m/s", flush=True)
                    pd.DataFrame(rows).to_csv(args.out, index=False)

    pd.DataFrame(rows).to_csv(args.out, index=False)
    print(f"\nwrote {len(rows)} rows -> {args.out}")


if __name__ == "__main__":
    main()
