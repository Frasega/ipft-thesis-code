"""
Term B — the van-removal saving S_van: CO2 of the van tours no longer driven.

Term B = CO2(removed tours, baseline v_mean)
       − CO2((1−alpha)·N backup tours, scenario v_mean)

Vans are consolidated (CONSOLIDATE_VANS): one tour carries C_van(w) =
min(150, floor(1100/w)) parcels, so n_tours = ceil((1−alpha)·N / C_van(w)); the
last, partly-filled tour is treated as full (a small conservative over-count).
Each tour is evaluated at its mean loaded mass, tare + VAN_LOAD_FACTOR·C_van·w
(load factor 1/2: exact for a stepwise-declining load because P(t) is affine in
the mass; see the mean-mass derivation in the thesis).

Per-link CO2 comes from the longitudinal-dynamics formula evaluated on a 1 Hz
profile reconstructed from the WLTC phases (van_cycles.reconstruct_van_profile,
micro-trip recombination): in-traffic idle is part of the reconstructed cycle,
and each tour adds the delivery-stop idle bracket (VAN_STOP_IDLE_S per stop at
the n_pickup_stops lockers; low = engine off + restart, high = full service time).

The two speed profiles come from different MATSim runs:
  Component 1 (removed tours): v_mean from the baseline run (alpha=0), same congestion
    → counterfactual: these tours would have driven under full-congestion conditions
  Component 2 (backup tours): v_mean directly from the scenario run (alpha=x)
    → actual: backup vans ARE in the simulation, their v_mean is real

Term B is always ≥ 0:
  alpha=0  → no tours removed → Component 1 = 0 → Term B = 0
  alpha=1  → no backup tours  → Component 2 = 0 → Term B = max

Limitation: the routing counterfactual for removed tours uses the baseline
link sequence and speeds, which approximates what those vans would have
experienced. On a dense urban network, this approximation is acceptable.
Declared as a thesis limitation.
"""

from __future__ import annotations

import pandas as pd

import math

import numpy as np

from emission_formula import (
    build_van_speed_profile,
    compute_co2_idle,
    compute_co2_running,
    speed_to_accel,
)
from parameters import (
    AVG_PERSON_WEIGHT_KG,
    CONSOLIDATE_VANS,
    VAN_CD,
    VAN_DRIVETRAIN_EFF,
    VAN_FRONTAL_AREA_M2,
    VAN_IDLE_FUEL_RATE_L_PER_S,
    VAN_KINEMATICS,
    VAN_ROLLING_RESISTANCE,
    VAN_TARE_KG,
    VAN_ID_PREFIX,
    VAN_PAYLOAD_CAPACITY_KG,
    VAN_PARCELS_PER_TOUR_MAX,
    VAN_STOP_IDLE_S,
    VAN_LOAD_FACTOR,
    c_van,
)
from van_cycles import IDLE_THRESH_MS, reconstruct_van_profile


# ── WLTC per-link van CO2 (with per-(v_mean, mass) cache) ─────────────────
# The CO2 of a reconstructed WLTC profile depends only on (v_mean, van_mass);
# the link length enters only through the t_actual/t_sort scaling. So we cache
# the CO2-PER-SECOND of each distinct (rounded v_mean, mass) and multiply by the
# link travel time. This collapses ~90k per-link reconstructions per fleet to a
# few hundred — same length-scaling logic as the bus (term_c._delta_co2_on_link).
_VAN_CO2_PER_S_CACHE: dict[tuple[float, float], float] = {}


