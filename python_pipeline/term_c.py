"""
Term C — Extra CO2 cost from loading freight onto the bus.

Term C = F × Σ_links [ CO2(bus, M_bus_with_freight(link)) − CO2(bus, M_bus_pax_only(link)) ]
       + F × CO2_idle_extra

where:
  F                    = bus trips per day on the H→B route (RET schedule)
  M_bus_with_freight   = tare + passengers×75 + freight_remaining_on_link
  M_bus_pax_only       = tare + passengers×75
  CO2_idle_extra       = idle fuel burned during extra freight unloading dwell

The bus speed profile is reconstructed per-link from the SORT cycle library
(Lin & Niemeier recombination). The delta ΔCO2 per link captures the weight
effect: heavier bus needs more engine power → more fuel → more CO2.

The passenger dwell time (~3 min/stop, already in MATSim v_mean) cancels in the
delta — only the EXTRA freight unloading dwell is added via CO2_idle_extra.

Dynamic mass: freight_remaining decreases at each pickup point as the bus
delivers packages along the route. Using a constant mass would overestimate
Term C on later route segments.
"""

from __future__ import annotations

import pandas as pd

from dynamic_mass import (
    build_freight_remaining_uniform,
    compute_extra_dwell_time,
    compute_mbus_on_link,
    compute_mbus_passengers_only,
)
from emission_formula import (
    compute_co2_idle,
    compute_co2_running,
    speed_to_accel,
)
from parameters import (
    BUS_CD,
    BUS_DRIVETRAIN_EFF,
    BUS_FRONTAL_AREA_M2,
    BUS_IDLE_FUEL_RATE_L_PER_S,
    BUS_ROLLING_RESISTANCE,
    BUS_TARE_KG,
    BUS_TRIPS_PER_DAY,
    EXTRA_DWELL_PER_UNIT_S,
    BUS_ID_PREFIXES,
)
from sort_cycles import reconstruct_bus_profile


# ── Per-link CO2 delta (with vs. without freight) ─────────────────────────

def _delta_co2_on_link(
    v_mean_ms: float,
    t_enter: float,
    t_leave: float,
    link_id: str,
    bus_id: str,
    freight_remaining: dict[str, float],
    pax_timeline: dict,
    bus_tare_kg: float,
    rng_seed: int,
) -> float:
    """
    CO2(bus with freight) − CO2(bus without freight) on one link [kg].

    Reconstructs the 1 Hz speed profile via SORT recombination, then applies
    the longitudinal dynamics formula with M_with and M_without.
    """
    # Reconstruct 1 Hz speed profile for this link
    v_t = reconstruct_bus_profile(v_mean_ms, rng_seed=rng_seed)
    a_t = speed_to_accel(v_t)

    # Mass profiles
    m_with = compute_mbus_on_link(
        link_id, freight_remaining, pax_timeline, bus_id, t_enter, t_leave, bus_tare_kg
    )
    m_without = compute_mbus_passengers_only(bus_id, t_enter, t_leave, pax_timeline, bus_tare_kg)

    freight_on_link = m_with - m_without
    if freight_on_link <= 0:
        return 0.0

    co2_with = compute_co2_running(
        v_t, a_t, m_with, BUS_CD, BUS_FRONTAL_AREA_M2, BUS_ROLLING_RESISTANCE, BUS_DRIVETRAIN_EFF
    )
    co2_without = compute_co2_running(
        v_t, a_t, m_without, BUS_CD, BUS_FRONTAL_AREA_M2, BUS_ROLLING_RESISTANCE, BUS_DRIVETRAIN_EFF
    )
    # Scale delta to the actual link traversal duration.
    # The SORT profile duration (T_sort) may differ from actual link traversal time (T_actual).
    # The CO2 delta is proportional to time, so we scale: delta_link = delta_sort × T_actual/T_sort
    t_sort = float(len(v_t))  # 1 Hz → duration in seconds
    t_actual = t_leave - t_enter if (t_leave - t_enter) > 0 else t_sort
    scale = t_actual / t_sort if t_sort > 0 else 1.0
    return (co2_with - co2_without) * scale


# ── Term C for one bus vehicle ─────────────────────────────────────────────

