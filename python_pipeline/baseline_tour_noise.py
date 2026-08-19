"""
Baseline tour noise and the error amplification of Section "Why only the full-adoption row is precise".

The chapter claims two numbers that nothing in the pipeline computed:

  1. the two baselines of a block price the same vans and disagree by X to Y kg per tour;
  2. that per-tour disagreement is amplified by (baseline tours + scenario tours)
     / (baseline tours - scenario tours) when the two fleets are subtracted.

This script derives both from results_long.csv, so the section has a source.

WHY alpha=1.0 IS THE MEASUREMENT. Term B is
    component1 (removed tours, priced at BASELINE v_mean)
  - component2 (backup tours, priced at SCENARIO v_mean).
At full adoption no parcel is left on the road, component2 is zero, and term_b_kg
is exactly the baseline van fleet priced under that seed. Running the same block
under seed 4711 and 9876 prices the SAME vans on the SAME network twice, so the
gap between them is pure simulation noise: nothing physical has changed. Divided
by the tour count it is the per-tour error E used in the section.

At any other alpha the two components mix baseline and scenario pricing, and the
difference would no longer isolate the baseline.

The idle component of a tour (van_stop_idle_s at the pickup stops) is
deterministic and identical in both seeds, so it cancels in the difference: what
survives is the driving part, which is what the seed actually perturbs.

Run from project root:
    python python_pipeline/baseline_tour_noise.py
    python python_pipeline/baseline_tour_noise.py --out output/baseline_tour_noise.csv
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from parameters import ALPHA_VALUES, WEIGHT_REGIMES, c_van
from scenario_presets import get_preset

DEFAULT_RESULTS = "output/sensitivity_rotterdam_dwell_blocking/results_long.csv"


def tours(n_units: int, weight_regime: str, alpha: float) -> int:
    """Van tours still driven at this adoption rate, consolidated as Term B counts them."""
    remaining = (1.0 - alpha) * n_units
    if remaining <= 0:
        return 0
    return math.ceil(remaining / c_van(WEIGHT_REGIMES[weight_regime]))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--results", default=DEFAULT_RESULTS,
                    help="results_long.csv of the surface the chapter reports")
    ap.add_argument("--out", default=None, help="optional CSV to write the per-block table to")
    args = ap.parse_args()

    n_units = get_preset("rotterdam").n_freight_units_sim
    df = pd.read_csv(args.results)

    full = df[df.alpha == 1.0]
    if full.empty:
        sys.exit(f"no alpha=1.0 rows in {args.results}: the baseline cannot be isolated")

    rows = []
    for (cong, wreg), g in full.groupby(["congestion", "weight_regime"]):
        if len(g) != 2:
            print(f"[skip] {cong}/{wreg}: {len(g)} seed(s), need 2")
            continue
        n_base = tours(n_units, wreg, 0.0)
        per_tour = (g.set_index("seed")["term_b_kg"] / n_base).sort_index()
        s_lo, s_hi = per_tour.index
        rows.append(dict(
            congestion=cong, weight_regime=wreg, baseline_tours=n_base,
            kg_per_tour_seed1=per_tour[s_lo], kg_per_tour_seed2=per_tour[s_hi],
            kg_per_tour_mean=per_tour.mean(),
            noise_kg_per_tour=abs(per_tour[s_hi] - per_tour[s_lo]),
            noise_pct=100 * abs(per_tour[s_hi] - per_tour[s_lo]) / per_tour.mean(),
        ))

    t = pd.DataFrame(rows).sort_values(["congestion", "weight_regime"])

    print("\nPER-TOUR BASELINE NOISE  (alpha=1.0: term_b_kg is the baseline fleet alone)")
    print("  the same vans, the same network, two seeds -> the gap is simulation noise\n")
    print(t.to_string(index=False, float_format=lambda v: f"{v:8.3f}"))

    lo, hi = t.noise_kg_per_tour.min(), t.noise_kg_per_tour.max()
    print(f"\n  per-tour noise E ranges {lo:.2f} to {hi:.2f} kg  "
          f"({t.noise_pct.min():.1f}% to {t.noise_pct.max():.1f}% of a tour)")
    print(f"  worst case rounded up for the chapter: E = {math.ceil(hi * 100) / 100:.2f} "
          f"-> {math.ceil(hi):.0f} kg per tour")

    # ── amplification: how the per-tour error survives the fleet subtraction ──
    print("\n\nERROR AMPLIFICATION  (worst case: every priced tour wrong by E, signs aligned "
          "against each other across the subtraction)\n")
    amp_rows = []
    for r in t.itertuples():
        for a in sorted(x for x in ALPHA_VALUES if x > 0):
            n_scen = tours(n_units, r.weight_regime, a)
            priced, answer = r.baseline_tours + n_scen, r.baseline_tours - n_scen
            saving = r.kg_per_tour_mean * answer
            amp_rows.append(dict(
                congestion=r.congestion, weight_regime=r.weight_regime, alpha=a,
                tours_priced=priced, tours_in_answer=answer,
                amplification=priced / answer,
                saving_kg=saving,
                error_kg=priced * r.noise_kg_per_tour,
                error_pct=100 * priced * r.noise_kg_per_tour / saving,
            ))
    amp = pd.DataFrame(amp_rows)
    print(amp.to_string(index=False, float_format=lambda v: f"{v:8.2f}"))

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        t.to_csv(args.out, index=False)
        amp.to_csv(Path(args.out).with_name(Path(args.out).stem + "_amplification.csv"),
                   index=False)
        print(f"\nWROTE {args.out} and its _amplification companion")


if __name__ == "__main__":
    main()
