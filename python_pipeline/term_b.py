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
    # The drag, area, rolling and drivetrain constants are NOT imported here any
    # more: they are properties of a vehicle, and this file now reads them from
    # the VanType it is given. VAN_TYPES["base"] is assembled from exactly those
    # constants, so the default path is unchanged — but there is one place that
    # says what van is being emitted, and it is not this file.
    VAN_IDLE_FUEL_RATE_L_PER_S,
    VAN_KINEMATICS,
    VAN_TARE_KG,
    VAN_ID_PREFIX,
    VAN_PAYLOAD_CAPACITY_KG,
    VAN_PARCELS_PER_TOUR_MAX,
    VAN_STOP_IDLE_S,
    VAN_LOAD_FACTOR,
    VAN_TYPES,
    VanType,
    c_van,
)
from van_cycles import IDLE_THRESH_MS, reconstruct_van_profile

# The headline vehicle, assembled in parameters.py from the same module constants
# this file used to read directly. Every function below takes a VanType and
# defaults to this one, so the default path is arithmetically identical to what it
# was before the van size became a variable.
BASE_VAN: VanType = VAN_TYPES["base"]


def _resolve_van(van: VanType | None, van_tare_kg: float,
                 payload_capacity_kg: float, parcels_per_tour_max: int
                 ) -> tuple[float, float, int, VanType]:
    """Reconcile the loose vehicle arguments with a whole van type.

    The loose ones came first and are still how the van-capacity screening sweeps
    consolidation capacity on its own. When a VanType is given it WINS on all
    three, because that is the point of the object: a different vehicle is lighter
    AND less capacious AND smaller in frontal area, and letting a caller pass a
    large van's payload with the base van's tare would reintroduce exactly the
    'same van, bigger cargo bay' error the type exists to prevent.
    """
    if van is None:
        return van_tare_kg, payload_capacity_kg, parcels_per_tour_max, BASE_VAN
    return van.tare_kg, van.payload_capacity_kg, van.parcels_per_tour_max, van


# ── WLTC per-link van CO2 (with per-(v_mean, mass, vehicle) cache) ────────
# The CO2 of a reconstructed WLTC profile depends only on (v_mean, van_mass) and
# the vehicle's drag and rolling parameters; the link length enters only through
# the t_actual/t_sort scaling. So we cache the CO2-PER-SECOND of each distinct
# (rounded v_mean, mass, vehicle) and multiply by the link travel time. This
# collapses ~90k per-link reconstructions per fleet to a few hundred — same
# length-scaling logic as the bus (term_c._delta_co2_on_link).
#
# THE VEHICLE BELONGS IN THE KEY. Until the van size became a variable the key was
# (v_mean, mass, seed) and that was sufficient, because cd and the frontal area
# were module constants. They are arguments now, and two van types can land on the
# SAME evaluation mass — tare + payload/2 is a sum, so a small van loaded and a
# large one lightly loaded meet — at which point a cache keyed without the
# geometry would hand the second type the first one's number, silently and
# plausibly. rotterdam_surface_robust runs each cell in its own subprocess and
# would never notice; sensitivity_surface runs in-process and would.
_VAN_CO2_PER_S_CACHE: dict[tuple, float] = {}


def _van_co2_per_second(v_mean_ms: float, van_mass_kg: float, rng_seed: int = 42,
                        van: VanType = BASE_VAN) -> float:
    """CO2 [kg] per second of driving at this link mean speed and van mass,
    using the WLTC-reconstructed stop-and-go profile."""
    key = (round(v_mean_ms, 2), round(van_mass_kg, 1), rng_seed,
           van.cd, van.frontal_area_m2, van.rolling_resistance, van.drivetrain_eff)
    cached = _VAN_CO2_PER_S_CACHE.get(key)
    if cached is not None:
        return cached
    v_t = reconstruct_van_profile(v_mean_ms, rng_seed=rng_seed)
    a_t = speed_to_accel(v_t)
    # Traction CO2 (P>0 seconds; braking P<=0 contributes 0 = diesel fuel cut-off).
    co2_traction = compute_co2_running(
        v_t, a_t, van_mass_kg,
        cd=van.cd, frontal_area_m2=van.frontal_area_m2,
        rolling_coeff=van.rolling_resistance, drivetrain_eff=van.drivetrain_eff,
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
                      rng_seed: int = 42, exclude_links=None,
                      van: VanType = BASE_VAN) -> float:
    """Average CO2 [kg] of one simulated van route at the given loaded mass."""
    if not van_ids:
        return 0.0
    return co2_van_fleet(vmean_df, van_ids, van_mass_kg, rng_seed=rng_seed,
                         exclude_links=exclude_links, van=van) / len(van_ids)


