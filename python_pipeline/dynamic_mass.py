"""
Dynamic bus mass profile — M_bus per link.

The bus mass changes continuously along the route:
  - Freight decreases as packages are delivered at successive pickup points (bus stops)
  - Passengers board and alight at every stop (read from MATSim events.xml)

M_bus(link) = tare_bus + passengers_on_link × 75 kg + freight_remaining_on_link

This per-link mass is used in term_c.py to compute the CO2 delta from weight.
Using a single constant M_bus would systematically overestimate Term C on later
route segments where freight has already been delivered.

For the toy simulation (Phase 1), pickup points are assumed to be equally spaced
along the bus route, each delivering 1/N_stops of total freight.
Rotterdam runs (Phase 5) use the actual bus stop sequence with explicit pickup
point locations.
"""

from __future__ import annotations

from parameters import AVG_PERSON_WEIGHT_KG, BUS_TARE_KG, EXTRA_DWELL_FIXED_OVERHEAD_S


# ── Freight remaining profile ──────────────────────────────────────────────

def build_freight_remaining(
    total_freight_kg: float,
    link_sequence: list[str],
    pickup_link_ids: list[str],
) -> dict[str, float]:
    """
    Build a {link_id: freight_remaining_kg} mapping for one bus trip.

    Freight is assumed to be equally distributed across all pickup points.
    Delivery occurs *at* the pickup link — freight on that link is still the
    pre-delivery mass (the bus arrives full, unloads, then departs lighter).
    The reduction takes effect for all subsequent links.

    Parameters
    ----------
    total_freight_kg : total freight loaded onto the bus at the hub
    link_sequence    : ordered list of link IDs along the bus route
    pickup_link_ids  : subset of link_sequence where freight is delivered

    Returns
    -------
    {link_id: freight_remaining_kg} for every link in link_sequence
    """
    if not pickup_link_ids or total_freight_kg <= 0:
        return {lid: 0.0 for lid in link_sequence}

    n_pickups = len(pickup_link_ids)
    delta_per_stop = total_freight_kg / n_pickups
    pickup_set = set(pickup_link_ids)

    remaining = total_freight_kg
    result: dict[str, float] = {}
    for lid in link_sequence:
        result[lid] = remaining
        if lid in pickup_set:
            remaining = max(0.0, remaining - delta_per_stop)

    return result


