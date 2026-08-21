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
  - term_c_bus_ids: ONLY the 98 one-to-many line-44 vehicles (the line has 197
    in total; the 99 return departures carry no freight), used for Term C and the
    passenger-load timeline (exact ids loaded from line44_vehicle_ids.txt,
    extracted from transitLine 99437 in the schedule).
"""

from __future__ import annotations

import os
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
# Overridable without editing the code: set IPFT_OUTPUT_ROOT to run the pipeline
# on a machine without a D: drive.
OUTPUT_ROOT = Path(os.environ.get("IPFT_OUTPUT_ROOT", "D:/TesiOutputs"))


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
    base_transit_schedule: str             # the UNMODIFIED schedule every dwell
                                           # variant is built from, and the one the
                                           # line/route derivations read. It was a
                                           # literal filename in four modules.

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
    van_locker_stops: tuple[tuple[str, float, float], ...] = ()  # insert_vans:
                                            # (link, x, y) of every locker the van
                                            # serves, in order. Empty = one direct
                                            # hub->terminal leg (toy). NOT the same
                                            # set as pickup_link_ids: the bus also
                                            # serves the bus-only terminus platform,
                                            # which no car can enter.
    pickup_link_ids: tuple[str, ...] | None = None  # Term C: the links where the
                                            # bus actually hands parcels over, in
                                            # route order. None = space them evenly
                                            # along the link sequence (toy: there
                                            # are no real stop locations to use).

    # ── which transit line this preset is about (2026-08-19) ─────────────
    # Until the second line these three were module constants in
    # make_dwell_schedules.py, which is exactly the hardcoding that stopped
    # anyone else from running the pipeline on a corridor of their own.
    transit_line_id: str = ""              # the <transitLine id>, e.g. "99437"
    hub_stop_link: str = ""                # link of the FIRST stop of the
                                           # one-to-many direction; the route
                                           # selection keys on it
    # line_tag goes into every generated filename: warm plans, configs, run
    # directories, dwell schedules, surfaces. Empty keeps the historical names,
    # so line 44 is untouched. Without it a second line silently REUSES the
    # first line's warm plans, because make_rotterdam_warm_scenarios only
    # regenerates a plans file when it does not already exist.
    line_tag: str = ""

    @property
    def suffix(self) -> str:
        """'' for line 44, '_L87' for the second line — appended to filenames."""
        return f"_{self.line_tag}" if self.line_tag else ""

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
    base_transit_schedule="scenarios/ipft_toy/reduced_transitSchedule_5min_headway.xml",
    flow_capacity_factor=1.0,
    storage_capacity_factor=1.0,
    emission_vehicles_file="../emission_vehicles.xml",
    add_freight_mode=True,
)


def _rotterdam() -> ScenarioPreset:
    return ScenarioPreset(
        name="rotterdam",
        # Line 44 described HERE and nowhere else: these two were module constants
        # in make_dwell_schedules, so the second line had to inherit or override
        # them by accident. Same values, now in the same place as line 87's.
        transit_line_id="99437",                  # RET bus 44
        hub_stop_link="174131",                   # Centraal perron BB, first H->B stop
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
        # The 8 delivery stops in route order, from transitLine 99437 (the 93-link
        # H->B variant, 98 departures). Spacing them evenly by link index instead
        # dropped a phantom delivery mid-way across the Maas, where the route has
        # 2.6 km with no stop at all, and under-charged the freight mass by ~8%.
        # NOTE 121963 (Zuidplein Hoog, the terminus) is the LAST link of the route,
        # so it never appears in an event-derived link sequence — the divisor stays
        # n_pickup_stops, see dynamic_mass.build_freight_remaining.
        pickup_link_ids=("448306", "668963", "448173", "438699",
                         "178358", "390546", "49628", "121963"),
        # The same stops on the van side, minus the bus-only terminus: all seven
        # are bus,car,pt links, so a car-mode van can enter them. Coordinates are
        # the links' to-node (networkWithRideAndBike.xml.gz). An activity on each
        # forces the router through them — without this the van took the fastest
        # car road and touched NONE of the stops the bus serves.
        van_locker_stops=(("448306", 91409.1, 437465.0),
                          ("668963", 91432.1, 437135.3),
                          ("448173", 91471.0, 436243.8),
                          ("438699", 92015.2, 433846.2),
                          ("178358", 91974.3, 433544.4),
                          ("390546", 92101.6, 433426.3),
                          ("49628",  92665.7, 433474.5)),
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
        base_transit_schedule="scenarios/ipft_rotterdam/ptSchedule36Hour.xml.gz",
        flow_capacity_factor=0.1,
        storage_capacity_factor=0.1,
        emission_vehicles_file="../emission_vehicles_rotterdam.xml",
        add_freight_mode=False,
        write_emission_events=False,
        corridor_links_file="scenarios/ipft_rotterdam/corridor_links.txt",
        bus_stop_links_file="scenarios/ipft_rotterdam/bus_stop_links.txt",
    )


def _rotterdam_l87() -> ScenarioPreset:
    """Second corridor: RET line 87, Spijkenisse Medocgaard -> Hofweg.

    Chosen on 2026-08-19 by screening every bus direction in the network (163
    candidates with at least 4 km, 6 stops and 30 daily departures) on three
    criteria: whether the service is healthy in the simulation, whether the
    catchment is large enough, and whether the parcel load stays feasible. 68 of
    the 163 pass. The first candidate, 99470 Zuidplein->Ridderkerk, was dropped
    because only 20 of its 53 delivery trips reach the end of the route: the
    route crosses the Zuidplein deadlock (see make_deadlock_links.py). Line 87
    completes 63 of 63.

    Every value below comes from derive_line.py 94298 564425, not from hand
    counting. Contrast with line 44: 63 departures against 98, 9.58 km against
    6.39, 16 delivery stops against 8, median route capacity 600 veh/h against
    3,000, catchment 2,966 in the 10% sample against 3,759. The two lines carry a
    comparable total freight dwell per day, 28,224 s against 31,360 — within 10%
    — so the bus-side cost is roughly held while everything else varies.

    Feasibility at alpha=1: 58.8 parcels/trip against the 70-parcel space cap
    (margin 11); light 177 kg and medium 589 kg against the 1,000 kg allowance,
    but heavy 1,471 kg — so heavy is infeasible above alpha ~ 0.68 against 0.83
    on line 44. What binds is service frequency, not demand.

    DIRECTORY LAYOUT, and it is not cosmetic. The generated configs live in
    scenarios/ipft_rotterdam/generated_L87/ so that the '../' re-prefixing in
    generate_configs.patch_config still resolves the network, schedule and
    vehicles files in scenarios/ipft_rotterdam/. The corridor and bus-stop link
    files, however, live INSIDE generated_L87/, because Co2TotalsHandler looks
    for the bare filename in the config's own directory first and only then in
    the parent — so the L87 copies win, and line 44's files are never read by an
    L87 run. Verify on the log line 'Co2TotalsHandler: link set loaded from ...'.
    """
    return ScenarioPreset(
        name="rotterdam_L87",
        line_tag="L87",
        transit_line_id="94298",
        hub_stop_link="564425",                    # Medocgaard, first stop
        transit_prefixes=("veh_",),
        term_c_bus_ids=_load_id_file("line94298_564425_vehicle_ids.txt", 63,
                                     "../../python_pipeline/derive_line.py 94298 564425"),
        hb_route_prefixes=(),
        bus_trips_per_day=63,                      # all outbound route variants
        n_pickup_stops=16,                         # 17 common stops minus the hub
        # THE DELIVERY SEGMENT IS THE COMMON PREFIX, and this is a real property of
        # the line, not a simplification. The 63 outbound departures split into two
        # branches after stop 17: 40 of them run via Halfweg 2 and Laanweg (both
        # bus,pt-only) to Hofweg 343629, and 23 via Kelvinweg and Wattweg to Hofweg
        # 343628. The first 17 stops are identical in every departure. A locker can
        # only sit where every bus stops, so the delivery segment ends at stop 17
        # and the branch is outside it. Dropping those stops costs almost nothing in
        # demand — the catchment goes from 2,968 to 2,966 in the 10% sample, because
        # the branch runs through an industrial estate with no homes.
        #
        # Two of the 16 (360277 Hekelingseweg, 489287 Metro Centrum) are bus,pt-only
        # platform links: the bus serves them, so they carry freight mass, but no van
        # can enter and no locker can be placed. They are therefore in
        # pickup_link_ids and NOT in van_locker_stops — the same asymmetry as line
        # 44's terminus, and the reason the freight-mass divisor is the number of
        # BUS stops rather than the number of lockers.
        pickup_link_ids=("386421", "124553", "575533", "565674", "612221", "562034",
                         "360304", "360277", "489287", "541196", "543792", "222093",
                         "717829", "593358", "592823", "616176"),
        van_locker_stops=(("386421", 82429.2, 427600.4),
                          ("124553", 82507.5, 427740.1),
                          ("575533", 82847.8, 427525.7),
                          ("565674", 83141.4, 427626.2),
                          ("612221", 83181.7, 428136.3),
                          ("562034", 82778.7, 428701.7),
                          ("360304", 82380.1, 428676.7),
                          ("541196", 82060.2, 429313.6),
                          ("543792", 81825.2, 429556.7),
                          ("222093", 81820.8, 430147.2),
                          ("717829", 81638.4, 430420.2),
                          ("593358", 81243.7, 430294.6),
                          ("592823", 80923.6, 430257.3),
                          ("616176", 80577.0, 430075.8)),
        n_freight_units_sim=371,                   # catchment 2,968 -> 3,710 real
        sample_rate=0.10,
        # Unlike line 44, both termini are already car-accessible: 564425 is
        # bus,car and 343628 is bus,car,pt, so no nearby-car-link substitution is
        # needed. Still to be confirmed by the routing smoke run.
        hub_link="564425",
        terminal_link="616176",                    # last common stop, where the
                                                   # delivery segment ends
        hub_xy=(82212.0, 427078.0),                # link midpoints, EPSG:28992
        terminal_xy=(80610.3, 430099.7),
        van_mode="car",
        van_departure="07:00:00",
        van_spread_minutes=120.0,
        base_config="scenarios/ipft_rotterdam/config.xml",
        peak_base_plans="scenarios/ipft_rotterdam/planExternalProcessed_lowerCase.xml.gz",
        offpeak_base_plans="scenarios/ipft_rotterdam/plans_offpeak_base.xml.gz",
        generated_dir="scenarios/ipft_rotterdam/generated_L87",
        output_base_dir=str(OUTPUT_ROOT / "ipft_rotterdam_L87_runs"),
        network_file="scenarios/ipft_rotterdam/networkWithRideAndBike.xml.gz",
        base_transit_schedule="scenarios/ipft_rotterdam/ptSchedule36Hour.xml.gz",
        flow_capacity_factor=0.1,
        storage_capacity_factor=0.1,
        emission_vehicles_file="../emission_vehicles_rotterdam.xml",
        add_freight_mode=False,
        write_emission_events=False,
        corridor_links_file="scenarios/ipft_rotterdam/generated_L87/corridor_links.txt",
        bus_stop_links_file="scenarios/ipft_rotterdam/generated_L87/bus_stop_links.txt",
    )


# ── Layer-3 sensitivity knobs: line 44 only (2026-08-20) ──────────────────
# The four knobs — van delivery-stop idle, van load factor, kinematic
# reconstruction seed, per-parcel handling dwell — are BRACKETS built on the
# line-44 corridor: the idle bounds from its tour structure, the load factor
# from its locker spacing, and every sweep reported in the thesis is that
# corridor. On another scenario they would produce a table that LOOKS like a
# sensitivity while resting on nothing, so they are refused there.
# force=True (--force-sensitivity) means the caller wants them anyway and
# accepts reporting them as unvalidated for that corridor.
SENSITIVITY_SCENARIO = "rotterdam"      # the line-44 preset

SENSITIVITY_KNOBS = ("van_stop_idle", "van_load", "recon_seed", "extra_dwell_s")


def check_sensitivity_allowed(scenario_name: str,
                              requested: dict,
                              force: bool = False) -> None:
    """Raise unless the layer-3 knobs may be applied to this scenario.

    `requested` holds ONLY the knobs the caller moved away from the headline
    value ({} when none did), so the default path never trips the gate and the
    headline numbers are reproduced by every scenario without extra flags.
    """
    if not requested or scenario_name == SENSITIVITY_SCENARIO:
        return
    asked = ", ".join(f"{k}={v!r}" for k, v in sorted(requested.items()))
    if force:
        print(f"[warn] sensitivity knobs ({asked}) applied to scenario "
              f"{scenario_name!r}, which is not line 44: the brackets behind "
              f"them were never validated on this corridor. --force-sensitivity "
              f"given, applying them anyway — report them as unvalidated.")
        return
    raise ValueError(
        f"sensitivity knobs ({asked}) are supported only on the line-44 "
        f"scenario {SENSITIVITY_SCENARIO!r}, not on {scenario_name!r}. The "
        f"brackets they sweep were built on the line-44 corridor and no other "
        f"corridor has been validated against them. Drop the knobs to run the "
        f"headline configuration, or pass --force-sensitivity to apply them "
        f"anyway.")


def get_preset(name: str) -> ScenarioPreset:
    if name == "toy":
        return TOY
    if name == "rotterdam":
        return _rotterdam()
    if name in ("rotterdam_L87", "L87"):
        return _rotterdam_l87()
    raise ValueError(f"Unknown scenario preset: {name!r} "
                     f"(expected 'toy', 'rotterdam' or 'rotterdam_L87')")