# ── Per-van CO2 via speed-change method ────────────────────────────────────

def _co2_for_van_on_route(
    vmean_df: pd.DataFrame,
    van_id: str,
    van_mass_kg: float,
    van: VanType = BASE_VAN,
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
        cd=van.cd,
        frontal_area_m2=van.frontal_area_m2,
        rolling_coeff=van.rolling_resistance,
        drivetrain_eff=van.drivetrain_eff,
    )


def co2_van_fleet(
    vmean_df: pd.DataFrame,
    van_ids: list[str],
    van_mass_kg: float,
    rng_seed: int = 42,
    exclude_links: frozenset[str] | set[str] | None = None,
    van: VanType = BASE_VAN,
) -> float:
    """Sum CO2 over a list of van vehicle IDs from one MATSim run.

    Two kinematic methods (parameters.VAN_KINEMATICS):
      "wltc"         — per-link WLTC reconstruction + t_actual/t_sort length
                       scaling (captures within-link stop-and-go); the per-second
                       CO2 is cached by (v_mean, mass) for speed.
      "speed_change" — historical constant-speed-per-link profile over the route.

    Uses a single groupby instead of per-van DataFrame filters to avoid O(n*m) scans.

    exclude_links drops those links from the sum. It exists for the deadlocked
    links of the Rotterdam network (make_deadlock_links.py), and it is NOT a
    detail: on the line-44 corridor TWO links out of 96 carry 0.6% of the
    distance and 70% of the time, one of them 30 m long holding a van for 46
    minutes at 0.0 km/h. The formula charges idle fuel for every one of those
    seconds — 1.48 of the 2.63 kg of a tour — and unlike the congestion terms it
    does NOT cancel in the delta, because Term B counts tours REMOVED. Excluding
    them is the same convention already used for the vehicle-hours rows.
    """
    if not van_ids:
        return 0.0
    van_ids_set = set(van_ids)
    van_df = vmean_df[vmean_df["vehicle_id"].isin(van_ids_set)]
    if van_df.empty:
        return 0.0
    if exclude_links:
        van_df = van_df[~van_df["link_id"].isin(frozenset(exclude_links))]
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
                                             rng_seed=rng_seed, van=van) * max(1.0, tt)
        else:
            link_seq = []
            for _, row in sorted_group.iterrows():
                tt = float(row["travel_time_s"]) if has_tt else 20.0
                link_seq.append((row["link_id"], row["v_mean_ms"], max(1.0, tt)))
            v_ms, _ = build_van_speed_profile(link_seq)
            a_ms2 = speed_to_accel(v_ms)
            total += compute_co2_running(
                v_ms, a_ms2, van_mass_kg,
                cd=van.cd, frontal_area_m2=van.frontal_area_m2,
                rolling_coeff=van.rolling_resistance, drivetrain_eff=van.drivetrain_eff,
            )
    return total


# ── Van kilometres, and Euro-class NOx on those kilometres ────────────────
#
# Everything below is ADDITIVE: no function above is modified, and no CO2 number
# changes. Term B's CO2 comes from longitudinal dynamics; NOx cannot, because it
# is not proportional to fuel (combustion temperature, EGR, SCR light-off). So
# NOx is carried on the KILOMETRES instead, with published per-Euro-class
# factors from euro_factors.py.
#
# The van is the one term where that works cleanly, because the scenario really
# does take tours off the road: delta-km is large and real. (The bus is the
# opposite case - same trips, same links, delta-km exactly zero - which is why
# Term C uses engine work instead. See term_c.)

