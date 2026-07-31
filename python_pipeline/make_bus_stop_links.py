"""
Build the bus-stop congestion link set for the dwell-in-MATSim measurement.

With isBlocking=true the stopped bus holds up the cars BEHIND it, so the bus
cost of IPFT lives on the stop links and their immediately UPSTREAM links —
not on the van corridor. The two effects are therefore measured on two
disjoint link sets and reported separately (Term A rows):

  row 1  vans taken off the road   corridor_links.txt MINUS the stop links   + saving
  row 2  buses held at stops       THIS file                                 − cost

This script writes row 2:  bus_stop_links.txt =
    (car-mode H->B stop links of line 44)                       [7 of 9: the
     hub and terminus platforms are bus-only, no cars to block]
  ∪ (car-mode links whose TO-node feeds a stop link, i.e. 1-hop upstream,
     MINUS the van corridor)                                    [queue reach]

The 3 stop links that also belong to the van corridor stay in row 2 (the stop
belongs to the bus side); the Java Co2TotalsHandler and run_pipeline derive
row 1 as corridor − row 2, so the rows are disjoint by construction.

--hops 2 additionally writes bus_stop_links_2hop.txt (adds the second
upstream ring) for the reach check: if the 2-hop ring is already seed noise,
1 hop is proven sufficient rather than assumed.

Usage (from project root):
    python python_pipeline/make_bus_stop_links.py
    python python_pipeline/make_bus_stop_links.py --hops 2
"""

from __future__ import annotations

import argparse
import gzip
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from make_dwell_schedules import (HUB_LINK, _facility_links, _find_line44,
                                  _hb_routes, _load_schedule)
from scenario_presets import get_preset


def load_network_topology(network_path: Path) -> dict[str, tuple[str, str, str]]:
    """{link_id: (from_node, to_node, modes)} — streaming, the network is 657k links."""
    topo: dict[str, tuple[str, str, str]] = {}
    opener = gzip.open if network_path.suffix == ".gz" else open
    with opener(network_path, "rb") as f:
        for _, elem in ET.iterparse(f, events=["end"]):
            if elem.tag == "link":
                topo[elem.get("id")] = (elem.get("from"), elem.get("to"),
                                        elem.get("modes") or "")
            elem.clear()
    return topo


def upstream_of(targets: set[str], topo: dict, car_only: bool = True) -> set[str]:
    """Car-mode links whose TO-node is the FROM-node of a target link —
    the road a queue behind the target physically backs up into."""
    from_nodes = {topo[l][0] for l in targets if l in topo}
    ups = set()
    for lid, (fr, to, modes) in topo.items():
        if to in from_nodes and lid not in targets:
            if car_only and "car" not in modes:
                continue
            ups.add(lid)
    return ups


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--hops", type=int, choices=[1, 2], default=1)
    ap.add_argument("--out", default=None,
                    help="Default: scenarios/ipft_rotterdam/bus_stop_links.txt")
    args = ap.parse_args()

    preset = get_preset("rotterdam")
    scen_dir = Path(preset.base_config).parent
    root_dir = scen_dir.parent.parent

    # ── The 9 H->B stop links, from the schedule (same code path as the
    #    dwell-schedule generator, so the two can never disagree) ───────────
    tree = _load_schedule(scen_dir / "ptSchedule36Hour.xml.gz")
    sroot = tree.getroot()
    fac_link = _facility_links(sroot)
    hb_ids = frozenset((scen_dir / "line44_hb_vehicle_ids.txt").read_text().split())
    routes = _hb_routes(_find_line44(sroot), fac_link, hb_ids)
    stop_links: list[str] = []
    for route in routes:
        for s in route.find("routeProfile").findall("stop"):
            lid = fac_link[s.get("refId")]
            if lid not in stop_links:
                stop_links.append(lid)
    assert len(stop_links) == 9, f"expected 9 H->B stop links, got {len(stop_links)}"
    assert stop_links[0] == HUB_LINK

    topo = load_network_topology(root_dir / preset.network_file)
    corridor = set((root_dir / preset.corridor_links_file)
                   .read_text().split())

    car_stops = [l for l in stop_links if "car" in topo[l][2]]
    platform_stops = [l for l in stop_links if "car" not in topo[l][2]]
    print(f"H->B stop links      : {len(stop_links)}  "
          f"(car-mode: {len(car_stops)}, bus-only platforms: {len(platform_stops)} "
          f"{platform_stops})")

    stops = set(car_stops)
    ring1 = upstream_of(stops, topo) - corridor
    busstop_set = stops | ring1
    print(f"1-hop upstream (car) : {len(upstream_of(stops, topo))}  "
          f"of which outside the van corridor: {len(ring1)}")
    print(f"stop links inside van corridor (stay in row 2): "
          f"{sorted(stops & corridor)}")
    print(f"row 2 set            : {len(busstop_set)} links")
    print(f"row 1 = corridor minus row 2 : {len(corridor - busstop_set)} links "
          f"(corridor {len(corridor)})")

    out = Path(args.out) if args.out else scen_dir / "bus_stop_links.txt"
    out.write_text("\n".join(sorted(busstop_set)) + "\n", encoding="utf-8")
    print(f"wrote {out}")

    if args.hops == 2:
        ring2 = upstream_of(busstop_set | (stops & corridor), topo) - corridor - busstop_set
        two_hop = busstop_set | ring2
        out2 = out.with_name("bus_stop_links_2hop.txt")
        out2.write_text("\n".join(sorted(two_hop)) + "\n", encoding="utf-8")
        print(f"2-hop ring adds {len(ring2)} links -> {out2}")

    # ── Verification: re-read and check disjointness of the two rows ──────
    written = set(out.read_text().split())
    assert written == busstop_set
    assert not (corridor - written) & written, "rows are not disjoint"
    assert stops <= written, "a car-mode stop link is missing from row 2"
    print("[verified] row sets disjoint, all car-mode stop links present")


if __name__ == "__main__":
    main()
