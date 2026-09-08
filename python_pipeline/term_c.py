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

The passenger dwell cancels in the delta — only the EXTRA freight unloading
dwell is added via CO2_idle_extra. Note what that dwell actually is: the
line-44 timetable reserves ZERO seconds for it (arrivalOffset ==
departureOffset at all 9 one-to-many stops), and the line carries ~1 passenger
per trip, so the boarding time MATSim produces is of the order of a second per
stop, not the "~3 min" an earlier version of this docstring claimed. The
cancellation is what makes it harmless either way.

CO2_idle_extra has two modes:
  a-priori (default)  — the 10 s/stop + 5 s/parcel convention, charged in full.
    Correct while the freight dwell is NOT simulated inside MATSim.
  measured (use_measured_idle=True) — once the dwell IS in the schedule
    (make_dwell_schedules.py), the extra standing is measured from the stop
    events: mean over the H->B fleet of (standing_scenario − standing_baseline).
    Where the timetable had slack, the parcels are unloaded while the bus was
    going to stand anyway: no extra standing, no extra fuel. The a-priori
    number is therefore an UPPER BOUND; the measured one replaces the estimate
    with the simulation's answer. Never enable this on runs whose schedule has
    no minimumStopDuration — the measured extra would be ~0 by construction.

Dynamic mass: freight_remaining decreases at each pickup point as the bus
delivers packages along the route. Using a constant mass would overestimate
Term C on later route segments.
"""

from __future__ import annotations

import pandas as pd

from dynamic_mass import (
    build_freight_remaining,
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
    BUS_TRANSMISSION_EFF,
    BUS_TRIPS_PER_DAY,
    CALORIFIC_VALUE_DIESEL_MJ_PER_L,
    CO2_FACTOR_KG_PER_L,
    EXTRA_DWELL_PER_UNIT_S,
    BUS_ID_PREFIXES,
)
from sort_cycles import reconstruct_bus_profile


# ── Term C in NOx: mass and standing, not kilometres ──────────────────────
#
# Everything in this block is ADDITIVE. No CO2 number changes.
#
# THE POINT, IN ONE SENTENCE: the bus drives the same F trips on the same links
# whether or not it carries the freight, so delta-bus-km is EXACTLY ZERO and a
# g/km emission factor would return exactly zero. What changes is the mass it
# carries and how long it stands, and those are priced separately - the mass on
# the EMEP/EEA load axis, the standing on a measured idle rate.
#
# The engine-work conversion below is no longer on the NOx path. It is kept
# because the implied brake thermal efficiency it guards is reported in the
# methodology, and because it is the check that the two efficiencies in
# parameters.py stay physically consistent with each other.
#
# The conversion needs no new physics, because emission_formula is linear:
#     E_fuel[J] -> MJ -> litres (/35.8) -> kg CO2 (x2.65)
# and compute_power_array already divides by BUS_DRIVETRAIN_EFF, which
# parameters.py documents as fuel-to-wheel. So, running it backwards:
#     W_wheel [kWh]  = CO2[kg] x (35.8 / 2.65 / 3.6) x 0.37   = CO2 x 1.389
#     W_engine [kWh] = W_wheel / BUS_TRANSMISSION_EFF          = CO2 x 1.543
#
# What is being assumed, stated plainly so it can be defended or attacked:
# adding a few hundred kg to a 12 t bus does not move the engine far enough on
# its operating map to change its SPECIFIC NOx. That is why this is legitimate
# where scaling absolute NOx off absolute fuel would not be: the Euro class and
# the SCR live entirely inside the external g/kWh factor, which is not derived
# from the fuel model.

def co2_kg_to_engine_kwh(co2_kg: float,
                         drivetrain_eff: float = BUS_DRIVETRAIN_EFF,
                         transmission_eff: float = BUS_TRANSMISSION_EFF) -> float:
    """
    Engine work [kWh] that produced a given tractive CO2 [kg], for this bus.

    Raises if the implied brake thermal efficiency is not physically sane, which
    is the cheapest possible guard against someone changing one of the two
    efficiencies without thinking about the other.
    """
    bte = drivetrain_eff / transmission_eff
    if not (0.30 <= bte <= 0.50):
        raise ValueError(
            f"implied brake thermal efficiency {bte:.3f} "
            f"(= {drivetrain_eff} / {transmission_eff}) is not a physical value "
            f"for a diesel engine. A modern Euro VI urban bus sits near 0.41. "
            f"Fix BUS_TRANSMISSION_EFF or BUS_DRIVETRAIN_EFF, not this check.")
    kwh_fuel = co2_kg / CO2_FACTOR_KG_PER_L * CALORIFIC_VALUE_DIESEL_MJ_PER_L / 3.6
    return kwh_fuel * drivetrain_eff / transmission_eff


def compute_term_c_nox(term_c_result: dict, euro_class: str,
                       bus_trips_per_day: int | None = None,
                       baseline_load_fraction: float = 0.0,
                       cross_check: bool = True) -> dict:
    """Term C in NOx: the extra tailpipe NOx of carrying freight on the bus.

    TWO EFFECTS, EACH PRICED BY THE SOURCE THAT MEASURES IT. Delta-bus-km is
    exactly zero - the same F trips on the same links - so a plain g/km factor
    would return zero. What changes is the mass carried and the time spent
    standing, and those are two different measurements:

        dNOx = [EF(v_base, load + dLoad) - EF(v_base, load)] x bus_km    <- mass
             + idle_rate_g_per_h x extra_dwell_hours_per_day             <- dwell

    THE MASS TERM moves one published axis of the EMEP/EEA curves and holds
    everything else fixed, so it is exact within the source.

    THE DWELL TERM is a measured idle rate (euro_factors.bus_idle_nox_g_per_h),
    which is the same shape the CO2 side has always used: seconds of standing
    times a per-hour rate. It is NOT read off the speed curve. A bus standing
    still is not the same object as a bus driving slowly: the slow bus in the
    source data is accelerating 18 tonnes back up to speed repeatedly, while a
    stationary one lets the exhaust cool until the SCR falls below light-off.
    Reading the curve at the depressed journey speed was the previous method and
    it understated the cost - its widest Euro VI reading implied 17.3 g/h against
    a measurement of 20. The superseded bracket is still returned under
    `legacy_*` keys so the change stays auditable.

    NO DOUBLE COUNTING. The mass term is evaluated at v_base on BOTH sides, so
    the dwell never enters it; the speed axis is not moved at all any more.

    WHY NOT g/kWh. That route needs an engine-work conversion (CO2 -> litres ->
    kWh) with a brake-thermal-efficiency assumption, and the only published
    g/kWh figures for buses are Euro VI type-approval limits, which real vehicles
    exceed in urban duty. The load axis needs neither.

    THE LOAD BASIS, which is the one judgement call. Passengers are identical in
    both worlds and cancel, so only the freight enters:

        dLoad = freight_per_trip_kg / (BUS_PASSENGER_CAPACITY x AVG_PERSON_WEIGHT_KG)

    using the thesis's own constants (80 x 75 kg = 6,000 kg). A larger nominal
    capacity would make the freight a smaller share and the bus look cheaper, so
    this is the conservative choice as well as the consistent one.

    baseline_load_fraction defaults to 0: line 44 carries about one passenger per
    trip (109 boardings over 98 trips, measured), i.e. ~1 % of capacity. Pass the
    measured value on a line where it matters.

    Returns the NOx delta and, unless cross_check=False, the same calculation run
    in ENERGY CONSUMPTION from the same table - which prices the freight in CO2
    empirically and can be compared against the physics model's own term_c_kg.
    That comparison is what makes the mixed method defensible (PIANO.md
    4.2quater D); it is reported, never used to correct anything.
    """
    from euro_factors import (IDLE_NOX_SOURCE, bus_ec,
                              bus_idle_nox_g_per_h, bus_nox)
    from parameters import AVG_PERSON_WEIGHT_KG, BUS_PASSENGER_CAPACITY

    F = bus_trips_per_day if bus_trips_per_day is not None else BUS_TRIPS_PER_DAY

    missing = [k for k in ("bus_km_per_trip", "bus_running_time_s_per_trip",
                           "extra_dwell_s_per_trip", "freight_per_trip_kg")
               if term_c_result.get(k) is None]
    if missing:
        raise ValueError(
            f"term_c_result is missing {missing} — it must come from "
            f"compute_term_c_for_bus, not from an older cached result.")

    km = float(term_c_result["bus_km_per_trip"])
    t_run = float(term_c_result["bus_running_time_s_per_trip"])
    t_stand = term_c_result.get("bus_standing_s_per_trip_baseline")
    if t_stand is None:
        raise ValueError(
            "the representative bus has no baseline standing time recorded, so "
            "its baseline journey speed cannot be formed. That speed is where "
            "the mass term is evaluated: treating the standing as zero would "
            "raise it, and the load axis flattens as speed rises, so the bus "
            "cost would be UNDERSTATED. Re-run the cell with facility events "
            "(--dwell-in-matsim), or pass the standing explicitly.")
    t_stand = float(t_stand)
    extra_dwell = float(term_c_result["extra_dwell_s_per_trip"])
    if km <= 0 or t_run <= 0:
        raise ValueError(f"bus trip geometry is degenerate: {km} km in {t_run} s")

    # Journey speed, INCLUDING standing: that is what the published curves mean
    # by mean speed. parse_events subtracts standing from v_mean, so it is added
    # back here rather than assumed to be zero.
    v_base = km / ((t_run + t_stand) / 3600.0)
    v_raw = km / ((t_run + t_stand + extra_dwell) / 3600.0)

    capacity_kg = BUS_PASSENGER_CAPACITY * AVG_PERSON_WEIGHT_KG
    d_load = float(term_c_result["freight_per_trip_kg"]) / capacity_kg
    L0 = baseline_load_fraction
    L1 = L0 + d_load

    nox = bus_nox(euro_class)
    ec = bus_ec(euro_class)
    # Taken as a DELTA: the curves live in an lru_cache, so one instance serves
    # every cell in the process and the absolute count would be the running
    # total of all the cells before this one.
    clamped_before = nox.n_clamped
    ef_base = nox.at(v_base, L0)

    # ── the dwell, priced by a MEASURED idle rate ───────────────────────────
    #
    # This is now the same shape as the CO2 side: seconds of extra standing
    # times a published per-hour rate. It replaces a bracket that existed only
    # because EMEP/EEA publishes no idle row, and that bracket was wrong in a
    # way worth recording - its upper end for Euro VI was 17.3 g/h implied,
    # while the measurement is 20. Reading the speed curve UNDERSTATED the cost
    # at both ends, because a bus that is genuinely stationary lets the SCR cool
    # in a way that no point on a driving curve represents.
    #
    # The mass term keeps coming from the EMEP load axis, which is exact: it
    # moves one published axis and nothing else. Each of the two effects is now
    # priced by the source that actually measures it.
    idle_rate = bus_idle_nox_g_per_h(euro_class)
    extra_dwell_h_per_day = extra_dwell * F / 3600.0
    dwell_g_per_day = idle_rate * extra_dwell_h_per_day

    mass_only = nox.at(v_base, L1) - ef_base
    mass_g_per_day = mass_only * km * F

    # ── the superseded bracket, kept so the change is auditable ─────────────
    #
    # Reading the curve at the speed the extra dwell produces treats our
    # standing as ordinary slow urban driving (upper end); anchoring the speed
    # drop to the idle fuel the CO2 model charges asks what that much fuel does
    # to NOx (lower end). Reported for comparison only. Nothing downstream of
    # `term_c_nox_g_per_day` depends on it.
    idle_l_per_trip = BUS_IDLE_FUEL_RATE_L_PER_S * extra_dwell
    target_mj_per_km = (idle_l_per_trip * CALORIFIC_VALUE_DIESEL_MJ_PER_L / km
                        if km else 0.0)
    v_cal = _speed_for_extra_energy(ec, v_base, L0, target_mj_per_km)
    dwell_high = nox.at(v_raw, L0) - ef_base
    dwell_low = nox.at(v_cal, L0) - ef_base

    out = {
        # One number now, not a pair: mass off the published load axis, dwell
        # off a published idle rate.
        "term_c_nox_g_per_day": mass_g_per_day + dwell_g_per_day,
        "nox_mass_component_g_per_day": mass_g_per_day,
        "nox_dwell_g_per_day": dwell_g_per_day,
        "nox_idle_rate_g_per_h": idle_rate,
        "nox_idle_rate_source": IDLE_NOX_SOURCE,
        "nox_extra_dwell_h_per_day": extra_dwell_h_per_day,
        # superseded, for the audit trail only
        "legacy_term_c_nox_g_per_day_low": mass_g_per_day + dwell_low * km * F,
        "legacy_term_c_nox_g_per_day_high": mass_g_per_day + dwell_high * km * F,
        "legacy_nox_dwell_low_g_per_day": dwell_low * km * F,
        "legacy_nox_dwell_high_g_per_day": dwell_high * km * F,
        "ef_baseline_g_per_km": ef_base,
        "v_baseline_kmh": v_base,
        "v_dwell_raw_kmh": v_raw,
        "v_dwell_fuel_calibrated_kmh": v_cal,
        "idle_litres_per_trip": idle_l_per_trip,
        "load_baseline": L0,
        "load_scenario": L1,
        "d_load": d_load,
        "bus_payload_capacity_kg": capacity_kg,
        "bus_km_per_day": km * F,
        "euro_class": euro_class,
        "bus_trips_per_day": F,
        "n_speed_clamped": nox.n_clamped - clamped_before,
        # Zero BY CONSTRUCTION, not by measurement: same trips, same links.
        # Carried explicitly so a reader never wonders whether it was forgotten.
        "delta_bus_km_per_day": 0.0,
    }

    if cross_check:
        # What the empirical energy curve says the raw speed drop costs in CO2,
        # against what the physics model charges. The gap is not an error to be
        # corrected: it IS the width of the dwell bracket, made explicit.
        d_mj_raw = ec.at(v_raw, L1) - ec.at(v_base, L0)
        litres = d_mj_raw * km * F / CALORIFIC_VALUE_DIESEL_MJ_PER_L
        out["crosscheck_ec_co2_kg_per_day"] = litres * CO2_FACTOR_KG_PER_L
        out["crosscheck_model_co2_kg_per_day"] = term_c_result.get("term_c_kg_per_day")
        model = out["crosscheck_model_co2_kg_per_day"]
        out["crosscheck_ratio"] = (out["crosscheck_ec_co2_kg_per_day"] / model
                                   if model else None)

    return out


def _speed_for_extra_energy(ec, v_base: float, load: float,
                            target_mj_per_km: float) -> float:
    """The speed at which the energy curve costs `target_mj_per_km` more than at
    v_base - i.e. the speed drop the SOURCE says burns the fuel our idle model
    says the dwell burns.

    Bisection on a monotone stretch: EC rises as speed falls over the whole urban
    range these curves cover. Returns v_base when there is nothing to find, and
    the bottom of the range when the curve cannot account for that much fuel.
    """
    if target_mj_per_km <= 0:
        return v_base
    lo, hi = ec.lowest_valid_speed, v_base
    base = ec.at(v_base, load)
    if lo >= v_base or ec.at(lo, load) - base < target_mj_per_km:
        # The curve cannot account for that much fuel within its published range;
        # the bottom of that range is the strongest statement it can make.
        return lo
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if ec.at(mid, load) - base > target_mj_per_km:
            lo = mid
        else:
            hi = mid
    return hi


# ── Measured extra standing (dwell simulated inside MATSim) ────────────────

def measured_extra_standing_per_trip(
    stop_standing_baseline: dict,
    stop_standing_scenario: dict,
    fleet_ids: frozenset[str] | set[str] | None = None,
) -> tuple[float, int]:
    """
    Mean extra standing per H->B trip [s] = mean over the fleet of
    (total standing in the scenario − total standing in the baseline).

    Both dicts come from parse_events (vmean_df.attrs["stop_standing"]):
    {vehicle_id: {link_id: standing_s}}. The same vehicle ids exist in both
    runs (identical timetable), so the difference is per-vehicle and the
    timetable holding — present on both sides — cancels. The mean is clamped
    at 0: freight can only ADD standing; a negative mean would be seed noise.

    Returns (mean_extra_s_per_trip, n_vehicles_matched).
    """
    ids = set(stop_standing_baseline) & set(stop_standing_scenario)
    if fleet_ids is not None:
        ids &= set(fleet_ids)
    if not ids:
        return 0.0, 0
    diffs = [sum(stop_standing_scenario[v].values())
             - sum(stop_standing_baseline[v].values()) for v in ids]
    return max(0.0, sum(diffs) / len(diffs)), len(ids)


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
    pax_sample_rate: float = 1.0,
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
        link_id, freight_remaining, pax_timeline, bus_id, t_enter, t_leave, bus_tare_kg,
        pax_sample_rate,
    )
    m_without = compute_mbus_passengers_only(
        bus_id, t_enter, t_leave, pax_timeline, bus_tare_kg, pax_sample_rate
    )

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
    pax_sample_rate: float = 1.0,
    use_measured_idle: bool = False,
    stop_standing_baseline: dict | None = None,
    stop_standing_scenario: dict | None = None,
    idle_fleet_ids: frozenset[str] | set[str] | None = None,
    min_measured_dwell_fraction: float = 0.10,
    pickup_link_ids: tuple[str, ...] | None = None,
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

    # Build freight-remaining profile along the route for ONE trip.
    # With the real stop links (Rotterdam) the load drops where the bus actually
    # hands parcels over; without them (toy) they are spaced evenly by index.
    if pickup_link_ids:
        wanted = set(pickup_link_ids)
        matched = [lid for lid in link_sequence if lid in wanted]
        # The terminus is never in an event-derived sequence (the leg ends there
        # with 'vehicle leaves traffic', not 'left link'), so one missing stop is
        # expected. Two or more means the wrong route — e.g. the return
        # direction, whose stops sit on entirely different links — and that would
        # silently leave the freight on board for the whole trip.
        if len(set(matched)) < n_pickup_stops - 1:
            raise ValueError(
                f"bus {bus_id}: only {len(set(matched))} of {n_pickup_stops} pickup "
                f"links found in its {len(link_sequence)}-link sequence. Expected "
                f"at least {n_pickup_stops - 1} (the terminus link is never in the "
                f"events). Check that the bus id belongs to the freight direction."
            )
        freight_remaining = build_freight_remaining(
            freight_per_trip_kg, link_sequence, matched, n_deliveries=n_pickup_stops
        )
    else:
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
            pax_sample_rate=pax_sample_rate,
        )
        running_delta += delta

    # Extra idle CO2 from freight unloading dwell — per trip.
    # a-priori: the full 10 s/stop + 5 s/parcel convention (upper bound).
    # measured: fleet-mean extra standing, scenario − baseline, from the stop
    # events — only valid when the dwell is actually in the MATSim schedule.
    units_per_stop = units_per_trip / max(1, n_pickup_stops)
    extra_dwell_per_stop = compute_extra_dwell_time(units_per_stop, extra_dwell_per_unit_s)
    apriori_dwell_per_trip = extra_dwell_per_stop * n_pickup_stops

    idle_mode = "a-priori"
    measured_dwell_per_trip = None
    n_idle_fleet = 0
    total_extra_dwell_per_trip = apriori_dwell_per_trip
    if use_measured_idle:
        if not stop_standing_baseline or not stop_standing_scenario:
            raise ValueError(
                "use_measured_idle=True but stop standing data is missing — "
                "these runs have no facility events (or parse_events was not "
                "updated). Refusing to silently fall back to the a-priori dwell.")
        measured_dwell_per_trip, n_idle_fleet = measured_extra_standing_per_trip(
            stop_standing_baseline, stop_standing_scenario, idle_fleet_ids)
        if n_idle_fleet == 0:
            raise ValueError(
                "use_measured_idle=True but no vehicle appears in BOTH runs' "
                "stop standing data — check the bus id allowlist.")
        # Guard against the silent-wrong-number case: pointing --dwell-in-matsim
        # at runs whose schedule has NO minimumStopDuration. There the scenario
        # and the baseline stand for the same passenger dwell, so the measured
        # extra collapses to ~0 and Term C would lose its whole idle component
        # without any error. Timetable slack can legitimately swallow part of
        # the dwell, but not essentially all of it (most line-44 stops have zero
        # slack), so a near-zero measurement means the wrong runs.
        if (apriori_dwell_per_trip > 0
                and measured_dwell_per_trip < min_measured_dwell_fraction
                * apriori_dwell_per_trip):
            raise ValueError(
                f"use_measured_idle=True but the measured extra standing is only "
                f"{measured_dwell_per_trip:.1f} s/trip against {apriori_dwell_per_trip:.1f} s "
                f"expected a priori ({measured_dwell_per_trip / apriori_dwell_per_trip:.1%}). "
                f"That almost certainly means these runs use a schedule WITHOUT "
                f"minimumStopDuration (i.e. pre-dwell runs) — rerun them with a "
                f"schedule from make_dwell_schedules.py, or drop --dwell-in-matsim "
                f"to charge the a-priori dwell. Lower min_measured_dwell_fraction "
                f"only if the timetable really does absorb this much.")
        total_extra_dwell_per_trip = measured_dwell_per_trip
        idle_mode = "measured"

    co2_idle_extra = compute_co2_idle(total_extra_dwell_per_trip, BUS_IDLE_FUEL_RATE_L_PER_S)

    co2_per_trip = running_delta + co2_idle_extra
    term_c_per_day = co2_per_trip * F

    return {
        "term_c_kg_per_day": term_c_per_day,
        "co2_running_delta_kg_per_trip": running_delta,
        "co2_idle_extra_kg_per_trip": co2_idle_extra,
        "idle_mode": idle_mode,
        "extra_dwell_s_per_trip": total_extra_dwell_per_trip,
        "extra_dwell_s_per_trip_apriori": apriori_dwell_per_trip,
        "extra_dwell_s_per_trip_measured": measured_dwell_per_trip,
        "n_idle_fleet_vehicles": n_idle_fleet,
        "freight_per_trip_kg": freight_per_trip_kg,
        "units_per_trip": units_per_trip,
        "n_links_processed": len(bus_links),
        "bus_id": bus_id,
        "total_freight_kg_per_day": total_freight_kg_per_day,
        # ── geometry of the trip, for the NOx side ──────────────────────────
        # Distance is v_mean x travel_time, the same two numbers the emission
        # model was charged with, so the kilometres cannot silently disagree
        # with the CO2 they accompany (same convention as term_b.van_km_on_route).
        # Running time EXCLUDES standing at stops: parse_events subtracts it from
        # v_mean. The NOx side needs the journey speed INCLUDING standing, so the
        # baseline standing of this bus is carried out too and added back there.
        "bus_km_per_trip": float(
            (bus_links["v_mean_ms"].astype(float)
             * (bus_links["travel_time_s"].astype(float).clip(lower=1.0)
                if has_tt else 20.0)).sum() / 1000.0),
        "bus_running_time_s_per_trip": float(
            bus_links["travel_time_s"].astype(float).clip(lower=1.0).sum()
            if has_tt else 20.0 * len(bus_links)),
        # None, not 0.0, when this bus is absent from the baseline standing
        # data: a silent zero would put the baseline journey speed too high and
        # overstate the whole NOx cost. The NOx side refuses rather than guesses.
        "bus_standing_s_per_trip_baseline": (
            float(sum(stop_standing_baseline[bus_id].values()))
            if stop_standing_baseline and bus_id in stop_standing_baseline
            else None),
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
    pax_sample_rate: float = 1.0,
    use_measured_idle: bool = False,
    stop_standing_baseline: dict | None = None,
    stop_standing_scenario: dict | None = None,
    min_measured_dwell_fraction: float = 0.10,
    pickup_link_ids: tuple[str, ...] | None = None,
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
        pax_sample_rate=pax_sample_rate,
        use_measured_idle=use_measured_idle,
        stop_standing_baseline=stop_standing_baseline,
        stop_standing_scenario=stop_standing_scenario,
        idle_fleet_ids=bus_id_allowlist,
        min_measured_dwell_fraction=min_measured_dwell_fraction,
        pickup_link_ids=pickup_link_ids,
    )

    return {
        "term_c_kg_per_day": result["term_c_kg_per_day"],
        "per_bus": [result],
        "alpha": alpha,
        "weight_per_unit_kg": weight_per_unit_kg,
        "total_freight_kg_per_day": total_freight_kg_per_day,
        "representative_bus_id": representative_bus,
        "idle_mode": result.get("idle_mode", "a-priori"),
        "extra_dwell_s_per_trip": result.get("extra_dwell_s_per_trip"),
        "extra_dwell_s_per_trip_measured": result.get("extra_dwell_s_per_trip_measured"),
    }


if __name__ == "__main__":
    print("term_c.py: run via run_pipeline.py for full execution.")