def _van_records(vmean_df: pd.DataFrame, van_ids: list[str],
                 exclude_links: frozenset[str] | set[str] | None = None):
    """The van rows the NOx side works on, with the SAME link exclusion the CO2
    side uses.

    This helper exists because the four functions below used to filter the
    dataframe each in their own way, and only co2_van_fleet took exclude_links.
    The day the NOx factors arrived, the kilometres behind the NOx and the
    kilometres behind the CO2 would have counted different links. Small - the
    deadlocked links are 30 m, so it is 0.6 % of the distance rather than the
    56 % it was worth on the CO2, which is charged per second of engine idle -
    but silently inconsistent. One filter, used by all of them.
    """
    if not van_ids:
        return None, None
    d = vmean_df[vmean_df["vehicle_id"].isin(set(van_ids))]
    if exclude_links:
        d = d[~d["link_id"].isin(frozenset(exclude_links))]
    if d.empty:
        return None, None
    tt = (d["travel_time_s"].astype(float).clip(lower=1.0)
          if "travel_time_s" in d.columns else 20.0)
    return d, tt


def van_km_on_route(vmean_df: pd.DataFrame, van_ids: list[str],
                    exclude_links: frozenset[str] | set[str] | None = None) -> float:
    """
    Kilometres driven by the given vans in one run.

    Distance is taken as v_mean x travel_time, NOT from the network's link
    lengths. The two are equal by construction - parse_events defines
    v_mean = link_length / dt - but using the same two numbers the emission
    model was charged with means the kilometres cannot silently disagree with
    the CO2 they are supposed to accompany. It also inherits, for free, the
    standing-time subtraction parse_events applies at bus stops.
    """
    d, tt = _van_records(vmean_df, van_ids, exclude_links)
    if d is None:
        return 0.0
    return float((d["v_mean_ms"].astype(float) * tt).sum() / 1000.0)


def mean_km_per_van(vmean_df: pd.DataFrame, van_ids: list[str],
                    exclude_links: frozenset[str] | set[str] | None = None) -> float:
    """Average route length [km] of one simulated van. Mirrors _mean_co2_per_van."""
    if not van_ids:
        return 0.0
    return van_km_on_route(vmean_df, van_ids, exclude_links) / len(van_ids)


def mean_nox_g_per_van(vmean_df: pd.DataFrame, van_ids: list[str],
                       euro_class: str,
                       exclude_links: frozenset[str] | set[str] | None = None,
                       ) -> tuple[float, int]:
    """
    Average NOx [g] of one simulated van route, at its per-link mean speeds.

    The factor is evaluated link by link at that link's own mean speed, so van
    NOx responds to the congestion the scenario creates instead of being a flat
    multiplier on a tour count. That costs nothing: the speeds are already in
    the dataframe.

    The curve is the EMEP/EEA Tier 3 one for a diesel N1 Class III - the class
    the Transit Custom of this model belongs to. It is a REAL-WORLD urban curve,
    which matters for what it already contains: idling at lights and at delivery
    stops is inside it, because the measurements behind it included standing
    time. Nothing may be added on top for van idle, and the CO2 side's separate
    idle charge has no NOx counterpart here by design, not by omission.

    Returns (grams, n_links_outside_published_speed_range). The second number is
    not decoration - a fitted curve is only valid over the speeds its source
    states, and a corridor that spends its time below that range is a caveat
    that belongs in the table caption.
    """
    from euro_factors import van_nox

    d, tt = _van_records(vmean_df, van_ids, exclude_links)
    if d is None:
        return 0.0, 0

    curve = van_nox(euro_class)
    v_ms = d["v_mean_ms"].astype(float)
    km = (v_ms * tt) / 1000.0
    v_kmh = v_ms * 3.6

    before = curve.n_clamped
    grams = float(sum(curve.at(v) * k for v, k in zip(v_kmh, km)))
    return grams / len(van_ids), curve.n_clamped - before


