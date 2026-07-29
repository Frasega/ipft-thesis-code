"""Velocita' del corridoio all'EQUILIBRIO (it.80, run LONGBASE senza van)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from corridor_metrics import corridor_background_stats, load_corridor_links
from parse_events import load_link_attributes, parse_events
from scenario_presets import get_preset

ROOT = Path(__file__).parent.parent
RUN = Path(r"D:\TesiOutputs\ipft_rotterdam_longbase")

p = get_preset("rotterdam")
events = RUN / "MRDH_10pct.output_events.xml.zst"
vmean, pax = parse_events(str(events), str(ROOT / p.network_file), verbose=False,
                          bus_prefixes=p.transit_prefixes, pax_bus_ids=p.term_c_bus_ids)
links = load_corridor_links(ROOT / p.corridor_links_file)
ll, _ = load_link_attributes(str(ROOT / p.network_file))
stats = corridor_background_stats(vmean, links, ll)
print(f"corridor background @equilibrio (it.80, no van):")
print(f"  mean speed {stats['mean_speed_ms']:.2f} m/s ({stats['mean_speed_ms']*3.6:.1f} km/h)")
print(f"  vehicle-hours {stats['vehicle_hours']:.0f} | traversals {stats['n_traversals']:,}")

# bonus: residuo merci della linea 44 all'equilibrio (per alpha_max definitivo)
from feasibility import compute_alpha_max
r = compute_alpha_max(pax, weight_per_unit_kg=10.0, n_freight_units_real=4700,
                      sample_rate=0.10, expected_vehicle_ids=p.term_c_bus_ids)
print(f"\nalpha_max medium @equilibrio: {r['alpha_max']:.3f} "
      f"(residuo medio {r['mean_trip_residual_kg']:.0f} kg/corsa, "
      f"min {r['min_trip_residual_kg']:.0f} kg)")
