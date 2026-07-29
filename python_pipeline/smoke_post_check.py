"""Valida il post-processing completo sullo smoke run #4 (proxy mode:
baseline = scenario, quindi i delta devono essere 0 — qui si verifica solo
che ogni pezzo nuovo produca numeri sensati senza errori)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from run_pipeline import run_scenario
from scenario_presets import get_preset

ROOT = Path(__file__).parent.parent
EVENTS = ROOT / "output" / "ipft_rotterdam_smoke" / "MRDH_10pct.output_events.xml.zst"

p = get_preset("rotterdam")
res = run_scenario(
    baseline_events_path=str(EVENTS),
    network_path=str(ROOT / p.network_file),
    scenario_events_path=None,          # proxy mode
    alpha=0.5,
    weight_regime="medium",
    n_freight_units=p.n_freight_units_sim,
    n_pickup_stops=p.n_pickup_stops,
    verbose=True,
    sample_rate=p.sample_rate,
    bus_trips_per_day=p.bus_trips_per_day,
    transit_prefixes=p.transit_prefixes,
    bus_id_allowlist=p.term_c_bus_ids,
    hb_route_prefixes=p.hb_route_prefixes,
    corridor_links_file=str(ROOT / p.corridor_links_file),
)
print("\n=== result keys of interest ===")
for k in ("alpha_max", "term_c_kg", "bus_id", "n_bus_links",
          "term_a_corridor_kg", "corridor_delta_vehicle_hours",
          "corridor_speed_change_ms"):
    print(f"  {k}: {res[k]}")