def build_freight_remaining_uniform(
    total_freight_kg: float,
    link_sequence: list[str],
    n_pickup_stops: int,
) -> dict[str, float]:
    """
    Convenience wrapper: evenly space n_pickup_stops pickup points along
    link_sequence and call build_freight_remaining.

    Used for the toy simulation where explicit pickup points are not defined.
    """
    if n_pickup_stops <= 0 or not link_sequence:
        return {lid: 0.0 for lid in link_sequence}

    # Pick evenly-spaced indices along the route (deduplicate to avoid under-delivery)
    step = max(1, len(link_sequence) // n_pickup_stops)
    pickup_links = list(dict.fromkeys(  # preserves order, removes duplicates
        link_sequence[min(i * step, len(link_sequence) - 1)]
        for i in range(1, n_pickup_stops + 1)
    ))

    return build_freight_remaining(total_freight_kg, link_sequence, pickup_links)


# ── Per-link bus mass ──────────────────────────────────────────────────────

def occupant_mass_kg(avg_pax_sim: float, pax_sample_rate: float = 1.0) -> float:
    """
    Mass of the people on board [kg] = (real passengers + driver) × 75.

    Two corrections live here, and they live in ONE place on purpose, because
    keeping them in step across the mass functions is what previously failed:

    1. `pax_sample_rate` — pax_timeline counts SIMULATED passengers. On a 10%
       population sample each of them stands for ten real ones, while the freight
       added alongside is already at real scale. Dividing puts both on the same
       scale. The toy runs a full population (rate 1.0) and is unaffected.
    2. `+1` driver — parse_events counts PersonEntersPtVehicle only, so the
       driver (a plain PersonEntersVehicle on the bus) is deliberately not in
       pax_timeline. He is real mass on the axles, so he is added back here.
       This matches the explicit +1 in feasibility.check_feasibility and
       feasibility.compute_alpha_max.

    Both corrections cancel exactly in the Term C delta (the formula is linear
    in mass and both branches share this term). They matter for any ABSOLUTE
    bus mass, such as bus_absolute_co2_check.
    """
    rate = pax_sample_rate if pax_sample_rate > 0 else 1.0
    return (avg_pax_sim / rate + 1.0) * AVG_PERSON_WEIGHT_KG


def compute_mbus_on_link(
    link_id: str,
    freight_remaining: dict[str, float],
    pax_timeline: dict,
    bus_id: str,
    t_enter: float,
    t_leave: float,
    bus_tare_kg: float = BUS_TARE_KG,
    pax_sample_rate: float = 1.0,
) -> float:
    """
    Total bus mass on a specific link during [t_enter, t_leave].

    M_bus = tare + (avg_passengers/sample_rate + driver) × 75 kg
            + freight_remaining_on_link

    Parameters
    ----------
    link_id          : the link being traversed
    freight_remaining: {link_id: freight_kg} from build_freight_remaining()
    pax_timeline     : from parse_events.parse_events() — boarding/alighting events
    bus_id           : bus vehicle ID (for pax_timeline lookup)
    t_enter, t_leave : entry/exit time on this link [s since midnight]
    bus_tare_kg      : bus empty mass [kg]
    pax_sample_rate  : population sampling rate of the run (0.10 on Rotterdam,
                       1.0 on the toy) — see occupant_mass_kg()
    """
    from parse_events import get_avg_passengers_on_link

    avg_pax = get_avg_passengers_on_link(pax_timeline, bus_id, t_enter, t_leave)
    freight = freight_remaining.get(link_id, 0.0)
    return bus_tare_kg + occupant_mass_kg(avg_pax, pax_sample_rate) + freight


def compute_mbus_passengers_only(
    bus_id: str,
    t_enter: float,
    t_leave: float,
    pax_timeline: dict,
    bus_tare_kg: float = BUS_TARE_KG,
    pax_sample_rate: float = 1.0,
) -> float:
    """
    Bus mass without freight — used to compute Term C delta
    (CO2_with_freight − CO2_without_freight).

    Shares occupant_mass_kg() with compute_mbus_on_link so the two can never
    drift apart: the delta between them must be the freight and nothing else.
    """
    from parse_events import get_avg_passengers_on_link

    avg_pax = get_avg_passengers_on_link(pax_timeline, bus_id, t_enter, t_leave)
    return bus_tare_kg + occupant_mass_kg(avg_pax, pax_sample_rate)


# ── Extra dwell time from freight unloading ────────────────────────────────

def compute_extra_dwell_time(
    n_units_unloaded: float,
    extra_dwell_per_unit_s: float,
    fixed_overhead_s: float = EXTRA_DWELL_FIXED_OVERHEAD_S,
) -> float:
    """
    Extra dwell time at a pickup stop for freight unloading [seconds].

    Linear model: extra_dwell = fixed_overhead + extra_dwell_per_unit × n_units
    The fixed overhead covers door opening, ramp deployment, signature/handoff.

    Only this increment is added to CO2_idle in Term C.
    The normal ~3 min passenger dwell already in MATSim v_mean cancels in the delta.

    Parameters
    ----------
    n_units_unloaded        : freight units delivered at this stop
    extra_dwell_per_unit_s  : seconds per unit (parameters.EXTRA_DWELL_PER_UNIT_S = 5.0)
    fixed_overhead_s        : fixed extra time per freight stop
                              (parameters.EXTRA_DWELL_FIXED_OVERHEAD_S, default 10 s).
                              At low units_per_stop this overhead dominates the
                              per-stop dwell and propagates into Term C —
                              keep both parameters in parameters.py to enable
                              sensitivity analysis over the full range.
    """
    if n_units_unloaded <= 0:
        return 0.0
    return fixed_overhead_s + extra_dwell_per_unit_s * n_units_unloaded


# ── Standalone test ────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Example: 10-link route, 3 pickup stops, 500 kg total freight
    route = [f"link_{i}" for i in range(10)]
    pickup = ["link_2", "link_5", "link_8"]
    freight = build_freight_remaining(500.0, route, pickup)
    print("Freight remaining per link (kg):")
    for lid, kg in freight.items():
        print(f"  {lid}: {kg:.1f} kg")

    # Extra dwell at a stop with 5 units
    dwell = compute_extra_dwell_time(n_units_unloaded=5, extra_dwell_per_unit_s=15.0)
    print(f"\nExtra dwell for 5 units: {dwell:.0f} s")
