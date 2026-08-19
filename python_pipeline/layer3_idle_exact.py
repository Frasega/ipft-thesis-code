"""
Rebuild the van delivery-stop idle sweeps EXACTLY, without re-parsing anything.

Why this exists. The idle bracket enters Term B in closed form:

    Term B(x) = [m_base·T_base − m_scen·T_scen] + (T_base − T_scen)·i(x)

where x is the per-stop idle seconds, i(x) = idle CO2 of one tour at x s/stop
(linear in x), and T = the consolidated tour counts, which are integers fixed by
alpha and the weight regime. Only the second bracket depends on x, so any point of
the sweep follows from the headline surface (x = 10 s) by arithmetic:

    Term B(x) = Term B(10) + (T_base − T_scen)·i(10)·(x/10 − 1)

Re-running the pipeline for this costs 18 minutes per cell and returns the same
number. That is how the sweeps were priced on 2026-08-16, and those runs came back
with the scenario side evaluated on the baseline run, which is what this script
replaces.

The formula is not asserted, it is checked twice before it is used:
  1. i(10) is computed from parameters (idle rate x stops x sample scaling) and
     must agree with the value implied by the alpha=1 rows of the pipeline sweeps;
  2. the reconstruction must reproduce those alpha=1 rows exactly. They are the
     rows the fault cannot touch: at full load the scenario carries no vans, so
     the scenario term is zero whatever dataframe is handed to it.

Usage:  python python_pipeline/layer3_idle_exact.py
        (writes output/layer3_idle_zero and output/layer3_idle_high)
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

PIPE = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPE))

import pandas as pd

from emission_formula import compute_co2_idle
from parameters import VAN_IDLE_FUEL_RATE_L_PER_S, WEIGHT_REGIMES, c_van
from scenario_presets import get_preset

ROOT = PIPE.parent
OUT = ROOT / "output"
HEADLINE = OUT / "sensitivity_rotterdam_dwell_blocking" / "results_long.csv"
# The pipeline sweeps: only their alpha=1 rows are used, and only to validate.
PIPELINE_SWEEPS = {0.0: OUT / "layer3_idle_zero_BROKEN_20260816" / "results_long.csv",
                   100.0: OUT / "layer3_idle_high_BROKEN_20260816" / "results_long.csv"}
TARGETS = {0.0: OUT / "layer3_idle_zero", 100.0: OUT / "layer3_idle_high"}
HEADLINE_IDLE_S = 10.0

preset = get_preset("rotterdam")
N_SIM = preset.n_freight_units_sim
N_STOPS = preset.n_pickup_stops
SCALE = 1.0 / preset.sample_rate


def tours(alpha: float, weight_regime: str) -> int:
    """Consolidated tours left on the road at this load rate (sim scale)."""
    parcels = (1 - alpha) * N_SIM
    if parcels <= 0:
        return 0
    return math.ceil(parcels / c_van(WEIGHT_REGIMES[weight_regime]))


def idle_per_tour_real(x_s: float) -> float:
    """Idle CO2 of one tour at x s per stop, at real scale [kg]."""
    return compute_co2_idle(x_s * N_STOPS, VAN_IDLE_FUEL_RATE_L_PER_S) * SCALE


def main() -> None:
    head = pd.read_csv(HEADLINE)
    i10 = idle_per_tour_real(HEADLINE_IDLE_S)
    print(f"preset: N_sim={N_SIM}, stops/tour={N_STOPS}, scale={SCALE:g}")
    print(f"i(10 s) per tour, from parameters = {i10:.6f} kg\n")

    for x, path in PIPELINE_SWEEPS.items():
        if not path.exists():
            print(f"[validate] {path.name} missing — skipping the cross-check for x={x:g}")
            continue
        prev = pd.read_csv(path)
        prev = prev[prev.alpha == 1.0]
        m = head.merge(prev, on=["alpha", "congestion", "weight_regime", "seed"],
                       suffixes=("_head", "_pipe"))
        if m.empty:
            print(f"[validate] no alpha=1 rows to check for x={x:g}")
            continue
        t_base = m.apply(lambda r: tours(0.0, r.weight_regime), axis=1)
        pred = m.term_b_kg_head + t_base * i10 * (x / HEADLINE_IDLE_S - 1)
        err = (pred - m.term_b_kg_pipe).abs().max()
        print(f"[validate] x={x:6.1f} s: {len(m)} full-load rows from the pipeline, "
              f"max error of the closed form = {err:.3e} kg")
        if err > 1e-6:
            raise SystemExit("the closed form does not reproduce the pipeline: stop here")

    for x, out_dir in TARGETS.items():
        df = head.copy()
        t_base = df.apply(lambda r: tours(0.0, r.weight_regime), axis=1)
        t_scen = df.apply(lambda r: tours(r.alpha, r.weight_regime), axis=1)
        delta = (t_base - t_scen) * i10 * (x / HEADLINE_IDLE_S - 1)
        if (df.term_b_kg + delta < 0).any():
            raise SystemExit("a cell would go negative: the max(0, ...) clamp would bite")
        df["term_b_kg"] = df.term_b_kg + delta
        # Only Term B moves: the other two terms do not see this knob.
        df["net_saving_kg_per_day"] = df.net_saving_kg_per_day + delta
        df["net_robust_kg_per_day"] = df.net_robust_kg_per_day + delta

        out_dir.mkdir(parents=True, exist_ok=True)
        df.to_csv(out_dir / "results_long.csv", index=False)
        # Same aggregation, and the same column names, as rotterdam_surface_robust:
        # the sweep files are read side by side with the surface.
        agg = dict(net_mean=("net_saving_kg_per_day", "mean"),
                   net_robust_mean=("net_robust_kg_per_day", "mean"),
                   term_b_mean=("term_b_kg", "mean"), term_c_mean=("term_c_kg", "mean"),
                   term_a_corridor_mean=("term_a_corridor_kg", "mean"),
                   n_seeds=("seed", "count"))
        for col, label in [("term_a_vans_kg", "term_a_vans_mean"),
                           ("term_a_busstop_kg", "term_a_busstop_mean"),
                           ("vanrow_delta_vehicle_hours", "vanrow_dvh_mean"),
                           ("busstop_delta_vehicle_hours", "busstop_dvh_mean"),
                           ("extra_dwell_s_per_trip", "extra_dwell_s_mean")]:
            if col in df.columns and df[col].notna().any():
                agg[label] = (col, "mean")
        mean = df.groupby(["alpha", "congestion", "weight_regime"], as_index=False).agg(**agg)
        mean.to_csv(out_dir / "results_mean.csv", index=False)
        full = df[df.alpha == 1.0]
        print(f"[write] x={x:6.1f} s -> {out_dir.name}: {len(df)} rows, "
              f"full load net {full.net_robust_kg_per_day.min():.1f} to "
              f"{full.net_robust_kg_per_day.max():.1f} kg/day")


if __name__ == "__main__":
    main()