def _van_co2_per_second(v_mean_ms: float, van_mass_kg: float, rng_seed: int = 42) -> float:
    """CO2 [kg] per second of driving at this link mean speed and van mass,
    using the WLTC-reconstructed stop-and-go profile."""
    key = (round(v_mean_ms, 2), round(van_mass_kg, 1), rng_seed)
    cached = _VAN_CO2_PER_S_CACHE.get(key)
    if cached is not None:
        return cached
    v_t = reconstruct_van_profile(v_mean_ms, rng_seed=rng_seed)
    a_t = speed_to_accel(v_t)
    # Traction CO2 (P>0 seconds; braking P<=0 contributes 0 = diesel fuel cut-off).
    co2_traction = compute_co2_running(
        v_t, a_t, van_mass_kg,
        cd=VAN_CD, frontal_area_m2=VAN_FRONTAL_AREA_M2,
        rolling_coeff=VAN_ROLLING_RESISTANCE, drivetrain_eff=VAN_DRIVETRAIN_EFF,
    )
    # Idle CO2: the engine still burns fuel at true stops (v≈0). NOT applied during
    # braking (v>0, P<0): a modern diesel cuts injection on overrun. See HANDOFF §14.
    idle_s = int(np.count_nonzero(v_t < IDLE_THRESH_MS))
    co2_idle = compute_co2_idle(idle_s, VAN_IDLE_FUEL_RATE_L_PER_S)
    per_s = (co2_traction + co2_idle) / max(1, len(v_t))
    _VAN_CO2_PER_S_CACHE[key] = per_s
    return per_s


# ── Consolidation helper ──────────────────────────────────────────────────

def _consolidation(weight_per_unit_kg: float, van_tare_kg: float, consolidate: bool,
                   payload_capacity_kg: float = VAN_PAYLOAD_CAPACITY_KG,
                   parcels_per_tour_max: int = VAN_PARCELS_PER_TOUR_MAX,
                   load_factor: float = VAN_LOAD_FACTOR):
    """Resolve the per-tour parcel count and the tour EVALUATION mass.

    Consolidated (the corrected design): one van tour carries C_van(w) parcels,
    departing with C_van(w)·w kg of payload, and Term B counts tours, not parcels.
    Historical (consolidate=False): one van per parcel, loaded with a single w kg.

    The tour is EVALUATED at van_tare + load_factor·payload. The default
    load_factor = VAN_LOAD_FACTOR = 0.5 is the time-averaged mass of the
    linearly declining load (parameters.py, decided 2026-07-08): exact because
    traction power is affine in mass. load_factor=1.0 reproduces the historical
    full-departure-mass evaluation (kept as a sensitivity).

    payload_capacity_kg / parcels_per_tour_max default to the base Ford Transit
    Custom; the van-capacity screening (HANDOFF §8) overrides them to sweep van
    types. Tare is held fixed (the screening lever is consolidation capacity, not
    vehicle mass), declared as a sandbox simplification.

    Returns (parcels_per_tour, van_mass_kg).
    """
    if consolidate:
        parcels_per_tour = c_van(weight_per_unit_kg, payload_capacity_kg, parcels_per_tour_max)
        payload_kg = parcels_per_tour * weight_per_unit_kg
    else:
        parcels_per_tour = 1
        payload_kg = weight_per_unit_kg
    return parcels_per_tour, van_tare_kg + load_factor * payload_kg


def _mean_co2_per_van(vmean_df: pd.DataFrame, van_ids: list[str], van_mass_kg: float,
                      rng_seed: int = 42) -> float:
    """Average CO2 [kg] of one simulated van route at the given loaded mass."""
    if not van_ids:
        return 0.0
    return co2_van_fleet(vmean_df, van_ids, van_mass_kg, rng_seed=rng_seed) / len(van_ids)


# ── Per-van CO2 via speed-change method ────────────────────────────────────

def _co2_for_van_on_route(
    vmean_df: pd.DataFrame,
    van_id: str,
    van_mass_kg: float,
) -> float:
    """
    Compute CO2 [kg] for one van making one full trip, using v_mean per link.

    Builds a constant-speed-per-link profile (speed-change method) and applies
    the longitudinal dynamics formula.
    """
    van_links = vmean_df[vmean_df["vehicle_id"] == van_id].copy()
    if van_links.empty:
        return 0.0

    # Sort by entry time to get route order
    van_links = van_links.sort_values("time_entered_s")

    # Build (link_id, v_mean_ms, travel_time_s) from actual MATSim event timestamps.
    # travel_time_s is stored in the DataFrame by parse_events.py.
    has_tt = "travel_time_s" in van_links.columns
    link_seq = []
    for _, row in van_links.iterrows():
        tt = float(row["travel_time_s"]) if has_tt else 20.0  # 20 s fallback (~200 m at 36 km/h)
        link_seq.append((row["link_id"], row["v_mean_ms"], max(1.0, tt)))
    v_ms, _ = build_van_speed_profile(link_seq)
    a_ms2 = speed_to_accel(v_ms)

    return compute_co2_running(
        v_ms, a_ms2, van_mass_kg,
        cd=VAN_CD,
        frontal_area_m2=VAN_FRONTAL_AREA_M2,
        rolling_coeff=VAN_ROLLING_RESISTANCE,
        drivetrain_eff=VAN_DRIVETRAIN_EFF,
    )


