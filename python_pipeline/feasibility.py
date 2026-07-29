"""
Step 0 — Feasibility check for each (alpha, weight_regime) scenario.

The constraints are evaluated PER BUS TRIP, not per day:
  - the daily freight α·N·w is uniformly distributed over F bus trips/day
  - freight_per_trip = α·N·w / F is what each individual trip actually carries

  Constraint 1 — Freight capacity (per trip):
    freight_per_trip  ≤  BUS_FREIGHT_CAPACITY_KG

  Constraint 2 — Gross vehicle weight (per trip):
    tare_bus + max_passengers × 75 kg + freight_per_trip  ≤  BUS_MAX_GVW_KG

Infeasible combinations are FLAGGED and RETAINED in the sensitivity surface —
not excluded. Showing a high-gain scenario that is physically infeasible is
informative: it identifies which constraint is binding and how close the system
is to feasibility (e.g. a larger bus would unlock that gain).

Results and discussion must clearly distinguish feasible from infeasible
(alpha, weight) combinations.
"""

from __future__ import annotations

from dataclasses import dataclass

from parameters import (
    AVG_PERSON_WEIGHT_KG,
    BUS_FREIGHT_CAPACITY_KG,
    BUS_FREIGHT_NMAX_PARCELS,
    BUS_MAX_GVW_KG,
    BUS_PASSENGER_CAPACITY,
    BUS_TARE_KG,
    BUS_TRIPS_PER_DAY,
    ALPHA_VALUES,
    WEIGHT_REGIMES,
)


@dataclass
class FeasibilityResult:
    alpha: float
    weight_regime: str
    weight_per_unit_kg: float
    n_freight_units: int
    bus_trips_per_day: int
    freight_on_bus_per_trip_kg: float  # α·N·w / F
    freight_total_daily_kg: float       # α·N·w (for context)
    units_on_bus_per_trip: float        # α·N / F (parcels per trip — space check)
    c1_ok: bool   # freight WEIGHT capacity constraint (per trip)
    c2_ok: bool   # GVW constraint (per trip)
    c3_ok: bool   # on-board SPACE constraint (parcels per trip ≤ N_max)
    c1_headroom_kg: float
    c2_headroom_kg: float
    c3_headroom_parcels: float
    n_max_parcels: int

    @property
    def feasible(self) -> bool:
        return self.c1_ok and self.c2_ok and self.c3_ok

    @property
    def binding_constraint(self) -> str:
        if self.feasible:
            return "none"
        broken = []
        if not self.c1_ok:
            broken.append("freight_capacity")
        if not self.c2_ok:
            broken.append("gvw")
        if not self.c3_ok:
            broken.append("space")
        return broken[0] if len(broken) == 1 else "+".join(broken)

    def __str__(self) -> str:
        status = "FEASIBLE  " if self.feasible else f"INFEASIBLE [{self.binding_constraint}]"
        return (
            f"alpha={self.alpha:.0%}  weight={self.weight_regime} ({self.weight_per_unit_kg} kg)"
            f"  freight/trip={self.freight_on_bus_per_trip_kg:.0f} kg"
            f" ({self.units_on_bus_per_trip:.0f} parcels)"
            f"  ({self.freight_total_daily_kg:.0f} kg/day over {self.bus_trips_per_day} trips)"
            f"  {status}"
            f"  C1 headroom={self.c1_headroom_kg:.0f} kg"
            f"  C2 headroom={self.c2_headroom_kg:.0f} kg"
            f"  C3 headroom={self.c3_headroom_parcels:.0f} parcels"
        )


