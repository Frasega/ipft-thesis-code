"""Regression check: una cella toy ricalcolata vs results_long.csv storico.

term_a resta l'ancora dura: il toy legge gli eventi HBEFA, che nessuna delle
revisioni della pipeline ha toccato, quindi deve tornare IDENTICO al centesimo
di grammo. Se si muove, qualcosa che non doveva ha toccato il filtro dei
veicoli o lo scaling.

term_b e term_c NON coincidono più col CSV storico, che è di giugno, e non
devono: entrambi hanno cambiato metodo da allora, e i valori attesi sono quelli
qui sotto in REFERENCE, con la data e il motivo.

  term_b  22.3417 -> 21.3225   cinematica van rifatta (WLTC micro-trip + idle
                               al posto dello speed-change), 2026-07
  term_c   7.7028 ->  7.6666   fuori banda della libreria SORT: sotto SORT1 il
                               profilo del bus è ora scalato in ampiezza invece
                               che troncato a 11,82 km/h, 2026-08-12

Aggiornare REFERENCE è legittimo SOLO insieme a una modifica di metodo
dichiarata: se cambia senza che nessuno abbia toccato il metodo, è una
regressione, non un valore da riallineare.
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

# Valori attesi oggi. term_a viene dallo storico (deve essere identico), gli
# altri due dal metodo corrente — vedi il docstring per il perché di ciascuno.
REFERENCE = {"term_b_kg": 21.3225, "term_c_kg": 7.6666}
TOL = 1e-3

da = abs(row["term_a_kg"] - res["term_a_kg"])
print(f"\n|delta A| vs storico = {da:.6f}   (deve essere 0)")
fails = ["term_a_kg"] if da >= 1e-6 else []
for k, expected in REFERENCE.items():
    d = abs(expected - res[k])
    print(f"|delta {k}| vs riferimento = {d:.6f}   (atteso {expected:.4f})")
    if d >= TOL:
        fails.append(k)

print("\nREGRESSION OK" if not fails
      else f"\nREGRESSION MISMATCH su {', '.join(fails)} — "
           f"se nessuno ha cambiato metodo, è una regressione")