def co2_van_fleet(
    vmean_df: pd.DataFrame,
    van_ids: list[str],
    van_mass_kg: float,
    rng_seed: int = 42,
) -> float:
    """Sum CO2 over a list of van vehicle IDs from one MATSim run.

    Two kinematic methods (parameters.VAN_KINEMATICS):
      "wltc"         — per-link WLTC reconstruction + t_actual/t_sort length
                       scaling (captures within-link stop-and-go); the per-second
                       CO2 is cached by (v_mean, mass) for speed.
      "speed_change" — historical constant-speed-per-link profile over the route.

    Uses a single groupby instead of per-van DataFrame filters to avoid O(n*m) scans.
    """
    if not van_ids:
        return 0.0
    van_ids_set = set(van_ids)
    van_df = vmean_df[vmean_df["vehicle_id"].isin(van_ids_set)]
    if van_df.empty:
        return 0.0
    has_tt = "travel_time_s" in van_df.columns
    total = 0.0
    for vid, group in van_df.groupby("vehicle_id", sort=False):
        if vid not in van_ids_set:
            continue
        sorted_group = group.sort_values("time_entered_s")

        if VAN_KINEMATICS == "wltc":
            # Per link: CO2/s at the link mean speed (WLTC stop-and-go) × link
            # travel time = CO2 for exactly that link's length.
            for _, row in sorted_group.iterrows():
                tt = float(row["travel_time_s"]) if has_tt else 20.0
                total += _van_co2_per_second(row["v_mean_ms"], van_mass_kg,
                                             rng_seed=rng_seed) * max(1.0, tt)
        else:
            link_seq = []
            for _, row in sorted_group.iterrows():
                tt = float(row["travel_time_s"]) if has_tt else 20.0
                link_seq.append((row["link_id"], row["v_mean_ms"], max(1.0, tt)))
            v_ms, _ = build_van_speed_profile(link_seq)
            a_ms2 = speed_to_accel(v_ms)
            total += compute_co2_running(
                v_ms, a_ms2, van_mass_kg,
                cd=VAN_CD, frontal_area_m2=VAN_FRONTAL_AREA_M2,
                rolling_coeff=VAN_ROLLING_RESISTANCE, drivetrain_eff=VAN_DRIVETRAIN_EFF,
            )
    return total


# ── Component 1: removed vans (counterfactual, baseline v_mean) ───────────