def check_feasibility(
    alpha: float,
    n_freight_units: int,
    weight_per_unit_kg: float,
    weight_regime: str = "unknown",
    bus_tare_kg: float = BUS_TARE_KG,
    bus_max_gvw_kg: float = BUS_MAX_GVW_KG,
    bus_freight_capacity_kg: float = BUS_FREIGHT_CAPACITY_KG,
    bus_passenger_capacity: int = BUS_PASSENGER_CAPACITY,
    bus_trips_per_day: int = BUS_TRIPS_PER_DAY,
    n_max_parcels: int = BUS_FREIGHT_NMAX_PARCELS,
) -> FeasibilityResult:
    """
    Check the three physical constraints for one (alpha, weight) scenario.

    Constraints are evaluated PER BUS TRIP. The total daily freight α·N·w is
    uniformly distributed across F bus trips, so each individual trip carries
    freight_per_trip = α·N·w / F. This matches the per-trip Term C calculation
    in compute_term_c_for_bus.

      Constraint 1 — freight WEIGHT capacity:  freight_per_trip ≤ BUS_FREIGHT_CAPACITY_KG
      Constraint 2 — gross vehicle weight:     tare + pax + freight_per_trip ≤ GVW
      Constraint 3 — on-board SPACE:           parcels_per_trip ≤ N_max

    Parameters
    ----------
    alpha              : load success rate [0, 1]
    n_freight_units    : total daily freight units arriving at hub
    weight_per_unit_kg : package weight for this weight regime [kg]
    weight_regime      : label ("light", "medium", "heavy") for reporting
    bus_trips_per_day  : F — used to derive freight per trip
    n_max_parcels      : on-board locker compartments (space cap per trip)
    """
    F = max(1, int(bus_trips_per_day))
    freight_daily = alpha * n_freight_units * weight_per_unit_kg
    freight_per_trip = freight_daily / F
    units_per_trip = (alpha * n_freight_units) / F

    c1_headroom = bus_freight_capacity_kg - freight_per_trip
    c1_ok = freight_per_trip <= bus_freight_capacity_kg

    # +1 person = the driver (not included in passenger capacity). In the Term C
    # delta the driver mass cancels exactly (P is linear in M), but for the GVW
    # constraint it is real mass on the axles.
    total_mass = (bus_tare_kg + (bus_passenger_capacity + 1) * AVG_PERSON_WEIGHT_KG
                  + freight_per_trip)
    c2_headroom = bus_max_gvw_kg - total_mass
    c2_ok = total_mass <= bus_max_gvw_kg

    c3_headroom = n_max_parcels - units_per_trip
    c3_ok = units_per_trip <= n_max_parcels

    return FeasibilityResult(
        alpha=alpha,
        weight_regime=weight_regime,
        weight_per_unit_kg=weight_per_unit_kg,
        n_freight_units=n_freight_units,
        bus_trips_per_day=F,
        freight_on_bus_per_trip_kg=freight_per_trip,
        freight_total_daily_kg=freight_daily,
        units_on_bus_per_trip=units_per_trip,
        c1_ok=c1_ok,
        c2_ok=c2_ok,
        c3_ok=c3_ok,
        c1_headroom_kg=c1_headroom,
        c2_headroom_kg=c2_headroom,
        c3_headroom_parcels=c3_headroom,
        n_max_parcels=n_max_parcels,
    )


