"""
Per-alpha transit schedules with the freight dwell INSIDE MATSim.

For each alpha this script writes a copy of the Rotterdam transit schedule in
which the 8 delivery stops of every line-44 H->B route carry

    minimumStopDuration = EXTRA_DWELL_FIXED_OVERHEAD_S
                        + EXTRA_DWELL_PER_UNIT_S * units_per_stop

with units_per_stop = alpha * N_real / F / n_pickup_stops — the SAME seconds
Python charges as idle CO2 (dynamic_mass.compute_extra_dwell_time), so the
congestion side and the emission side tell one story. At alpha = 0 no attribute
is written: the baseline bus behaves exactly as today.

Semantics (verified in the MATSim 2026.0 sources):
  - minimumStopDuration is a native <stop> attribute of the v2 schedule
    (Constants.java:57, read by TransitScheduleReaderV2:184). While it runs,
    passenger boarding does not advance, so boarding time is added ON TOP:
    this is the SERIES convention (dwell_pax + dwell_pkg), the conservative
    choice. On line 44 the difference vs parallel is < 5% (~1 pax/trip).
  - awaitDeparture stays true everywhere: the timetable is consulted only
    AFTER the stop duration is satisfied (AbstractTransitDriverAgent), so the
    schedule can delay a departure but never cut the unloading short.
  - isBlocking is a stopFacility attribute: true  = the stopped bus holds the
    traffic lane (realistic case, headline), false = it pulls into a bay
    (sensitivity: "would a dedicated PT unloading bay help?"). Set here on the
    9 H->B facilities of line 44 only — the rest of the city is untouched.

The dwell depends only on alpha (units are a COUNT: N and F are fixed, weight
does not enter), so 5 alphas x 2 blocking variants = 10 schedule files cover
the whole 30-cell surface.

Usage (from project root):
    python python_pipeline/make_dwell_schedules.py                # blocking (headline)
    python python_pipeline/make_dwell_schedules.py --blocking false   # bay sensitivity

Each written file is re-read and structurally verified against the base
schedule (counts of lines/routes/stops/facilities/departures, attribute
placement, dwell values) before the script reports success.
"""

from __future__ import annotations

import argparse
import gzip
import io
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dynamic_mass import compute_extra_dwell_time
from parameters import (ALPHA_VALUES, EXTRA_DWELL_FIXED_OVERHEAD_S,
                        EXTRA_DWELL_PER_UNIT_S)
from scenario_presets import get_preset

SCHEDULE_DOCTYPE = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<!DOCTYPE transitSchedule SYSTEM '
    '"http://www.matsim.org/files/dtd/transitSchedule_v2.dtd">\n'
)

# Fallbacks for line 44, kept only so an OLD preset without the fields still
# works. Since 2026-08-21 every preset carries transit_line_id / hub_stop_link /
# base_transit_schedule, and the preset is the authority — do not add a third
# line here, describe it in scenario_presets.


def _fmt_hhmmss(seconds: int) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _parse_hhmmss(value: str) -> int:
    h, m, s = value.split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def dwell_seconds(alpha: float, preset, per_unit_s: float = EXTRA_DWELL_PER_UNIT_S) -> int:
    """Package dwell per delivery stop [integer s] — same formula as Term C.

    The XML wants whole seconds (HH:MM:SS), so the float is rounded here
    (alpha=1: 39.97 -> 40 s, a 0.08% rounding documented in the thesis).
    """
    units_per_stop = (alpha * preset.n_freight_units_real
                      / preset.bus_trips_per_day / preset.n_pickup_stops)
    return int(round(compute_extra_dwell_time(units_per_stop, per_unit_s)))


# ── Schedule surgery ───────────────────────────────────────────────────────

def _load_schedule(path: Path) -> ET.ElementTree:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as f:
        return ET.parse(f)


def _find_line(root, line_id_fragment: str) -> ET.Element:
    for line in root.iter("transitLine"):
        if line_id_fragment in (line.get("id") or ""):
            return line
    raise RuntimeError(f"transitLine containing '{line_id_fragment}' not found")


# Historical name, kept so existing imports do not break.
_find_line44 = _find_line


def _facility_links(root) -> dict[str, str]:
    return {f.get("id"): f.get("linkRefId") for f in root.iter("stopFacility")}


def _hb_routes(line44: ET.Element, fac_link: dict[str, str],
               hb_vehicle_ids: frozenset[str],
               hub_link: str) -> list[ET.Element]:
    """The H->B routes: first stop on the hub platform link. Cross-checked
    against the 98 known H->B vehicle ids — any mismatch aborts."""
    routes, hb_dep_total = [], 0
    for route in line44.iter("transitRoute"):
        stops = route.find("routeProfile").findall("stop")
        first_link = fac_link.get(stops[0].get("refId"))
        deps = [d.get("vehicleRefId")
                for d in route.find("departures").findall("departure")]
        if first_link == hub_link:
            unknown = [v for v in deps if v not in hb_vehicle_ids]
            if unknown:
                raise RuntimeError(
                    f"route {route.get('id')}: {len(unknown)} departures not in "
                    f"the one-to-many vehicle id list — direction identification is wrong")
            routes.append(route)
            hb_dep_total += len(deps)
        else:
            overlap = [v for v in deps if v in hb_vehicle_ids]
            if overlap:
                raise RuntimeError(
                    f"route {route.get('id')} does not start at the hub but has "
                    f"{len(overlap)} H->B vehicles — identification is wrong")
    if hb_dep_total != len(hb_vehicle_ids):
        raise RuntimeError(f"H->B departures found: {hb_dep_total}, "
                           f"expected {len(hb_vehicle_ids)}")
    return routes


