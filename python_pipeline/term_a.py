"""
Term A — Congestion relief CO2 savings for background traffic.

Term A = Σ CO2_background(alpha=0, baseline run)
       − Σ CO2_background(alpha=x, scenario run)

Source: HBEFA emission events from MATSim (warmEmissionEvent / coldEmissionEvent).
These are written to events.xml when EmissionModule is active (Phase 3 — now enabled).
Only background vehicle emissions are retained; backup van and bus HBEFA emissions
are discarded to avoid double-counting with Terms B and C.

HBEFA emission event attributes (MATSim 2026):
  vehicleId, linkId, CO2_TOTAL [grams], time [s], plus NOx, PM, etc.
  Both warmEmissionEvent (per-link running) and coldEmissionEvent (cold-start penalty)
  contribute real CO2 and are both summed for Term A.

Vehicle type → HBEFA category mapping (emission_vehicles.xml + transit vehicles):
  car / taxiA          → pass. car   (kept; this is Term A)
  freight (van)        → LCV         (computed by HBEFA but discarded — Term B uses formula)
  busType (transit)    → urban bus   (computed by HBEFA but discarded — Term C uses formula)
Filtering by vehicleId prefix (BUS_ID_PREFIXES, VAN_ID_PREFIX) removes van/bus
emissions from the Term A sum. The discarded HBEFA values can still be reported
as a sanity-check cross-reference against the longitudinal-dynamics CO2 estimate.
"""

from __future__ import annotations

import warnings

import pandas as pd

from parameters import BUS_ID_PREFIXES, VAN_ID_PREFIX

# Module-level cache for HBEFA event parses (avoids re-reading large .zst files)
_HBEFA_CACHE: dict = {}


# ── HBEFA emission event parser ────────────────────────────────────────────

def parse_hbefa_events(events_zst_path: str) -> pd.DataFrame:
    """
    Stream-parse warmEmissionEvent and coldEmissionEvent from events.xml.zst.

    Returns DataFrame with columns:
      vehicle_id, link_id, time_s, co2_kg, event_type ('warm'/'cold')

    Raises RuntimeWarning if no emission events found (HBEFA not enabled).
    """
    cache_key = str(events_zst_path)
    if cache_key in _HBEFA_CACHE:
        return _HBEFA_CACHE[cache_key].copy()

    import xml.etree.ElementTree as ET

    from parse_events import open_events_stream

    records = []
    n_warm = 0
    n_cold = 0

    with open_events_stream(events_zst_path) as reader:
            for _, elem in ET.iterparse(reader, events=["end"]):
                if elem.tag != "event":
                    elem.clear()
                    continue
                etype = elem.get("type", "")
                if etype in ("warmEmissionEvent", "coldEmissionEvent"):
                    # Attribute names: vehicleId, linkId, CO2_TOTAL (grams)
                    co2_g = float(elem.get("CO2_TOTAL", 0) or 0)
                    records.append((
                        elem.get("vehicleId"),
                        elem.get("linkId"),
                        float(elem.get("time", 0)),
                        co2_g / 1000.0,  # g → kg
                        "warm" if etype == "warmEmissionEvent" else "cold",
                    ))
                    if etype == "warmEmissionEvent":
                        n_warm += 1
                    else:
                        n_cold += 1
                elem.clear()

    if not records:
        warnings.warn(
            "No HBEFA emission events found in events.xml. "
            "EmissionsModule is not enabled — Term A cannot be computed. "
            "Enable HBEFA in MatsimModelImplementation.java (Phase 3).",
            RuntimeWarning,
            stacklevel=2,
        )
        empty = pd.DataFrame(columns=["vehicle_id", "link_id", "time_s", "co2_kg", "event_type"])
        _HBEFA_CACHE[cache_key] = empty
        return empty.copy()

    print(f"[term_a] HBEFA events: {n_warm} warm + {n_cold} cold")
    df = pd.DataFrame(records, columns=["vehicle_id", "link_id", "time_s", "co2_kg", "event_type"])
    _HBEFA_CACHE[cache_key] = df
    return df.copy()


# ── co2_totals.csv (written by the Java Co2TotalsHandler) ─────────────────

def _find_co2_totals(events_path: str):
    """Locate <runId.>co2_totals.csv next to an events file.

    The events file sits either in the run dir root (output_events.xml.zst)
    or in ITERS/it.N/ — the CSV is always written to the run dir root.
    Returns the Path or None.
    """
    from pathlib import Path
    run_dir = Path(events_path).parent
    if run_dir.name.startswith("it."):
        run_dir = run_dir.parent.parent
    hits = sorted(run_dir.glob("*co2_totals.csv"))
    return hits[0] if hits else None


def read_background_co2_from_totals(
    events_path: str,
    vehicle_class: str = "background",
) -> float | None:
    """CO2 [kg] for one vehicle class from co2_totals.csv, or None.

    Classes written by Co2TotalsHandler.java: background / van / transit, and
    background_corridor (background CO2 restricted to the corridor link set,
    written only when corridor_links.txt was found at startup). The buckets
    mirror filter_background_vehicles, so 'background' IS the Term A quantity.
    """
    csv_path = _find_co2_totals(events_path)
    if csv_path is None:
        return None
    df = pd.read_csv(csv_path)
    row = df[df["vehicle_class"] == vehicle_class]
    if row.empty:
        return None
    return float(row["total_co2_kg"].iloc[0])