def compute_component1(
    baseline_vmean_df: pd.DataFrame,
    n_total_vans: int,
    weight_per_unit_kg: float,
    van_tare_kg: float = VAN_TARE_KG,
    consolidate: bool = CONSOLIDATE_VANS,
    payload_capacity_kg: float = VAN_PAYLOAD_CAPACITY_KG,
    parcels_per_tour_max: int = VAN_PARCELS_PER_TOUR_MAX,
    n_pickup_stops: int = 0,
    van_stop_idle_s: float = VAN_STOP_IDLE_S,
    load_factor: float = VAN_LOAD_FACTOR,
    rng_seed: int = 42,
) -> tuple[float, bool]:
    """
    CO2 that all N_total parcels' vans would have emitted under baseline congestion.

    In the baseline run (alpha=0) ALL N_total vans are on the road; their v_mean is
    taken directly from the baseline events.xml. With consolidation, those N parcels
    are carried on ⌈N / C_van(w)⌉ heavier tours rather than N single-parcel trips:
    Term B counts tours at the loaded mass, reusing the simulated corridor geometry.

    If the baseline run has no backup_van_* IDs (no vans inserted), we approximate
    via background car v_mean and flag it.

    Returns (co2_kg, used_proxy).
    """
    parcels_per_tour, van_mass = _consolidation(
        weight_per_unit_kg, van_tare_kg, consolidate,
        payload_capacity_kg, parcels_per_tour_max, load_factor)
    n_tours = math.ceil(n_total_vans / parcels_per_tour) if n_total_vans > 0 else 0
    # Delivery-stop idle: each tour idles at the n_pickup_stops corridor lockers it
    # serves (bracketed via van_stop_idle_s; HANDOFF §3.4). Charged per tour.
    stop_idle = n_tours * compute_co2_idle(van_stop_idle_s * n_pickup_stops,
                                           VAN_IDLE_FUEL_RATE_L_PER_S)

    van_ids = [
        vid for vid in baseline_vmean_df["vehicle_id"].unique()
        if vid.startswith(VAN_ID_PREFIX)
    ]

    if van_ids:
        # Real baseline: vans are in the simulation. Average one van's route CO2 at
        # the loaded mass, then multiply by the number of tours (consolidated) or
        # the full parcel count (historical, parcels_per_tour=1 → n_tours=N).
        mean_per_van = _mean_co2_per_van(baseline_vmean_df, van_ids, van_mass,
                                         rng_seed=rng_seed)
        return mean_per_van * n_tours + stop_idle, False
    else:
        # Proxy: no vans in baseline — use background car v_mean as approximation
        bg_df = baseline_vmean_df[baseline_vmean_df["vehicle_type"] == "background"]
        if bg_df.empty:
            return 0.0, True
        avg_v = bg_df["v_mean_ms"].mean()
        # Synthesise a constant-speed profile: 1 trip = avg_v for 300 s (proxy)
        import numpy as np
        v_ms = np.full(300, avg_v)
        a_ms2 = speed_to_accel(v_ms)
        co2_per_van = compute_co2_running(
            v_ms, a_ms2, van_mass,
            cd=VAN_CD, frontal_area_m2=VAN_FRONTAL_AREA_M2,
            rolling_coeff=VAN_ROLLING_RESISTANCE, drivetrain_eff=VAN_DRIVETRAIN_EFF,
        )
        return co2_per_van * n_tours + stop_idle, True


# ── Component 2: backup vans (actual, scenario v_mean) ────────────────────

def compute_component2(
    scenario_vmean_df: pd.DataFrame,
    alpha: float,
    n_total_vans: int,
    weight_per_unit_kg: float,
    van_tare_kg: float = VAN_TARE_KG,
    consolidate: bool = CONSOLIDATE_VANS,
    payload_capacity_kg: float = VAN_PAYLOAD_CAPACITY_KG,
    parcels_per_tour_max: int = VAN_PARCELS_PER_TOUR_MAX,
    n_pickup_stops: int = 0,
    van_stop_idle_s: float = VAN_STOP_IDLE_S,
    load_factor: float = VAN_LOAD_FACTOR,
    rng_seed: int = 42,
) -> float:
    """
    CO2 emitted by the backup vans actually present in the scenario run.

    The (1−alpha)·N undelivered parcels need ⌈(1−alpha)·N / C_van(w)⌉ consolidated
    tours (or (1−alpha)·N single trips in the historical design). Backup van v_mean
    is taken directly from the scenario events.xml.
    """
    parcels_per_tour, van_mass = _consolidation(
        weight_per_unit_kg, van_tare_kg, consolidate,
        payload_capacity_kg, parcels_per_tour_max, load_factor)
    n_backup_parcels = (1 - alpha) * n_total_vans
    n_tours = math.ceil(n_backup_parcels / parcels_per_tour) if n_backup_parcels > 0 else 0
    if n_tours == 0:
        return 0.0

    van_ids = [
        vid for vid in scenario_vmean_df["vehicle_id"].unique()
        if vid.startswith(VAN_ID_PREFIX)
    ]
    if not van_ids:
        return 0.0

    # Average one backup van's scenario-congestion route CO2 at the loaded mass,
    # then scale to the number of consolidated tours, plus the per-tour delivery-stop
    # idle at the n_pickup_stops corridor lockers (bracketed; HANDOFF §3.4).
    mean_per_van = _mean_co2_per_van(scenario_vmean_df, van_ids, van_mass,
                                     rng_seed=rng_seed)
    stop_idle = n_tours * compute_co2_idle(van_stop_idle_s * n_pickup_stops,
                                           VAN_IDLE_FUEL_RATE_L_PER_S)
    return mean_per_van * n_tours + stop_idle