def build_schedule(base: Path, alpha: float, blocking: bool, out_path: Path,
                   preset, hb_vehicle_ids: frozenset[str],
                   per_unit_s: float = EXTRA_DWELL_PER_UNIT_S) -> dict:
    """Write one modified schedule; return a report dict for verification."""
    tree = _load_schedule(base)
    root = tree.getroot()
    fac_link = _facility_links(root)
    line44 = _find_line(root, preset.transit_line_id)
    routes = _hb_routes(line44, fac_link, hb_vehicle_ids,
                        preset.hub_stop_link)

    dwell_s = dwell_seconds(alpha, preset, per_unit_s)
    n_stamped = 0
    hb_facilities: set[str] = set()
    # The dwell is stamped on the DELIVERY stops, identified by their link, not on
    # "every stop except the first". The two coincide on line 44, where all 8 stops
    # after the hub are delivery stops. They do not on line 87, whose 63 departures
    # split into two branches after the 17th stop: a locker can only sit where every
    # bus stops, so the delivery segment is the common prefix and the branch is
    # outside it. Keying on pickup_link_ids makes that explicit and works for both.
    delivery_links = frozenset(preset.pickup_link_ids or ())
    hub_link = preset.hub_stop_link
    for route in routes:
        stops = route.find("routeProfile").findall("stop")
        if delivery_links:
            targets = [s for s in stops
                       if fac_link.get(s.get("refId")) in delivery_links]
            if len(targets) != preset.n_pickup_stops:
                raise RuntimeError(
                    f"route {route.get('id')}: {len(targets)} of its {len(stops)} "
                    f"stops are on a delivery link, expected {preset.n_pickup_stops}")
        else:
            if len(stops) - 1 != preset.n_pickup_stops:
                raise RuntimeError(f"route {route.get('id')}: {len(stops)} stops, "
                                   f"expected {preset.n_pickup_stops + 1}")
            targets = stops[1:]
        # isBlocking goes on the delivery segment only: the hub plus the stops that
        # actually receive freight. Stops beyond it get no extra dwell, so flagging
        # them would change baseline and scenario identically and measure nothing.
        hb_facilities.update(s.get("refId") for s in targets)
        hb_facilities.update(s.get("refId") for s in stops
                             if fac_link.get(s.get("refId")) == hub_link)
        for stop in targets:
            if dwell_s > 0:
                stop.set("minimumStopDuration", _fmt_hhmmss(dwell_s))
                n_stamped += 1

    # isBlocking on the 9 H->B facilities only (2 of them are platforms on
    # bus-only links, where blocking has no car traffic to block — harmless).
    n_blocking = 0
    for fac in root.iter("stopFacility"):
        if fac.get("id") in hb_facilities:
            fac.set("isBlocking", "true" if blocking else "false")
            n_blocking += 1
    if n_blocking != len(hb_facilities):
        raise RuntimeError(f"stamped isBlocking on {n_blocking} facilities, "
                           f"expected {len(hb_facilities)}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    tree.write(buf, encoding="utf-8", xml_declaration=False)
    with gzip.open(out_path, "wb") as f:
        f.write(SCHEDULE_DOCTYPE.encode("utf-8"))
        f.write(buf.getvalue())
        f.write(b"\n")

    return {"alpha": alpha, "blocking": blocking, "dwell_s": dwell_s,
            "n_stamped": n_stamped, "n_routes": len(routes),
            "n_facilities_flagged": n_blocking, "path": out_path}


# ── Post-write verification ────────────────────────────────────────────────

def _structure_counts(tree: ET.ElementTree) -> dict:
    root = tree.getroot()
    return {
        "transitLines": sum(1 for _ in root.iter("transitLine")),
        "transitRoutes": sum(1 for _ in root.iter("transitRoute")),
        "stopFacilities": sum(1 for _ in root.iter("stopFacility")),
        "routeStops": sum(1 for r in root.iter("routeProfile")
                          for _ in r.findall("stop")),
        "departures": sum(1 for _ in root.iter("departure")),
        "awaitDeparture_true": sum(
            1 for r in root.iter("routeProfile") for s in r.findall("stop")
            if s.get("awaitDeparture") == "true"),
    }


def verify_schedule(base_counts: dict, report: dict, preset) -> None:
    """Re-read the written file from disk and check it is exactly the base
    schedule plus the intended edits — nothing more, nothing less."""
    tree = _load_schedule(report["path"])
    counts = _structure_counts(tree)
    for key, expected in base_counts.items():
        if counts[key] != expected:
            raise RuntimeError(f"{report['path'].name}: {key} = {counts[key]}, "
                               f"base had {expected} — structure was altered")

    root = tree.getroot()
    stamped = [(s.get("minimumStopDuration"))
               for r in root.iter("routeProfile") for s in r.findall("stop")
               if s.get("minimumStopDuration") is not None]
    expected_stamped = (report["n_routes"] * preset.n_pickup_stops
                        if report["dwell_s"] > 0 else 0)
    if len(stamped) != expected_stamped:
        raise RuntimeError(f"{report['path'].name}: {len(stamped)} "
                           f"minimumStopDuration attrs, expected {expected_stamped}")
    for val in stamped:
        if _parse_hhmmss(val) != report["dwell_s"]:
            raise RuntimeError(f"{report['path'].name}: dwell {val} != "
                               f"{report['dwell_s']} s")

    n_true = sum(1 for f in root.iter("stopFacility")
                 if f.get("isBlocking") == "true")
    expected_true = report["n_facilities_flagged"] if report["blocking"] else 0
    if n_true != expected_true:
        raise RuntimeError(f"{report['path'].name}: {n_true} facilities with "
                           f"isBlocking=true, expected {expected_true}")


# ── CLI ────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--blocking", choices=["true", "false"], default="true",
                    help="isBlocking on the 9 H->B stop facilities "
                         "(true = in-lane, headline; false = bay sensitivity)")
    ap.add_argument("--alphas", type=float, nargs="*", default=list(ALPHA_VALUES))
    ap.add_argument("--schedule", default=None,
                    help="Base schedule (default: the one in the Rotterdam config)")
    ap.add_argument("--out-dir", default=None,
                    help="Default: scenarios/ipft_rotterdam/dwell_schedules<suffix>")
    ap.add_argument("--scenario", default="rotterdam",
                    help="preset name: rotterdam (line 44) or rotterdam_L87")
    ap.add_argument("--vehicle-ids", default=None,
                    help="Default: the preset's one-to-many vehicle id file")
    ap.add_argument("--per-unit-s", type=float, default=EXTRA_DWELL_PER_UNIT_S,
                    help="Handling seconds per parcel (default: "
                         "parameters.EXTRA_DWELL_PER_UNIT_S). This is the whole "
                         "handling-time sensitivity: once the dwell is in the "
                         "schedule the seconds are an INPUT, so a different value "
                         "means new schedules and new runs. Write them somewhere "
                         "else with --out-dir, or the existing ones are overwritten.")
    args = ap.parse_args()

    preset = get_preset(args.scenario)
    scen_dir = Path(preset.base_config).parent
    base = Path(args.schedule) if args.schedule else Path(preset.base_transit_schedule)
    out_dir = (Path(args.out_dir) if args.out_dir
               else scen_dir / f"dwell_schedules{preset.suffix}")
    blocking = args.blocking == "true"
    tag = "blocking" if blocking else "bay"

    if args.vehicle_ids:
        ids_path = Path(args.vehicle_ids)
    elif preset.name == "rotterdam":
        ids_path = scen_dir / "line44_hb_vehicle_ids.txt"   # historical name
    else:
        ids_path = scen_dir / f"line{preset.transit_line_id}_{preset.hub_stop_link}_vehicle_ids.txt"
    hb_ids = frozenset(ids_path.read_text().split())
    print(f"scenario      : {preset.name}  (line {preset.transit_line_id}, "
          f"hub {preset.hub_stop_link}, F={preset.bus_trips_per_day})")
    print(f"vehicle ids   : {ids_path.name} ({len(hb_ids)})")
    print(f"base schedule : {base}")
    print(f"variant       : isBlocking={args.blocking} ({tag})")
    print(f"handling time : {EXTRA_DWELL_FIXED_OVERHEAD_S:.0f} s/stop + "
          f"{args.per_unit_s:.0f} s/parcel")
    base_counts = _structure_counts(_load_schedule(base))
    print(f"base structure: {base_counts}")

    for alpha in args.alphas:
        astr = f"{int(round(alpha * 100)):03d}"
        out = out_dir / f"ptSchedule_dwell_alpha{astr}_{tag}.xml.gz"
        report = build_schedule(base, alpha, blocking, out, preset, hb_ids,
                                args.per_unit_s)
        verify_schedule(base_counts, report, preset)
        print(f"  alpha={astr}  dwell={report['dwell_s']:2d} s/stop  "
              f"stamped {report['n_stamped']:2d} stops on {report['n_routes']} routes, "
              f"isBlocking->{args.blocking} on {report['n_facilities_flagged']} "
              f"facilities  -> {out.name}  [verified]")

    print("\nAll schedules written and verified. Point the warm configs at them with\n"
          "  make_rotterdam_warm_scenarios.py --dwell-schedules-dir "
          f"{out_dir}  [--dwell-tag {tag}]")


if __name__ == "__main__":
    main()
