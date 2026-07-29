"""Verifica it.10 vs it.0: tempi di viaggio dei van e velocita' del corridoio."""
import io
import sys
from pathlib import Path

import zstandard as zstd

sys.path.insert(0, str(Path(__file__).parent))

from corridor_metrics import corridor_background_stats, load_corridor_links
from parse_events import load_link_attributes, parse_events
from scenario_presets import get_preset

ROOT = Path(__file__).parent.parent
RUN = ROOT / "output" / "ipft_rotterdam_convergence_check"

# 1) tempi di viaggio van da trips.csv
times = []
with open(RUN / "MRDH_10pct.output_trips.csv.zst", "rb") as f:
    r = zstd.ZstdDecompressor().stream_reader(f)
    t = io.TextIOWrapper(r, encoding="utf-8")
    header = t.readline().strip().split(";")
    i_person = header.index("person")
    i_trav = header.index("trav_time")
    for line in t:
        if line.startswith("backup_van_"):
            parts = line.split(";")
            h, m, s = parts[i_trav].split(":")
            times.append(int(h) * 3600 + int(m) * 60 + int(s))

times.sort()
n = len(times)
print(f"van trips: {n}")
if n:
    print(f"van travel time  min {times[0]/60:.0f} min | median {times[n//2]/60:.0f} min | "
          f"max {times[-1]/60:.0f} min   (it.0 era ~148 min)")

# 2) velocita' corridoio dal final events
p = get_preset("rotterdam")
events = RUN / "MRDH_10pct.output_events.xml.zst"
vmean, _ = parse_events(str(events), str(ROOT / p.network_file), verbose=False,
                        bus_prefixes=p.transit_prefixes, pax_bus_ids=p.term_c_bus_ids)
links = load_corridor_links(ROOT / p.corridor_links_file)
ll, _ = load_link_attributes(str(ROOT / p.network_file))
stats = corridor_background_stats(vmean, links, ll)
print(f"\ncorridor background @it.10: mean speed {stats['mean_speed_ms']:.2f} m/s "
      f"({stats['mean_speed_ms']*3.6:.1f} km/h)  (it.0 era 0.32 m/s)")
print(f"vehicle-hours {stats['vehicle_hours']:.0f} h | traversals {stats['n_traversals']:,}")