def compute_alpha_max(
    pax_timeline: dict,
    weight_per_unit_kg: float,
    n_freight_units_real: float,
    sample_rate: float = 1.0,
    bus_tare_kg: float = BUS_TARE_KG,
    bus_max_gvw_kg: float = BUS_MAX_GVW_KG,
    bus_freight_capacity_kg: float = BUS_FREIGHT_CAPACITY_KG,
    expected_vehicle_ids: frozenset[str] | set[str] | None = None,
) -> dict:
    """
    ENDOGENOUS feasibility envelope: the maximum load success rate alpha that the
    SIMULATED passenger loads physically allow on the H→B trips.

    For each H→B bus trip (one vehicle id = one departure; pax_timeline must be
    tracked for the H→B vehicles only, see scenario_presets.term_c_bus_ids):

        residual(t) = min(freight_capacity,
                          GVW − tare − (max_pax_real(t) + driver) × 75 kg)

    where max_pax_real = max simultaneous simulated passengers / sample_rate
    (the 10% Rotterdam sample carries 10% of real passengers). Using the trip
    MAXIMUM is conservative: the freight is on board for the whole run, so the
    binding moment is the most crowded link.

        alpha_max = min(1, Σ_t residual(t) / (N_real × w))

    This does NOT make alpha endogenous — alpha stays the exogenous experimental
    dial (the thesis sweeps it). It bounds the dial with simulation data:
    scenario cells with alpha > alpha_max are infeasible *because of passengers*,
    not just because of the static worst-case check in check_feasibility (which
    assumes a permanently full bus).
    """
    # pax_timeline only contains vehicles with at least one boarding event:
    # trips with ZERO passengers would silently disappear and UNDERCOUNT the
    # capacity. expected_vehicle_ids (the full H→B departure list) fills the
    # gap: missing vehicles ran empty → full residual capacity.
    vehicle_ids = (set(expected_vehicle_ids) if expected_vehicle_ids is not None
                   else set(pax_timeline.keys()))
    residuals = []
    for vid in vehicle_ids:
        timeline = pax_timeline.get(vid, [])
        max_pax_sim = max((c for _, c in timeline), default=0)
        pax_real = max_pax_sim / sample_rate
        headroom = bus_max_gvw_kg - bus_tare_kg - (pax_real + 1) * AVG_PERSON_WEIGHT_KG
        residuals.append(max(0.0, min(bus_freight_capacity_kg, headroom)))

    total_capacity_kg = sum(residuals)
    demand_kg = n_freight_units_real * weight_per_unit_kg
    alpha_max = min(1.0, total_capacity_kg / demand_kg) if demand_kg > 0 else 1.0

    return {
        "alpha_max": alpha_max,
        "total_residual_capacity_kg": total_capacity_kg,
        "daily_demand_kg": demand_kg,
        "n_trips_observed": len(residuals),
        "min_trip_residual_kg": min(residuals) if residuals else None,
        "mean_trip_residual_kg": (total_capacity_kg / len(residuals)) if residuals else None,
    }


def run_feasibility_matrix(
    n_freight_units: int,
    weight_regimes: dict | None = None,
    alpha_values: list | None = None,
    bus_trips_per_day: int = BUS_TRIPS_PER_DAY,
) -> list[FeasibilityResult]:
    """
    Run the feasibility check across all (alpha, weight_regime) combinations.

    Returns a list of FeasibilityResult for all 5×3 = 15 combinations
    (5 alpha values × 3 weight regimes).
    Infeasible results are included — callers must use result.feasible to filter.
    """
    if weight_regimes is None:
        weight_regimes = WEIGHT_REGIMES
    if alpha_values is None:
        alpha_values = ALPHA_VALUES

    results = []
    for regime, weight_kg in weight_regimes.items():
        for alpha in alpha_values:
            r = check_feasibility(
                alpha=alpha,
                n_freight_units=n_freight_units,
                weight_per_unit_kg=weight_kg,
                weight_regime=regime,
                bus_trips_per_day=bus_trips_per_day,
            )
            results.append(r)
    return results


def feasibility_summary(results: list[FeasibilityResult]) -> None:
    """Print a formatted feasibility table."""
    import pandas as pd

    rows = []
    for r in results:
        rows.append({
            "alpha": f"{r.alpha:.0%}",
            "weight_regime": r.weight_regime,
            "weight_kg": r.weight_per_unit_kg,
            "freight_per_trip_kg": round(r.freight_on_bus_per_trip_kg, 1),
            "parcels_per_trip": round(r.units_on_bus_per_trip, 1),
            "feasible": r.feasible,
            "binding": r.binding_constraint,
            "C1_headroom_kg": round(r.c1_headroom_kg, 1),
            "C2_headroom_kg": round(r.c2_headroom_kg, 1),
            "C3_headroom_parcels": round(r.c3_headroom_parcels, 1),
        })
    df = pd.DataFrame(rows)
    print("\nFeasibility matrix:")
    print(df.to_string(index=False))
    n_infeasible = sum(1 for r in results if not r.feasible)
    print(f"\n{n_infeasible}/{len(results)} scenarios infeasible.")


if __name__ == "__main__":
    from parameters import N_FREIGHT_UNITS_TOY

    results = run_feasibility_matrix(N_FREIGHT_UNITS_TOY)
    feasibility_summary(results)
