"""
Toy SCREENING, axis C — bus on-board capacity (N_max) x alpha (HANDOFF §7/§8).

Third sandbox screening, added 2026-06-29. The bus-capacity rebound is degenerate
on the toy at the realistic N_max (buses run ~empty, ~6 pax, so capacity never
binds, alpha_max = 1). To screen the N_max x alpha interaction we FORCE binding by
choosing a low N_max, declared as a forcing (mechanism check, not magnitude).

The rebound is DETERMINISTIC, so NO new runs are needed: with an on-board space cap
of N_max parcels per trip over F trips, at most alpha_max = min(1, N_max*F/N) of the
N parcels can ride the bus. Offered adoption alpha is capped to the effective
alpha_eff = min(alpha, alpha_max); the (1 - alpha_eff)*N parcels that do not fit
rebound to vans. A capped scenario at offered alpha is therefore IDENTICAL to the
existing no-cap toy run at adoption alpha_eff, so the screening is an exact remap of
the existing toy net(alpha) surface through the cap (medium weight, peak, matching
the Rotterdam bus-capacity slice).

(On Rotterdam the same axis DOES need runs, because the rebound vans add road traffic
and change S_cong; on the toy S_cong ~ 0, so the interaction is visible from Term B/C
alone, which the remap captures exactly.)

Run from project root:  python python_pipeline/screening_bus_capacity.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from parameters import BUS_TRIPS_PER_DAY, N_FREIGHT_UNITS_TOY, ALPHA_VALUES

RESULTS = Path("output/sensitivity_results_warm_perweight/results_mean.csv")
F = BUS_TRIPS_PER_DAY              # 60 toy bus trips/day
N = N_FREIGHT_UNITS_TOY           # 2000 toy parcels
# Forced-binding N_max values (low so the on-board space cap bites on the toy):
#   alpha_max = N_max*F/N -> 17->~0.5, 25->0.75, 70->2.1 (=1, no bind, reference).
NMAX_VALUES = [17, 25, 70]


def main() -> None:
    d = pd.read_csv(RESULTS)
    sub = d[(d.weight_regime == "medium") & (d.congestion == "peak")].sort_values("alpha")
    a_grid = sub["alpha"].to_numpy()
    net_grid = sub["net_robust_mean"].to_numpy()

    def net_at(alpha_eff: float) -> float:
        return float(np.interp(alpha_eff, a_grid, net_grid))

    print("[Axis C] bus on-board capacity N_max x alpha (medium, peak) — net_robust kg/day")
    print(f"  (forced binding on the toy; alpha_max = min(1, N_max*{F}/{N}))\n")
    rows = {}
    amax = {}
    for nmax in NMAX_VALUES:
        amax[nmax] = min(1.0, nmax * F / N)
        rows[nmax] = {a: net_at(min(a, amax[nmax])) for a in ALPHA_VALUES}
    df = pd.DataFrame(rows).T
    df.index.name = "N_max"
    df.columns = [f"a{a:.2f}" for a in ALPHA_VALUES]
    df.insert(0, "alpha_max", [amax[n] for n in NMAX_VALUES])
    print(df.to_string(float_format=lambda v: f"{v:+.2f}"))

    # Interaction read-out: N_max effect (tight 25 vs loose 70) per offered alpha.
    print("\n  N_max effect  net(N_max=70) - net(N_max=25)  per offered alpha:")
    for a in ALPHA_VALUES:
        diff = net_at(min(a, amax[70])) - net_at(min(a, amax[25]))
        tag = "no effect" if abs(diff) < 0.5 else "capacity bites"
        print(f"    alpha={a:.2f}: {diff:+.2f} kg/day ({tag})")
    print("  -> the cap bites only above alpha_max, so N_max matters only at high alpha: "
          "N_max x alpha INTERACTS. The Rotterdam bus-capacity slice must be CROSSED with "
          "alpha (cannot be held at a single alpha), confirming the planned design (HANDOFF "
          "§8). Sandbox screening with FORCED binding, not a proof.")

    out = Path("output/screening")
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "screening_axisC_busN_max_alpha.csv")
    print(f"\nWrote table -> {out}/screening_axisC_busN_max_alpha.csv")


if __name__ == "__main__":
    main()