def compute_term_b_nox(
    baseline_vmean_df: pd.DataFrame,
    scenario_vmean_df: pd.DataFrame,
    term_b_result: dict,
    euro_class: str,
    exclude_links: frozenset[str] | set[str] | None = None,
) -> dict:
    """
    Term B in NOx: the tailpipe NOx of the van tours no longer driven.

    Structure is identical to the CO2 Term B, and deliberately reuses its tour
    counts rather than recomputing them, so the two currencies can never
    disagree about how many tours the operation removed:

      Component 1  tours_baseline x (one van's NOx under BASELINE congestion)
      Component 2  tours_scenario x (one van's NOx under SCENARIO congestion)
      Term B NOx   Component 1 - Component 2, clamped at 0 like the CO2

    NO SEPARATE IDLE TERM, AND THAT IS NOT AN OMISSION. The CO2 side charges van
    idle from a litres-per-second rate. The NOx side must not add anything
    equivalent, because the EMEP/EEA curve is fitted to real-world urban driving
    whose measurements already contain standing at lights and at stops: the
    idling is inside the g/km. An earlier version of this docstring claimed the
    opposite - that idle was excluded and the saving therefore conservative -
    and that was wrong in both directions. It is neither excluded nor a
    conservatism; it is counted once, inside the factor.

    `exclude_links` is passed straight through to the shared record filter, so
    the kilometres behind the NOx count exactly the links the CO2 counts.
    """
    van_ids_b = [v for v in baseline_vmean_df["vehicle_id"].unique()
                 if v.startswith(VAN_ID_PREFIX)]
    van_ids_s = [v for v in scenario_vmean_df["vehicle_id"].unique()
                 if v.startswith(VAN_ID_PREFIX)]

    nox_b, clamp_b = mean_nox_g_per_van(baseline_vmean_df, van_ids_b,
                                        euro_class, exclude_links)
    nox_s, clamp_s = mean_nox_g_per_van(scenario_vmean_df, van_ids_s,
                                        euro_class, exclude_links)
    km_b = mean_km_per_van(baseline_vmean_df, van_ids_b, exclude_links)
    km_s = mean_km_per_van(scenario_vmean_df, van_ids_s, exclude_links)

    n_b = term_b_result["tours_baseline"]
    n_s = term_b_result["tours_scenario"]

    c1, c2 = nox_b * n_b, nox_s * n_s
    return {
        "term_b_nox_g": max(0.0, c1 - c2),
        "component1_nox_g": c1,
        "component2_nox_g": c2,
        "component1_km": km_b * n_b,
        "component2_km": km_s * n_s,
        "term_b_km": km_b * n_b - km_s * n_s,
        "mean_km_per_tour": km_b,
        "mean_nox_g_per_tour": nox_b,
        "euro_class": euro_class,
        "n_links_outside_ef_speed_range": clamp_b + clamp_s,
        "idle_inside_factor": True,
    }


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
    exclude_links=None,
    van: VanType | None = None,
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
    van_tare_kg, payload_capacity_kg, parcels_per_tour_max, veh = _resolve_van(
        van, van_tare_kg, payload_capacity_kg, parcels_per_tour_max)
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
                                         rng_seed=rng_seed, exclude_links=exclude_links,
                                         van=veh)
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
            cd=veh.cd, frontal_area_m2=veh.frontal_area_m2,
            rolling_coeff=veh.rolling_resistance, drivetrain_eff=veh.drivetrain_eff,
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
    exclude_links=None,
    van: VanType | None = None,
) -> float:
    """
    CO2 emitted by the backup vans actually present in the scenario run.

    The (1−alpha)·N undelivered parcels need ⌈(1−alpha)·N / C_van(w)⌉ consolidated
    tours (or (1−alpha)·N single trips in the historical design). Backup van v_mean
    is taken directly from the scenario events.xml.
    """
    van_tare_kg, payload_capacity_kg, parcels_per_tour_max, veh = _resolve_van(
        van, van_tare_kg, payload_capacity_kg, parcels_per_tour_max)
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
                                     rng_seed=rng_seed, exclude_links=exclude_links,
                                     van=veh)
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
    exclude_links: frozenset[str] | set[str] | None = None,
    van: VanType | None = None,
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
    van_tare_kg, payload_capacity_kg, parcels_per_tour_max, veh = _resolve_van(
        van, van_tare_kg, payload_capacity_kg, parcels_per_tour_max)
    parcels_per_tour, _ = _consolidation(
        van_payload_kg, van_tare_kg, consolidate,
        payload_capacity_kg, parcels_per_tour_max, load_factor)
    tours_baseline = math.ceil(n_total_vans / parcels_per_tour) if n_total_vans > 0 else 0
    tours_scenario = (math.ceil((1 - alpha) * n_total_vans / parcels_per_tour)
                      if (1 - alpha) * n_total_vans > 0 else 0)

    def _both(excl):
        # `van`, NOT the resolved `veh`. The components resolve for themselves, and
        # handing them a VanType would make them ignore the loose van_tare_kg /
        # payload_capacity_kg / parcels_per_tour_max arguments — which is how the
        # van-capacity screening sweeps consolidation capacity on its own
        # (screening_analysis.py passes van_payload_capacity_kg with no van type).
        # Passing veh here silently priced every one of those cells as the base van.
        a, proxy = compute_component1(
            baseline_vmean_df, n_total_vans, van_payload_kg, van_tare_kg, consolidate,
            payload_capacity_kg, parcels_per_tour_max, n_pickup_stops, van_stop_idle_s,
            load_factor, rng_seed, excl, van)
        b = compute_component2(
            scenario_vmean_df, alpha, n_total_vans, van_payload_kg, van_tare_kg, consolidate,
            payload_capacity_kg, parcels_per_tour_max, n_pickup_stops, van_stop_idle_s,
            load_factor, rng_seed, excl, van)
        return a, b, proxy

    # How many van vehicles the two runs ACTUALLY contain, as opposed to how many
    # the consolidation formula says they should. The two are not the same
    # quantity and nothing else in the pipeline compares them: the components
    # average the CO2 over whatever backup_van_* ids they find and multiply that
    # mean by the FORMULA's tour count, so a run carrying seven vans analysed as
    # five tours returns a perfectly plausible wrong number, silently. That is the
    # failure mode of a stale warm-plans file (the generator reuses one whenever
    # the name already exists), and until these two counts came out there was no
    # signal anywhere that it had happened. Reported, not enforced: the proxy path
    # legitimately has zero vans in the baseline, and a caller sweeping N knows
    # what it expects and can check.
    def _n_vans(df):
        if df.empty:
            return 0
        ids = df["vehicle_id"].unique()
        return sum(1 for vid in ids if str(vid).startswith(VAN_ID_PREFIX))

    n_obs_base = _n_vans(baseline_vmean_df)
    n_obs_scen = _n_vans(scenario_vmean_df)

    c1, c2, used_proxy = _both(None)
    term_b = max(0.0, c1 - c2)  # clamp at 0: savings cannot be negative by definition

    # Second value, with the deadlocked links dropped. BOTH are reported, and the
    # excluded one is the headline: the difference IS the size of the network
    # artefact, and a reader who sees only one number cannot judge it.
    if exclude_links:
        c1x, c2x, _ = _both(exclude_links)
        term_b_excl = max(0.0, c1x - c2x)
    else:
        c1x = c2x = term_b_excl = None

    return {
        "term_b_kg": term_b,
        "term_b_excl_deadlock_kg": term_b_excl,
        "component1_excl_deadlock_kg": c1x,
        "component2_excl_deadlock_kg": c2x,
        "n_excluded_links": len(exclude_links) if exclude_links else 0,
        "component1_kg": c1,
        "component2_kg": c2,
        "used_proxy": used_proxy,
        "alpha": alpha,
        "van_payload_kg": van_payload_kg,
        "consolidated": consolidate,
        "parcels_per_tour": parcels_per_tour,
        "tours_baseline": tours_baseline,
        "tours_scenario": tours_scenario,
        # What the runs actually contained, against the two above, which are what
        # the formula asked for. A caller that knows what it inserted should
        # compare them; they are the only evidence in the pipeline that the
        # simulation and the accounting are talking about the same fleet.
        "n_vans_observed_baseline": n_obs_base,
        "n_vans_observed_scenario": n_obs_scen,
        "van_stop_idle_s": van_stop_idle_s,
        "van_load_factor": load_factor,
        # The vehicle these numbers describe. Carried out so it lands in the CSV:
        # a van-size sweep whose rows do not say which van they are is unreadable
        # a week later.
        "van_type": veh.name,
        "van_tare_kg": van_tare_kg,
        "van_payload_capacity_kg": payload_capacity_kg,
        "van_frontal_area_m2": veh.frontal_area_m2,
    }


if __name__ == "__main__":
    print("term_b.py: run via run_pipeline.py for full execution.")
    print("Standalone test requires parsed v_mean DataFrames from parse_events.py.")
