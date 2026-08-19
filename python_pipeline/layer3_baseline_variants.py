"""
Price the reconstruction-draw and van-load sweeps at full load, one parse per run.

At full load the scenario carries no vans, so Term B collapses to its first
component, which reads the BASELINE run alone:

    Term B(alpha=1) = mean CO2 of one baseline van tour x tours + tour idle

Both remaining free sweeps change only how that mean is evaluated: the
reconstruction seed redraws the micro-trips, and the van-load factor evaluates the
tour at the departure mass instead of the time-averaged one. Neither touches the
scenario run, and neither touches Terms A or C on the van side.

Running them through the pipeline would parse two event files per cell, 18 minutes
each, for three sweeps of twelve cells. This script parses each of the twelve
baseline runs ONCE and evaluates every variant on that same dataframe, which is
the same arithmetic in a fifth of the time.

It is validated before it is believed: the default variant (seed 42, mean load)
must reproduce the full-load Term B of the headline surface for all twelve cells.
If it does, the parse and every constant behind it match the pipeline, and the
other variants differ from it only by the knob.

Term C also carries the reconstruction seed, through the bus profile, but it needs
the scenario run and moves by under a tenth of a kilogram; it is reported as
carried over, not recomputed, and the output says so.

Usage:  python python_pipeline/layer3_baseline_variants.py
        (writes output/layer3_baseline_variants.csv, ~2 h)
"""

from __future__ import annotations

import glob
import sys
from pathlib import Path

PIPE = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPE))

import pandas as pd

from corridor_metrics import load_corridor_links
from parse_events import parse_events
from parameters import VAN_LOAD_FACTOR, VAN_STOP_IDLE_S, WEIGHT_REGIMES
from scenario_presets import get_preset
from term_b import compute_component1

RUNS = Path("D:/TesiOutputs/ipft_rotterdam_dwell_blocking_runs")
ROOT = PIPE.parent
OUT = ROOT / "output" / "layer3_baseline_variants.csv"
HEADLINE = ROOT / "output" / "sensitivity_rotterdam_dwell_blocking" / "results_long.csv"

SEEDS = [4711, 9876]
CONG = ["peak", "offpeak"]
WEIGHTS = ["light", "medium", "heavy"]
# (label, reconstruction seed, load factor). The first is the headline itself.
VARIANTS = [("headline", 42, VAN_LOAD_FACTOR),
            ("recon_seed7", 7, VAN_LOAD_FACTOR),
            ("recon_seed123", 123, VAN_LOAD_FACTOR),
            ("load_full", 42, 1.0)]

preset = get_preset("rotterdam")
SCALE = 1.0 / preset.sample_rate
NET = str(ROOT / preset.network_file)


def baseline_events(cong: str, weight: str, seed: int) -> str | None:
    hits = sorted(glob.glob(str(RUNS / f"alpha000_{cong}_{weight}_seed{seed}"
                                 / "*output_events.xml.zst")))
    return hits[0] if hits else None


def main() -> None:
    keep = frozenset(load_corridor_links(preset.corridor_links_file)
                     | load_corridor_links(preset.bus_stop_links_file))
    head = pd.read_csv(HEADLINE)
    head = head[head.alpha == 1.0]

    rows = []
    for cong in CONG:
        for weight in WEIGHTS:
            for seed in SEEDS:
                path = baseline_events(cong, weight, seed)
                if path is None:
                    print(f"[skip] no baseline for {cong}/{weight}/seed{seed}", flush=True)
                    continue
                print(f"[parse] {cong}/{weight}/seed{seed}", flush=True)
                vmean, _ = parse_events(
                    path, NET, verbose=False,
                    bus_prefixes=preset.transit_prefixes,
                    pax_bus_ids=preset.term_c_bus_ids,
                    keep_link_ids=keep,
                )
                ref = head[(head.congestion == cong) & (head.weight_regime == weight)
                           & (head.seed == seed)]
                ref_b = float(ref.term_b_kg.iloc[0]) if len(ref) else float("nan")
                ref_c = float(ref.term_c_kg.iloc[0]) if len(ref) else float("nan")

                for label, rseed, load in VARIANTS:
                    c1, proxy = compute_component1(
                        baseline_vmean_df=vmean,
                        n_total_vans=preset.n_freight_units_sim,
                        weight_per_unit_kg=WEIGHT_REGIMES[weight],
                        n_pickup_stops=preset.n_pickup_stops,
                        van_stop_idle_s=VAN_STOP_IDLE_S,
                        load_factor=load,
                        rng_seed=rseed,
                    )
                    term_b = c1 * SCALE
                    rows.append(dict(variant=label, congestion=cong, weight_regime=weight,
                                     seed=seed, alpha=1.0, recon_seed=rseed, load_factor=load,
                                     term_b_kg=term_b, used_proxy=proxy,
                                     term_b_headline_kg=ref_b,
                                     term_c_kg_carried=ref_c,
                                     net_robust_kg_per_day=term_b - ref_c))
                    if label == "headline":
                        err = abs(term_b - ref_b)
                        flag = "OK" if err < 1e-6 else "MISMATCH"
                        print(f"   [validate] {flag}: {term_b:.4f} vs surface "
                              f"{ref_b:.4f} (diff {err:.2e})", flush=True)
                pd.DataFrame(rows).to_csv(OUT, index=False)

    df = pd.DataFrame(rows)
    base = df[df.variant == "headline"].set_index(["congestion", "weight_regime", "seed"])
    print("\n=== full load, change in Term B against the headline ===")
    for label, _, _ in VARIANTS[1:]:
        sub = df[df.variant == label].set_index(["congestion", "weight_regime", "seed"])
        pct = (sub.term_b_kg / base.term_b_kg - 1) * 100
        print(f"{label:16s}: {pct.min():+6.2f}% .. {pct.max():+6.2f}%  "
              f"(median {pct.median():+.2f}%, n={len(pct)})")
    print(f"\nsaved -> {OUT}")


if __name__ == "__main__":
    main()