def compute_term_c_for_bus(
    vmean_df: pd.DataFrame,
    bus_id: str,
    pax_timeline: dict,
    total_freight_kg_per_day: float,
    n_pickup_stops: int,
    n_freight_units_per_day: float,
    bus_tare_kg: float = BUS_TARE_KG,
    bus_trips_per_day: int = BUS_TRIPS_PER_DAY,
    rng_seed: int = 42,
    extra_dwell_per_unit_s: float = EXTRA_DWELL_PER_UNIT_S,
) -> dict:
    """
    Compute Term C [kg CO2 per day] using ONE representative bus trip × F.

    Interpretation (matches thesis_overview.md §4 / §9):
      - total_freight_kg_per_day  = alpha × N_freight × weight     (TOTAL daily freight)
      - freight per single trip   = total / F                       (uniform across the day)
      - delta per trip            = CO2(M_pax+freight_per_trip) − CO2(M_pax)
      - Term C per day            = F × delta_per_trip
      - Because the formula is linear in mass for small ΔM, this is
        algebraically equivalent to delta(total_freight) on a single trip,
        but using freight_per_trip keeps M_bus(link) consistent with the
        actual operational load per vehicle.

    Parameters
    ----------
    vmean_df                : full v_mean DataFrame from parse_events
    bus_id                  : bus vehicle ID (must start with a BUS_ID_PREFIXES entry)
    pax_timeline            : {bus_id: [(time, count), ...]} from parse_events
    total_freight_kg_per_day: total freight (kg) at the hub for the whole day
    n_pickup_stops          : number of stops where freight is delivered (equally spaced)
    n_freight_units_per_day : number of individual freight units delivered in the day
    bus_tare_kg             : bus empty mass [kg]
    bus_trips_per_day       : F — daily bus frequency on the H→B route
    rng_seed                : seed for SORT recombination (fixed per scenario for reproducibility)

    Returns
    -------
    {
      'term_c_kg_per_day': float,
      'co2_running_delta_kg_per_trip': float,
      'co2_idle_extra_kg_per_trip': float,
      'freight_per_trip_kg': float,
      'units_per_trip': float,
      'n_links_processed': int,
      'bus_id': str,
      'total_freight_kg_per_day': float,
    }
    """
    bus_links = vmean_df[vmean_df["vehicle_id"] == bus_id].copy()
    if bus_links.empty:
        return {
            "term_c_kg_per_day": 0.0,
            "co2_running_delta_kg_per_trip": 0.0,
            "co2_idle_extra_kg_per_trip": 0.0,
            "freight_per_trip_kg": 0.0,
            "units_per_trip": 0.0,
            "n_links_processed": 0,
            "bus_id": bus_id,
            "total_freight_kg_per_day": total_freight_kg_per_day,
        }

    bus_links = bus_links.sort_values("time_entered_s")
    link_sequence = bus_links["link_id"].tolist()

    # Distribute the daily freight uniformly across the F trips on the route.
    F = max(1, int(bus_trips_per_day))
    freight_per_trip_kg = total_freight_kg_per_day / F
    units_per_trip = n_freight_units_per_day / F

    # Build freight-remaining profile along the route for ONE trip
    freight_remaining = build_freight_remaining_uniform(
        freight_per_trip_kg, link_sequence, n_pickup_stops
    )

    has_tt = "travel_time_s" in bus_links.columns

    # Compute per-link CO2 delta (running) for ONE trip
    running_delta = 0.0
    for _, row in bus_links.iterrows():
        tt = float(row["travel_time_s"]) if has_tt else 20.0
        t_leave_approx = row["time_entered_s"] + tt

        delta = _delta_co2_on_link(
            v_mean_ms=row.v_mean_ms,
            t_enter=row.time_entered_s,
            t_leave=t_leave_approx,
            link_id=row.link_id,
            bus_id=bus_id,
            freight_remaining=freight_remaining,
            pax_timeline=pax_timeline,
            bus_tare_kg=bus_tare_kg,
            rng_seed=rng_seed,
        )
        running_delta += delta

    # Extra idle CO2 from freight unloading dwell — per trip
    units_per_stop = units_per_trip / max(1, n_pickup_stops)
    extra_dwell_per_stop = compute_extra_dwell_time(units_per_stop, extra_dwell_per_unit_s)
    total_extra_dwell_per_trip = extra_dwell_per_stop * n_pickup_stops
    co2_idle_extra = compute_co2_idle(total_extra_dwell_per_trip, BUS_IDLE_FUEL_RATE_L_PER_S)

    co2_per_trip = running_delta + co2_idle_extra
    term_c_per_day = co2_per_trip * F

    return {
        "term_c_kg_per_day": term_c_per_day,
        "co2_running_delta_kg_per_trip": running_delta,
        "co2_idle_extra_kg_per_trip": co2_idle_extra,
        "freight_per_trip_kg": freight_per_trip_kg,
        "units_per_trip": units_per_trip,
        "n_links_processed": len(bus_links),
        "bus_id": bus_id,
        "total_freight_kg_per_day": total_freight_kg_per_day,
    }


# ── Term C aggregated across all buses on the H→B route ──────────────────

