"""Regression check: una cella toy ricalcolata vs results_long.csv storico.

Atteso dopo i fix 2026-06-10:
  - term_a_kg, term_b_kg IDENTICI (filtri e scaling invariati per il toy)
  - term_c_kg leggermente DIVERSO (fix: bus rappresentativo mediano invece
    della prima corsa del mattino)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd

from run_pipeline import run_scenario
from scenario_presets import get_preset

ROOT = Path(__file__).parent.parent
preset = get_preset("toy")
RUNS = Path(preset.output_base_dir)          # D:\TesiOutputs\ipft_toy_runs
NET = ROOT / "scenarios" / "ipft_toy" / "reduced_network.xml"
HIST = ROOT / "output" / "sensitivity_results" / "results_long.csv"
alpha, congestion, seed, regime = 0.50, "peak", 4711, "medium"

baseline = RUNS / f"alpha000_{congestion}_seed{seed}" / "output_events.xml.zst"
scenario = RUNS / f"alpha050_{congestion}_seed{seed}" / "output_events.xml.zst"

res = run_scenario(
    baseline_events_path=str(baseline),
    network_path=str(NET),
    scenario_events_path=str(scenario),
    alpha=alpha,
    weight_regime=regime,
    n_freight_units=preset.n_freight_units_sim,
    n_pickup_stops=preset.n_pickup_stops,
    verbose=False,
    sample_rate=preset.sample_rate,
    bus_trips_per_day=preset.bus_trips_per_day,
    transit_prefixes=preset.transit_prefixes,
    bus_id_allowlist=preset.term_c_bus_ids,
    hb_route_prefixes=preset.hb_route_prefixes,
)

hist = pd.read_csv(HIST)
row = hist[(hist.alpha == alpha) & (hist.congestion == congestion)
           & (hist.seed == seed) & (hist.weight_regime == regime)].iloc[0]

print(f"{'':14s}{'storico':>14s}{'ricalcolato':>14s}")
for k in ("term_a_kg", "term_b_kg", "term_c_kg", "net_saving_kg_per_day"):
    print(f"{k:14s}{row[k]:14.4f}{res[k]:14.4f}")
print("\nbus usato ora:", res["bus_id"])

da = abs(row["term_a_kg"] - res["term_a_kg"])
db = abs(row["term_b_kg"] - res["term_b_kg"])
print(f"\n|delta A| = {da:.6f}  |delta B| = {db:.6f}")
print("REGRESSION OK" if da < 1e-6 and db < 1e-6 else "REGRESSION MISMATCH (A/B should be identical!)")