# ── Background vehicle filter ─────────────────────────────────────────────

def filter_background_vehicles(
    hbefa_df: pd.DataFrame,
    transit_prefixes: tuple[str, ...] | None = None,
) -> pd.DataFrame:
    """
    Keep only background vehicles (cars, non-freight trucks).
    Discard backup vans and ALL transit vehicles — vans are handled by Term B,
    the IPFT bus by Term C, and the remaining transit fleet must not pollute
    the background delta (its HBEFA values are fake pass-car numbers anyway).

    transit_prefixes: default parameters.BUS_ID_PREFIXES (toy).
    For Rotterdam pass ("veh_",) — every transit vehicle id starts with it.
    """
    if transit_prefixes is None:
        transit_prefixes = BUS_ID_PREFIXES
    vids = hbefa_df["vehicle_id"].fillna("")
    is_van = vids.str.startswith(VAN_ID_PREFIX, na=False)
    is_bus = vids.str.startswith(transit_prefixes, na=False)
    return hbefa_df[~is_van & ~is_bus].copy()


# ── Term A computation ────────────────────────────────────────────────────

def compute_term_a(
    baseline_events_path: str,
    scenario_events_path: str,
    transit_prefixes: tuple[str, ...] | None = None,
) -> dict:
    """
    Term A = ΣCO2_background(baseline) − ΣCO2_background(scenario) [kg CO2 per day].

    Parameters
    ----------
    baseline_events_path : path to events.xml.zst from the alpha=0 baseline run
    scenario_events_path : path to events.xml.zst from the alpha=x scenario run
    transit_prefixes     : vehicle-id prefixes excluded as transit (see
                           filter_background_vehicles)

    Returns
    -------
    {
      'term_a_kg': float,          positive = IPFT saves CO2 via congestion relief
      'co2_background_baseline': float,
      'co2_background_scenario': float,
      'hbefa_enabled': bool,
    }
    """
    # Preferred path: co2_totals.csv aggregated online by the Java handler
    # (the only option when isWritingEmissionsEvents=false — Rotterdam).
    bg_baseline = read_background_co2_from_totals(baseline_events_path)
    bg_scenario = read_background_co2_from_totals(scenario_events_path)

    if bg_baseline is None or bg_scenario is None:
        # Fallback: parse emission events from the events file (toy runs).
        baseline_hbefa = parse_hbefa_events(baseline_events_path)
        scenario_hbefa = parse_hbefa_events(scenario_events_path)

        if baseline_hbefa.empty or scenario_hbefa.empty:
            return dict(TERM_A_STUB)

        bg_baseline = filter_background_vehicles(baseline_hbefa, transit_prefixes)["co2_kg"].sum()
        bg_scenario = filter_background_vehicles(scenario_hbefa, transit_prefixes)["co2_kg"].sum()

    term_a = bg_baseline - bg_scenario

    # Corridor-local Term A (only available from the Java CSV, and only when
    # corridor_links.txt was present at run time). Far better signal-to-noise
    # than the region-wide delta: the treatment lives on ~1,400 links.
    def _bucket_delta(vehicle_class: str) -> float | None:
        b = read_background_co2_from_totals(baseline_events_path, vehicle_class)
        s = read_background_co2_from_totals(scenario_events_path, vehicle_class)
        return (b - s) if b is not None and s is not None else None

    term_a_corridor = _bucket_delta("background_corridor")

    # Dwell-in-MATSim split (2026-07-30): the van relief and the bus-stop
    # queueing live on two DISJOINT link sets and are REPORTED APART:
    #   term_a_vans_kg    = corridor minus stop links  (row 1, expected +)
    #   term_a_busstop_kg = stop links + 1-hop upstream (row 2, expected −)
    # Both are baseline − scenario; the bus cost comes out negative on its
    # own — do not flip signs by hand. Buckets exist only on runs whose
    # co2_totals.csv was written with bus_stop_links.txt present (None before).
    #
    # NOT COMPARABLE TO term_a_corridor_kg: row 1 + row 2 spans corridor UNION
    # bus-stop set (178 links), while term_a_corridor_kg is the corridor alone
    # (163). The 15 extra links are the upstream ring where the queue behind a
    # blocking bus forms — new road, deliberately added, not double counting.
    # So Term A(split) − term_a_corridor_kg is NOT the bus cost.
    term_a_vans = _bucket_delta("background_corridor_exstop")
    term_a_busstop = _bucket_delta("background_busstop")

    return {
        "term_a_kg": term_a,
        "co2_background_baseline": bg_baseline,
        "co2_background_scenario": bg_scenario,
        "term_a_corridor_kg": term_a_corridor,
        "term_a_vans_kg": term_a_vans,
        "term_a_busstop_kg": term_a_busstop,
        "hbefa_enabled": True,
    }


# ── Stub result (used by run_pipeline.py until HBEFA is enabled) ──────────

TERM_A_STUB = {
    "term_a_kg": None,
    "co2_background_baseline": None,
    "co2_background_scenario": None,
    "term_a_corridor_kg": None,
    "term_a_vans_kg": None,
    "term_a_busstop_kg": None,
    "hbefa_enabled": False,
}


if __name__ == "__main__":
    print("term_a.py: HBEFA module not yet enabled.")
    print("Activate Phase 3 (EmissionsModule in Java) before running this module.")