def compute_term_c(
    vmean_df: pd.DataFrame,
    pax_timeline: dict,
    alpha: float,
    n_freight_units: int,
    weight_per_unit_kg: float,
    n_pickup_stops: int = 5,
    bus_tare_kg: float = BUS_TARE_KG,
    bus_trips_per_day: int = BUS_TRIPS_PER_DAY,
    rng_seed: int = 42,
    bus_id_override: str | None = None,
    hb_route_prefixes: tuple[str, ...] = ("EW_",),
    bus_id_allowlist: frozenset[str] | set[str] | None = None,
    extra_dwell_per_unit_s: float = EXTRA_DWELL_PER_UNIT_S,
) -> dict:
    """
    Compute Term C [kg CO2 per day] across the H→B route for one scenario.

    Strategy:
      - If `bus_id_override` is given, compute for exactly that bus (one full trip × F).
      - Otherwise, pick ONE representative H→B bus trip (median link count across
        the candidate buses) and scale by F = bus_trips_per_day. Averaging across
        all buses found in events would double-count because
        compute_term_c_for_bus already multiplies by F.

    Candidate selection:
      - `bus_id_allowlist` (Rotterdam): exact vehicle ids of the H→B line
        (e.g. the 197 line-44 buses from the transit schedule).
      - otherwise (toy): vehicles matching BUS_ID_PREFIXES *and* hb_route_prefixes.

    Returns
    -------
    {
      'term_c_kg_per_day': float,  one trip × F (= daily Term C on the route)
      'per_bus': list[dict],       breakdown of the representative bus
      'alpha': float,
      'weight_per_unit_kg': float,
      'total_freight_kg_per_day': float,
      'representative_bus_id': str | None,
    }
    """
    total_freight_kg_per_day = alpha * n_freight_units * weight_per_unit_kg

    if total_freight_kg_per_day <= 0:
        return {
            "term_c_kg_per_day": 0.0,
            "per_bus": [],
            "alpha": alpha,
            "weight_per_unit_kg": weight_per_unit_kg,
            "total_freight_kg_per_day": 0.0,
            "representative_bus_id": None,
        }

    # Pick the representative bus on the H→B route.
    # Toy: only EW_lower / EW_upper cross both districts → prefix filter.
    # Rotterdam: bus_id_allowlist = the exact 197 line-44 vehicle ids.
    if bus_id_override:
        representative_bus = bus_id_override
    else:
        # Pick the H→B bus whose link count is closest to the median across all
        # H→B buses. This is more representative than the first bus by insertion
        # order (which is always the earliest morning departure → lighter traffic).
        # The median-link-count bus typically reflects a mid-day trip with
        # average congestion and an average number of stops.
        if bus_id_allowlist is not None:
            allow = frozenset(bus_id_allowlist)
            bus_candidates = [
                vid for vid in vmean_df["vehicle_id"].unique() if vid in allow
            ]
        else:
            bus_candidates = [
                vid for vid in vmean_df["vehicle_id"].unique()
                if vid.startswith(BUS_ID_PREFIXES) and vid.startswith(hb_route_prefixes)
            ]
        if bus_candidates:
            counts = (
                vmean_df[vmean_df["vehicle_id"].isin(bus_candidates)]
                .groupby("vehicle_id").size()
            )
            if not counts.empty:
                median = counts.median()
                # Bus with link count nearest to the median (stable tie-break by id)
                diffs = (counts - median).abs().sort_values(kind="stable")
                representative_bus = diffs.index[0]
            else:
                representative_bus = bus_candidates[0]
        else:
            representative_bus = None

        if representative_bus is None and bus_id_allowlist is None:
            # Fall back to any bus if no H→B route bus is present (e.g. toy without EW).
            # With an explicit allowlist there is NO fallback: a non-line-44 bus
            # would silently produce a wrong Term C — better to return 0 with
            # representative_bus_id=None so the caller can flag it.
            fallback = [
                vid for vid in vmean_df["vehicle_id"].unique()
                if vid.startswith(BUS_ID_PREFIXES)
            ]
            representative_bus = fallback[0] if fallback else None

    if representative_bus is None:
        return {
            "term_c_kg_per_day": 0.0,
            "per_bus": [],
            "alpha": alpha,
            "weight_per_unit_kg": weight_per_unit_kg,
            "total_freight_kg_per_day": total_freight_kg_per_day,
            "representative_bus_id": None,
        }

    result = compute_term_c_for_bus(
        vmean_df=vmean_df,
        bus_id=representative_bus,
        pax_timeline=pax_timeline,
        total_freight_kg_per_day=total_freight_kg_per_day,
        n_pickup_stops=n_pickup_stops,
        n_freight_units_per_day=alpha * n_freight_units,
        bus_tare_kg=bus_tare_kg,
        bus_trips_per_day=bus_trips_per_day,
        rng_seed=rng_seed,
        extra_dwell_per_unit_s=extra_dwell_per_unit_s,
    )

    return {
        "term_c_kg_per_day": result["term_c_kg_per_day"],
        "per_bus": [result],
        "alpha": alpha,
        "weight_per_unit_kg": weight_per_unit_kg,
        "total_freight_kg_per_day": total_freight_kg_per_day,
        "representative_bus_id": representative_bus,
    }


if __name__ == "__main__":
    print("term_c.py: run via run_pipeline.py for full execution.")
