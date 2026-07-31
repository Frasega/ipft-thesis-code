"""
Scenario presets — toy vs Rotterdam.

Centralises every scenario-specific value so that the pipeline modules stay
scenario-agnostic. The toy values reproduce the historical behaviour exactly
(same constants as parameters.py); the Rotterdam values come from the
2026-06-10 inspection of the XCARCITY files (see scenarios/ipft_rotterdam/
check_*.py and gather_facts.py / derive_n_freight.py):

  - transit line 99437 = RET bus 44, Rotterdam Centraal (perron BB) -> Zuidplein Hoog
  - 98 H->B departures/day (06:05-24:01); the 99 return departures carry no freight
  - hub link 174131 (Centraal perron BB), terminal link 121963 (Zuidplein Hoog)
  - 9 stops in the H->B direction -> 8 delivery stops (hub excluded)
  - plans = 10% ActivitySim sample, 256,447 persons; flow/storageCapacityFactor 0.1
  - N freight units: line-44 catchment = persons with home within 400 m of an
    H->B stop (3,759 in the 10% sample -> 37,590 real) x 0.125 parcels/person/day
    (NL e-commerce range 0.10-0.15) -> 4,699 ~ 4,700 real units/day.
    Simulated vans = N_real x sample_rate = 470 (consistent with capacity factors).

Term A vs Term C vehicle filtering (split deliberately):
  - transit_prefixes: ALL transit vehicles, excluded from Term A background
    (every Rotterdam transit vehicle id starts with "veh_").
  - term_c_bus_ids: ONLY the 197 line-44 vehicles, used for Term C and the
    passenger-load timeline (exact ids loaded from line44_vehicle_ids.txt,
    extracted from transitLine 99437 in the schedule).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from parameters import BUS_ID_PREFIXES, BUS_TRIPS_PER_DAY, N_FREIGHT_UNITS_TOY

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
ROTTERDAM_SCENARIO_DIR = _PROJECT_ROOT / "scenarios" / "ipft_rotterdam"
TOY_SCENARIO_DIR = _PROJECT_ROOT / "scenarios" / "ipft_toy"

# MATSim outputs live on D: (2026-06-12): C: was nearly full (the 20-run
# Rotterdam batch needs ~25-40 GB) and keeping multi-GB event files out of the
# OneDrive-synced tree avoids pointless cloud uploads. The toy runs were moved
# to D:\TesiOutputs\ipft_toy_runs (verified copy of output/ipft_toy_runs).
OUTPUT_ROOT = Path("D:/TesiOutputs")


@dataclass(frozen=True)
class ScenarioPreset:
    name: str

    # ── vehicle classification ────────────────────────────────────────────
    transit_prefixes: tuple[str, ...]      # excluded from Term A background
    term_c_bus_ids: frozenset[str] | None  # exact ids for Term C (None = use prefixes)
    hb_route_prefixes: tuple[str, ...]     # prefix fallback for Term C candidates

    # ── service / demand ──────────────────────────────────────────────────
    bus_trips_per_day: int                 # F, H->B direction only
    n_pickup_stops: int
    n_freight_units_sim: int               # vans inserted at alpha=0 (simulated scale)
    sample_rate: float                     # population sampling rate (1.0 = full)

    # ── van insertion ─────────────────────────────────────────────────────
    hub_link: str
    terminal_link: str
    hub_xy: tuple[float, float]
    terminal_xy: tuple[float, float]
    van_mode: str                          # leg mode for backup vans
    van_departure: str                     # first departure (HH:MM:SS)
    van_spread_minutes: float              # departures spread evenly over this window

    # ── files / dirs (relative to project root unless absolute) ──────────
    base_config: str
    peak_base_plans: str
    offpeak_base_plans: str
    generated_dir: str
    output_base_dir: str
    network_file: str

    # ── MATSim config patching ────────────────────────────────────────────
    flow_capacity_factor: float
    storage_capacity_factor: float
    emission_vehicles_file: str            # vehicles-module file (mode vehicle types)
    add_freight_mode: bool                 # toy: freight networkMode/mainMode/modeParams
    write_emission_events: bool = True     # False: CO2 aggregated by the Java
                                           # Co2TotalsHandler into co2_totals.csv,
                                           # emission events NOT written (the
                                           # Rotterdam events file would explode
                                           # to ~8 GB/run otherwise)
    corridor_links_file: str | None = None  # link set for corridor-local Term A
                                            # and speed metrics (None = disabled)
    bus_stop_links_file: str | None = None  # dwell-in-MATSim: the bus-stop cost
                                            # link set (7 car-mode line-44 stop
                                            # links + 1-hop upstream minus the
                                            # van corridor), written by
                                            # make_bus_stop_links.py. Van relief
                                            # and bus-stop queueing are measured
                                            # on DISJOINT sets, reported apart.

    @property
    def n_freight_units_real(self) -> float:
        """Real-world daily freight units (Term C / feasibility scale)."""
        return self.n_freight_units_sim / self.sample_rate


def _load_id_file(filename: str, expected: int, generator: str) -> frozenset[str]:
    path = ROTTERDAM_SCENARIO_DIR / filename
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found - run scenarios/ipft_rotterdam/{generator} first."
        )
    ids = frozenset(line.strip() for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip())
    if len(ids) != expected:
        raise ValueError(f"Expected {expected} ids, found {len(ids)} in {path}")
    return ids


TOY = ScenarioPreset(
    name="toy",
    transit_prefixes=BUS_ID_PREFIXES,            # ("EW_lower_", "EW_upper_", "NS_")
    term_c_bus_ids=None,                          # prefix-based (historical behaviour)
    hb_route_prefixes=("EW_",),
    bus_trips_per_day=BUS_TRIPS_PER_DAY,          # 60
    n_pickup_stops=5,
    n_freight_units_sim=N_FREIGHT_UNITS_TOY,      # 2000
    sample_rate=1.0,
    hub_link="H_5_0_H_5_1",
    terminal_link="B_5_14_B_5_15",
    hub_xy=(100.0, 1000.0),
    terminal_xy=(10400.0, 1000.0),
    van_mode="freight",
    van_departure="07:00:00",
    van_spread_minutes=0.0,
    base_config="scenarios/ipft_toy/config.xml",
    peak_base_plans="scenarios/ipft_toy/reduced_plans.xml",
    offpeak_base_plans="scenarios/ipft_toy/reduced_plans_offpeak_base.xml",
    generated_dir="scenarios/ipft_toy/generated",
    output_base_dir=str(OUTPUT_ROOT / "ipft_toy_runs"),
    network_file="scenarios/ipft_toy/reduced_network.xml",
    flow_capacity_factor=1.0,
    storage_capacity_factor=1.0,
    emission_vehicles_file="../emission_vehicles.xml",
    add_freight_mode=True,
)


def _rotterdam() -> ScenarioPreset:
    return ScenarioPreset(
        name="rotterdam",
        transit_prefixes=("veh_",),               # ALL transit vehicles (Term A exclusion)
        # ONLY the 98 H->B departures (Centraal->Zuidplein): the return direction
        # carries no freight, and its link sequence would put the load profile
        # backwards. Extracted by extract_corridor_data.py (the full 197-vehicle
        # list lives in line44_vehicle_ids.txt for reference).
        term_c_bus_ids=_load_id_file("line44_hb_vehicle_ids.txt", 98,
                                     "extract_corridor_data.py"),
        hb_route_prefixes=(),                      # unused when term_c_bus_ids given
        bus_trips_per_day=98,                      # H->B departures only (NOT 197)
        n_pickup_stops=8,                          # 9 H->B stops minus the hub
        n_freight_units_sim=470,                   # see derive_n_freight.py
        sample_rate=0.10,
        # Van depot links — NOT the bus stop links! The line-44 termini
        # (174131 "Centraal perron BB", 121963 "Zuidplein Hoog") are bus,pt-only
        # platform links: vans routed from there get stuck at the first turn
        # (verified in the 2026-06-10 smoke run). These are the nearest robust
        # car links (find_van_links.py): hub 129 m from perron BB (capacity
        # 2000/h, enough for 235 sim-vans over the 07:00-09:00 window at the
        # 0.1 flow factor), terminal 79 m from Zuidplein Hoog.
        hub_link="668917",
        terminal_link="717345",
        hub_xy=(91799.1, 437534.1),                # link midpoints, EPSG:28992
        terminal_xy=(93170.4, 433519.5),
        van_mode="car",                            # car is routed+scored in this config;
                                                   # backup_van_* id prefix keeps vans
                                                   # separable in every filter.
                                                   # KNOWN CONSERVATIVE BIAS: mode car
                                                   # means PCE 1.0 instead of ~1.5 (LCV),
                                                   # vans occupy less road space than a
                                                   # real van -> their congestion impact
                                                   # is underestimated -> Term A under-
                                                   # estimated -> IPFT looks LESS good.
                                                   # Document in thesis Ch4 limitations.
        van_departure="07:00:00",
        van_spread_minutes=120.0,                  # depot departures 07:00-09:00
        base_config="scenarios/ipft_rotterdam/config.xml",
        peak_base_plans="scenarios/ipft_rotterdam/planExternalProcessed_lowerCase.xml.gz",
        offpeak_base_plans="scenarios/ipft_rotterdam/plans_offpeak_base.xml.gz",
        generated_dir="scenarios/ipft_rotterdam/generated",
        output_base_dir=str(OUTPUT_ROOT / "ipft_rotterdam_runs"),
        network_file="scenarios/ipft_rotterdam/networkWithRideAndBike.xml.gz",
        flow_capacity_factor=0.1,
        storage_capacity_factor=0.1,
        emission_vehicles_file="../emission_vehicles_rotterdam.xml",
        add_freight_mode=False,
        write_emission_events=False,
        corridor_links_file="scenarios/ipft_rotterdam/corridor_links.txt",
        bus_stop_links_file="scenarios/ipft_rotterdam/bus_stop_links.txt",
    )


def get_preset(name: str) -> ScenarioPreset:
    if name == "toy":
        return TOY
    if name == "rotterdam":
        return _rotterdam()
    raise ValueError(f"Unknown scenario preset: {name!r} (expected 'toy' or 'rotterdam')")