# ── Term B ─────────────────────────────────────────────────────────────────

def compute_term_b(
    baseline_vmean_df: pd.DataFrame,
    scenario_vmean_df: pd.DataFrame,
    alpha: float,
    n_total_vans: int,
    van_payload_kg: float,
    van_tare_kg: float = VAN_TARE_KG,
    consolidate: bool = CONSOLIDATE_VANS,
    payload_capacity_kg: float = VAN_PAYLOAD_CAPACITY_KG,
    parcels_per_tour_max: int = VAN_PARCELS_PER_TOUR_MAX,
    n_pickup_stops: int = 0,
    van_stop_idle_s: float = VAN_STOP_IDLE_S,
    load_factor: float = VAN_LOAD_FACTOR,
    rng_seed: int = 42,
) -> dict:
    """
    Term B = Component1 − Component2 [kg CO2 saved per day by removing vans].

    `van_payload_kg` is the per-PARCEL package weight w (light/medium/heavy). The
    consolidated van mass is derived internally from C_van(w) and evaluated at
    tare + load_factor·payload (default: mean of the declining load, 0.5).

    Returns a dict with breakdown for reporting:
      {
        'term_b_kg', 'component1_kg', 'component2_kg', 'used_proxy', 'alpha',
        'van_payload_kg',                 per-parcel package weight w
        'consolidated': bool,
        'parcels_per_tour': int,          C_van(w) (1 if not consolidated)
        'tours_baseline': int,            ⌈N / C_van⌉
        'tours_scenario': int,            ⌈(1-alpha)·N / C_van⌉
      }
    """
    parcels_per_tour, _ = _consolidation(
        van_payload_kg, van_tare_kg, consolidate,
        payload_capacity_kg, parcels_per_tour_max, load_factor)
    tours_baseline = math.ceil(n_total_vans / parcels_per_tour) if n_total_vans > 0 else 0
    tours_scenario = (math.ceil((1 - alpha) * n_total_vans / parcels_per_tour)
                      if (1 - alpha) * n_total_vans > 0 else 0)

    c1, used_proxy = compute_component1(
        baseline_vmean_df, n_total_vans, van_payload_kg, van_tare_kg, consolidate,
        payload_capacity_kg, parcels_per_tour_max, n_pickup_stops, van_stop_idle_s,
        load_factor, rng_seed
    )
    c2 = compute_component2(
        scenario_vmean_df, alpha, n_total_vans, van_payload_kg, van_tare_kg, consolidate,
        payload_capacity_kg, parcels_per_tour_max, n_pickup_stops, van_stop_idle_s,
        load_factor, rng_seed
    )
    term_b = max(0.0, c1 - c2)  # clamp at 0: savings cannot be negative by definition

    return {
        "term_b_kg": term_b,
        "component1_kg": c1,
        "component2_kg": c2,
        "used_proxy": used_proxy,
        "alpha": alpha,
        "van_payload_kg": van_payload_kg,
        "consolidated": consolidate,
        "parcels_per_tour": parcels_per_tour,
        "tours_baseline": tours_baseline,
        "tours_scenario": tours_scenario,
        "van_stop_idle_s": van_stop_idle_s,
        "van_load_factor": load_factor,
    }


if __name__ == "__main__":
    print("term_b.py: run via run_pipeline.py for full execution.")
    print("Standalone test requires parsed v_mean DataFrames from parse_events.py.")
