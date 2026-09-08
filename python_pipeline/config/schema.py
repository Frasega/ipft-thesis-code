"""What every configurable value IS: family, type, unit, and what it means.

This module holds no values. It holds the declaration of which values exist, who is
expected to set them, and what they do — the thing that was missing when the answer
to "how do I change the bus line?" was "edit scenario_presets.py".

FAMILIES. The split is not cosmetic: it is who changes a value and how often, and it
decides which file a value lives in.

    MACHINE     where this computer keeps things       once per computer
    CITY        the simulated world                    once per city
    LINE        the corridor freight rides on          once per bus line
    PHYSICS     vehicles, fuel, parcels                almost never; needs a citation
    CAMPAIGN    what to run this time                  every launch
    EXPERIMENT  which assumption to probe              per research question

PHYSICS deliberately has no YAML file of its own for its VALUES: those live in
parameters.py, which is a sourced document rather than a settings file, and they are
overridable only through config/physics_overrides.yaml where an override without a
`source:` is refused. That rule already exists in parameters.van_type() for the van
specifications; the schema generalises it rather than replacing it.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Family(str, Enum):
    MACHINE = "machine"
    CITY = "city"
    LINE = "line"
    PHYSICS = "physics"
    CAMPAIGN = "campaign"
    EXPERIMENT = "experiment"


@dataclass(frozen=True)
class Param:
    """One configurable value, described well enough to be documented automatically."""
    name: str
    family: Family
    type: str                       # str | int | float | bool | path | list[str] |
                                    # list[float] | xy | stops
    description: str                # a sentence a stranger can act on
    unit: str = ""
    required: bool = True
    default: object = None
    note_recommended: bool = False  # a bare value here loses knowledge; ask for a note
    affects: str = ""               # which pipeline stage a change invalidates
    validate: str = ""              # extra rule stated in words, checked in resolve.py

    @property
    def optional(self) -> bool:
        return not self.required


def _p(*args, **kwargs) -> Param:
    return Param(*args, **kwargs)


# -- MACHINE ------------------------------------------------------------------
MACHINE_PARAMS: tuple[Param, ...] = (
    _p("output_root", Family.MACHINE, "path",
       "Directory MATSim run outputs are written to. Multi-GB per run, so it belongs "
       "on a disk with room and OUTSIDE any cloud-synced folder. Overrides the "
       "IPFT_OUTPUT_ROOT environment variable.",
       affects="nothing - it only moves where results are stored"),
    _p("jar", Family.MACHINE, "path",
       "The compiled MATSim jar. Rebuild it after ANY change to the Java sources: "
       "the CO2 buckets and the corridor link sets are computed on the Java side.",
       default="matsim-example-project-0.0.1-SNAPSHOT.jar", required=False,
       affects="every simulation"),
    _p("heap", Family.MACHINE, "str",
       "JVM maximum heap for one MATSim run, e.g. '7g'. An undersized heap does not "
       "crash the run - it thrashes the garbage collector and the run simply gets "
       "slower and slower, so check that the ceiling reported in the run's own "
       "logfile matches what was asked for.",
       default="7g", unit="GiB", required=False,
       affects="run wall time only"),
    _p("java_home", Family.MACHINE, "path",
       "JDK to launch MATSim with. Falls back to $JAVA_HOME, then to `java` on PATH.",
       required=False, affects="nothing"),
)

# -- CITY ---------------------------------------------------------------------
CITY_PARAMS: tuple[Param, ...] = (
    _p("name", Family.CITY, "str",
       "Identifier used in generated file names and in error messages."),
    _p("scenario_dir", Family.CITY, "path",
       "Directory holding this city's MATSim inputs. Every generated config directory "
       "MUST be a direct child of it, because config generation re-prefixes the input "
       "paths with '../' - put a config anywhere else and the network, the timetable "
       "and the vehicles all fail to resolve at once.",
       affects="everything",
       validate="must exist and contain the two HBEFA factor CSVs"),
    _p("network_file", Family.CITY, "path",
       "MATSim network. Every link id in the line configuration is resolved against it.",
       affects="everything"),
    _p("base_config", Family.CITY, "path",
       "The CALIBRATED MATSim config every generated config is cloned from. Only the "
       "seed, plans file, output directory, iteration count, capacity factors, the "
       "emissions module and the frozen strategies are overwritten; everything else - "
       "scoring, activity types, routing - is inherited untouched.",
       affects="everything"),
    _p("peak_base_plans", Family.CITY, "path",
       "Population for the peak demand level, before any van is inserted.",
       affects="everything"),
    _p("offpeak_base_plans", Family.CITY, "path",
       "Population for the off-peak level. Peak and off-peak cannot share an "
       "equilibrium: the off-peak population is a subsample, so it settles into a "
       "different network state and needs its own equilibration run.",
       affects="everything"),
    _p("base_transit_schedule", Family.CITY, "path",
       "The UNMODIFIED timetable. Every dwell variant is built from it and every line "
       "derivation reads it, so it must be the same file the equilibration used.",
       affects="stages 2-5"),
    _p("emission_vehicles_file", Family.CITY, "path",
       "Vehicles file carrying the HBEFA attributes, as written into the generated "
       "config. The path is relative to the GENERATED directory, hence the leading "
       "'../'.",
       affects="every simulation"),
    _p("transit_vehicles_file", Family.CITY, "path",
       "Vehicles file of the TRANSIT module - the one carrying each transit vehicle "
       "type's capacity, length and passengerCarEquivalents (PCE). Relative to the "
       "GENERATED directory, hence the leading '../'. Not to be confused with "
       "emission_vehicles_file, which the vehicles module names and which carries "
       "the HBEFA attributes of the road fleet: they are two different files under "
       "the same parameter name in two different modules. The PCE lives here and "
       "nowhere else, so this is the only place the bus's road-space footprint can "
       "be changed - see --pt-vehicles on the scenario generators.",
       required=False, default=None,
       affects="every simulation: the PCE sets how much link storage and flow "
               "capacity a bus consumes"),
    _p("sample_rate", Family.CITY, "float",
       "Fraction of the real population the plans file represents. It sets the scale "
       "convention: van insertion and Terms A/B work at simulated scale and are "
       "multiplied by 1/sample_rate at the end, while Term C and the feasibility check "
       "use real units, because the timetable is NOT sampled - every real bus "
       "departure is simulated one to one.",
       unit="fraction", affects="every reported number",
       validate="in (0, 1]; must be consistent with the capacity factors"),
    _p("flow_capacity_factor", Family.CITY, "float",
       "MATSim qsim flowCapacityFactor. Must match the population sample, or sampled "
       "demand meets full-size road capacity and the congestion signal disappears.",
       affects="every simulation"),
    _p("storage_capacity_factor", Family.CITY, "float",
       "MATSim qsim storageCapacityFactor. May differ from the flow factor; the Java "
       "raises MATSim's consistency tolerance so that it can.",
       affects="every simulation"),
    _p("transit_prefixes", Family.CITY, "list[str]",
       "Vehicle-id prefixes that mark a vehicle as public transport. These are "
       "EXCLUDED from the Term A background, because their HBEFA values are "
       "placeholder passenger-car numbers which the bus term replaces with a "
       "longitudinal-dynamics calculation. Get this wrong on a new network and the "
       "whole transit fleet lands in the background total, inflating Term A silently.",
       note_recommended=True, affects="Term A",
       validate="must match the prefixes compiled into Co2TotalsHandler.java"),
    _p("run_id_prefix", Family.CITY, "str",
       "MATSim runId, which it prepends to every output file (e.g. 'MRDH_10pct.' -> "
       "MRDH_10pct.output_events.xml.zst). Empty when the config sets no runId. Used "
       "only to FIND output files, never to write them.",
       required=False, default="", affects="nothing - file discovery only"),
    _p("add_freight_mode", Family.CITY, "bool",
       "Whether to register a separate 'freight' network mode in the generated config. "
       "True on the sandbox, whose vans are their own mode; False on a real city "
       "config where vans ride as cars and are kept separable by their id prefix.",
       required=False, default=False, affects="every simulation"),
    _p("write_emission_events", Family.CITY, "bool",
       "Whether MATSim writes individual emission events. False on a full city: the "
       "events file would grow to roughly 8 GB per run. With it False the Java "
       "aggregates CO2 per vehicle class into co2_totals.csv, which is what Term A "
       "then reads.",
       required=False, default=True, affects="Term A's input file, and disk"),
)

# -- LINE ---------------------------------------------------------------------
LINE_PARAMS: tuple[Param, ...] = (
    _p("name", Family.LINE, "str",
       "Preset name. Must be unique: it is how a run, a surface and a config are told "
       "apart afterwards."),
    _p("city", Family.LINE, "str",
       "Which city configuration this line belongs to. All the heavy inputs - network, "
       "population, timetable, calibrated config - come from there, so two lines of one "
       "city never restate them and therefore cannot disagree about them."),
    _p("line_tag", Family.LINE, "str",
       "Short tag stamped into EVERY generated name: warm plans, configs, run "
       "directories, dwell schedules, surfaces. Empty keeps the historical unsuffixed "
       "names of the first line. Without a tag a second line silently REUSES the first "
       "line's warm plans, because a plans file that already exists is never "
       "regenerated.",
       required=False, default="", note_recommended=True,
       affects="every generated file name"),
    _p("transit_line_id", Family.LINE, "str",
       "The <transitLine id> in the timetable. Not the public route number.",
       affects="stages 2-5"),
    _p("hub_stop_link", Family.LINE, "str",
       "Link of the FIRST stop of the one-to-many direction. Route selection keys on "
       "it: it is what separates the outbound departures, which carry freight, from "
       "the return departures, which do not.",
       affects="stages 2-5"),
    _p("vehicle_ids_file", Family.LINE, "path",
       "File listing the exact vehicle ids of the freight-carrying departures, one per "
       "line, relative to the city scenario directory. Exact ids rather than a prefix: "
       "a line usually has vehicles in both directions and only one direction carries "
       "freight, so a prefix would feed the return journeys' load profile in backwards. "
       "Omit it only where there are no real vehicles to name, as in the sandbox, and "
       "then hb_route_prefixes selects them instead.",
       required=False, default=None,
       affects="Term C and the passenger-load timeline"),
    _p("vehicle_ids_expected", Family.LINE, "int",
       "How many ids that file must hold. A count checked on load is what turns a "
       "truncated or stale id file into an error instead of into a quietly smaller "
       "Term C. Required whenever vehicle_ids_file is given.",
       unit="vehicles", required=False, default=None,
       validate="must equal the number of non-empty lines in vehicle_ids_file"),
    _p("bus_trips_per_day", Family.LINE, "int",
       "F - freight-carrying departures per day, ONE direction only. It multiplies the "
       "per-trip bus cost into a daily figure, so counting both directions doubles "
       "Term C.",
       unit="trips/day", affects="Term C"),
    _p("n_pickup_stops", Family.LINE, "int",
       "Stops on the delivery segment where parcels are handed over, excluding the hub. "
       "It divides the freight mass along the route, so it must equal the length of "
       "pickup_link_ids.",
       unit="stops", affects="Term C and the van counterfactual",
       validate="must equal len(pickup_link_ids)"),
    _p("pickup_link_ids", Family.LINE, "list[str]",
       "The delivery stops as network links, IN ROUTE ORDER - the order is the datum, "
       "because the freight mass declines along it. Spacing stops evenly by link index "
       "instead of using the real ones puts phantom deliveries where the route has no "
       "stop at all. Omit only where the network has no real stop locations to use, as "
       "in the sandbox, and the stops are then spaced evenly along the link sequence.",
       required=False, default=None, note_recommended=True, affects="Term C"),
    _p("van_locker_stops", Family.LINE, "stops",
       "The same stops on the VAN side - (link, x, y) each, in order - used to force "
       "the van's route through them. This is a SUBSET of pickup_link_ids, not the same "
       "set: a bus-only platform link carries freight mass but no car can enter it, so "
       "it is a pickup stop and not a locker. Leave empty for a single direct "
       "hub-to-terminal leg.",
       required=False, default=(), note_recommended=True,
       affects="Term B (the van counterfactual)"),
    _p("n_freight_units_sim", Family.LINE, "int",
       "Parcels per day at SIMULATED scale - and so the van count dispatched when none "
       "of them ride the bus. Divide by the city sample_rate for the real daily figure.",
       unit="parcels/day", affects="everything downstream"),
    _p("hub_link", Family.LINE, "str",
       "Link the vans depart from. NOT necessarily the bus terminus: a bus platform "
       "link is often bus-and-pt-only, and a van routed from there gets stuck at the "
       "first turn. Use the nearest robust car link with enough capacity for the whole "
       "departure window.",
       note_recommended=True, affects="Term B"),
    _p("terminal_link", Family.LINE, "str",
       "Link the van tour ends at. Same caveat as hub_link.", affects="Term B"),
    _p("hub_xy", Family.LINE, "xy",
       "Coordinates of hub_link, in the network's coordinate system, used for the van's "
       "departure activity.", affects="Term B"),
    _p("terminal_xy", Family.LINE, "xy",
       "Coordinates of terminal_link, same convention.", affects="Term B"),
    _p("van_mode", Family.LINE, "str",
       "Leg mode the vans travel as. 'car' where the calibrated config routes and "
       "scores cars - a known conservative bias, since a car occupies less road space "
       "than a real van, so the vans' congestion contribution, and hence the relief "
       "from removing them, is UNDER-estimated.",
       note_recommended=True, affects="Term A and Term B"),
    _p("van_departure", Family.LINE, "str",
       "First van departure, HH:MM:SS.", unit="clock time", affects="Term B"),
    _p("van_spread_minutes", Family.LINE, "float",
       "Window the departures are spread evenly over. The grid is the BASELINE's: a van "
       "that survives a higher load rate keeps the exact departure time it had at zero, "
       "so baseline minus scenario is the missing tours and not the same tours moved "
       "elsewhere in the peak.",
       unit="minutes", note_recommended=True, affects="Term B"),
    _p("generated_dir", Family.LINE, "path",
       "Where this line's generated configs go. MUST be a direct child of the city "
       "scenario directory, and must contain this line's OWN corridor_links.txt and "
       "bus_stop_links.txt - the Java looks for the bare filename in the config's own "
       "directory FIRST and only then in the parent, so missing copies mean it silently "
       "reads the neighbouring line's corridor.",
       affects="everything", validate="direct child of city.scenario_dir"),
    _p("output_base_dir", Family.LINE, "path",
       "Run output tree for this line. May use ${machine.output_root}.",
       affects="nothing - storage location only"),
    _p("corridor_links_file", Family.LINE, "path",
       "Links the vans actually drive - the set corridor-local Term A and the speed "
       "metrics are measured on. It is derived FROM an alpha=0 run, which is why a new "
       "line costs one run before its real campaign can start.",
       required=False, default=None, affects="Term A (corridor rows)"),
    _p("bus_stop_links_file", Family.LINE, "path",
       "The bus-stop cost link set: this line's car-mode stop links plus one hop "
       "upstream, MINUS the van corridor. Van relief and bus-stop queueing are measured "
       "on DISJOINT sets and reported apart, so neither can absorb the other's sign.",
       required=False, default=None, affects="Term A (bus-stop row)"),
    _p("hb_route_prefixes", Family.LINE, "list[str]",
       "Prefix fallback used to find freight-carrying vehicles when no explicit id file "
       "is given. Unused, and should be empty, whenever vehicle_ids_file is set.",
       required=False, default=(), affects="Term C"),
)

ALL_PARAMS: tuple[Param, ...] = MACHINE_PARAMS + CITY_PARAMS + LINE_PARAMS

BY_FAMILY: dict[Family, tuple[Param, ...]] = {
    Family.MACHINE: MACHINE_PARAMS,
    Family.CITY: CITY_PARAMS,
    Family.LINE: LINE_PARAMS,
}


def params_for(family: Family) -> tuple[Param, ...]:
    return BY_FAMILY.get(family, ())


def get(family: Family, name: str) -> Param | None:
    for p in params_for(family):
        if p.name == name:
            return p
    return None


def required_names(family: Family) -> set[str]:
    return {p.name for p in params_for(family) if p.required}


def known_names(family: Family) -> set[str]:
    return {p.name for p in params_for(family)}
