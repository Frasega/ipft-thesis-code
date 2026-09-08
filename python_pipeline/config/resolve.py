"""Combine machine + city + line into the ScenarioPreset the pipeline already uses.

This is the join that did not exist before. `ScenarioPreset` mixed two lifetimes in
one object: the CITY (network, population, timetable, calibrated config, capacity
factors, sample rate) and the LINE (which corridor freight rides on). Because they
were one object, the second line's preset re-copied eight city fields verbatim from
the first, and a third would copy them again - at which point one mistyped character
in one of those copies is invisible.

Here the city is declared once and every line of that city points at it. Nothing is
duplicated, so nothing can disagree.

The output is deliberately the SAME frozen dataclass the rest of the pipeline already
consumes, not a new type. Every module keeps working untouched, and the conversion is
provable: tests/snapshot_presets.py --check compares the rebuilt presets field by
field against the ones Python declared before the move.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from scenario_presets import ScenarioPreset

from . import loader
from .loader import ConfigError, Document
from .schema import Family

_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = _ROOT / "config"


@dataclass(frozen=True)
class ResolvedScenario:
    """A ScenarioPreset plus the documents it was built from.

    The documents are kept, not discarded, because they carry the notes and the
    provenance of every value - which is what `describe` prints, what the HTML report's
    glossary is built from, and what goes into the run manifest.
    """
    preset: ScenarioPreset
    machine: Document
    city: Document
    line: Document

    @property
    def name(self) -> str:
        return self.preset.name


def _load_vehicle_ids(scenario_dir: Path, filename: str, expected: int | None,
                      line_name: str) -> frozenset[str]:
    """The exact freight-carrying vehicle ids, refusing a file of the wrong size.

    The count check is the point. A truncated or stale id file does not raise anywhere
    downstream: it just makes Term C smaller, which looks like a result.
    """
    path = scenario_dir / filename
    if not path.exists():
        raise ConfigError(
            f"line {line_name!r}: vehicle_ids_file {path} does not exist.\n"
            f"  Produce it with:  python python_pipeline/derive_line.py <line_id> <hub_link>")
    ids = frozenset(line.strip()
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip())
    if expected is None:
        raise ConfigError(
            f"line {line_name!r}: vehicle_ids_file is set but vehicle_ids_expected is "
            f"not. The count is what turns a stale id file into an error instead of a "
            f"quietly smaller Term C - {path} currently holds {len(ids)} ids.")
    if len(ids) != expected:
        raise ConfigError(
            f"line {line_name!r}: {path} holds {len(ids)} vehicle ids but "
            f"vehicle_ids_expected says {expected}. Either the file is stale (rerun "
            f"derive_line.py) or the expected count is wrong.")
    return ids


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise ConfigError(message)


def validate(preset: ScenarioPreset, city: Document, line: Document) -> None:
    """The rules that used to live only in comments, or nowhere.

    Each of these is a real invariant of the code that a new line breaks silently, so
    each is checked here - in the first second, against the configuration - rather than
    discovered ten minutes into a run or, worse, not discovered at all.
    """
    name = preset.name

    _check(0 < preset.sample_rate <= 1,
           f"city {city.value('name')!r}: sample_rate must be in (0, 1], got "
           f"{preset.sample_rate}")

    if preset.pickup_link_ids is not None:
        _check(len(preset.pickup_link_ids) == preset.n_pickup_stops,
               f"line {name!r}: n_pickup_stops is {preset.n_pickup_stops} but "
               f"pickup_link_ids lists {len(preset.pickup_link_ids)}. The freight mass "
               f"is divided by n_pickup_stops and dropped at those links, so the two "
               f"must agree.")

    # A locker is a stop a VAN can reach. Every locker must therefore also be a bus
    # stop; the reverse does not hold, because a bus-only platform link carries freight
    # mass but admits no car.
    if preset.van_locker_stops and preset.pickup_link_ids:
        pickup = set(preset.pickup_link_ids)
        stray = [link for link, _, _ in preset.van_locker_stops if link not in pickup]
        _check(not stray,
               f"line {name!r}: van_locker_stops contains {stray}, which are not in "
               f"pickup_link_ids. A locker sits at a stop the bus actually serves, so "
               f"every locker link must also be a pickup link.")

    scenario_dir = Path(city.value("scenario_dir"))
    generated = Path(preset.generated_dir)
    _check(generated.parent == scenario_dir,
           f"line {name!r}: generated_dir is {generated}, whose parent is "
           f"{generated.parent}, but the city scenario_dir is {scenario_dir}. Config "
           f"generation re-prefixes every input path with '../', so a generated "
           f"directory that is not a direct child of the scenario directory breaks the "
           f"network, the timetable and the vehicles at once.")

    for label, rel in (("network_file", preset.network_file),
                       ("base_config", preset.base_config),
                       ("base_transit_schedule", preset.base_transit_schedule),
                       ("peak_base_plans", preset.peak_base_plans)):
        _check((_ROOT / rel).exists(),
               f"city {city.value('name')!r}: {label} points at {rel}, which does not "
               f"exist relative to the project root.")

    # The Java resolves a bare link-set filename in the config's own directory FIRST
    # and only then in the parent. So the copy that WINS for a given line depends on
    # where it sits, and a line whose generated directory lacks its own copy silently
    # reads the NEIGHBOURING line's corridor, writes plausible numbers, and says
    # nothing. The rule that follows from that:
    #
    #   untagged line (line_tag empty)  the scenario-directory copy IS its own, so it
    #                                   may live either there or in generated_dir;
    #   tagged line                     it MUST have its own copy in generated_dir,
    #                                   or the parent fallback hands it the untagged
    #                                   line's corridor.
    for label, declared in (("corridor_links_file", preset.corridor_links_file),
                            ("bus_stop_links_file", preset.bus_stop_links_file)):
        if declared is None:
            continue
        path = Path(declared)
        _check(path.name in ("corridor_links.txt", "bus_stop_links.txt"),
               f"line {name!r}: {label} is named {path.name!r}, but the Java looks for "
               f"the literal names corridor_links.txt and bus_stop_links.txt and will "
               f"never see this file.")
        allowed = {generated} if preset.line_tag else {generated, scenario_dir}
        if path.parent not in allowed:
            raise ConfigError(
                f"line {name!r}: {label} is {path}, which is not "
                f"{' or '.join(str(p) for p in sorted(allowed, key=str))}. The Java "
                f"tries the bare filename in the config's own directory first and the "
                f"parent second, so this line's runs would read whatever link set "
                f"happens to sit in {scenario_dir} - another line's corridor, with no "
                f"error anywhere.")
        if preset.line_tag and not path.exists():
            raise ConfigError(
                f"line {name!r}: {label} {path} does not exist. Because this line is "
                f"tagged {preset.line_tag!r}, the Java would fall back to "
                f"{scenario_dir / path.name} - the untagged line's link set - and "
                f"report its corridor as this line's. Generate this line's own copy "
                f"before running.")


def build_preset(machine: Document, city: Document, line: Document) -> ScenarioPreset:
    """City fields plus line fields, in exactly the shape the pipeline expects."""
    scenario_dir = Path(city.value("scenario_dir"))

    ids_file = line.value("vehicle_ids_file")
    term_c_bus_ids = (
        _load_vehicle_ids(scenario_dir, ids_file, line.value("vehicle_ids_expected"),
                          str(line.value("name")))
        if ids_file else None)

    return ScenarioPreset(
        name=line.value("name"),
        # -- vehicle classification: the city names its transit fleet, the line names
        #    the individual vehicles that carry freight.
        transit_prefixes=city.value("transit_prefixes"),
        term_c_bus_ids=term_c_bus_ids,
        hb_route_prefixes=line.value("hb_route_prefixes") or (),
        # -- service / demand
        bus_trips_per_day=line.value("bus_trips_per_day"),
        n_pickup_stops=line.value("n_pickup_stops"),
        n_freight_units_sim=line.value("n_freight_units_sim"),
        sample_rate=city.value("sample_rate"),
        # -- van insertion
        hub_link=line.value("hub_link"),
        terminal_link=line.value("terminal_link"),
        hub_xy=line.value("hub_xy"),
        terminal_xy=line.value("terminal_xy"),
        van_mode=line.value("van_mode"),
        van_departure=line.value("van_departure"),
        van_spread_minutes=line.value("van_spread_minutes"),
        # -- files / dirs
        base_config=city.value("base_config"),
        peak_base_plans=city.value("peak_base_plans"),
        offpeak_base_plans=city.value("offpeak_base_plans"),
        generated_dir=line.value("generated_dir"),
        # str(Path(...)) reproduces exactly how this string used to be built - as
        # OUTPUT_ROOT / "<name>_runs" - which on Windows means native separators. The
        # preset snapshot compares these as strings, so "D:/x" and "D:\\x" are a
        # difference even though they name the same directory.
        output_base_dir=str(Path(line.value("output_base_dir"))),
        network_file=city.value("network_file"),
        base_transit_schedule=city.value("base_transit_schedule"),
        # -- MATSim config patching
        flow_capacity_factor=city.value("flow_capacity_factor"),
        storage_capacity_factor=city.value("storage_capacity_factor"),
        emission_vehicles_file=city.value("emission_vehicles_file"),
        transit_vehicles_file=city.value("transit_vehicles_file"),
        add_freight_mode=city.value("add_freight_mode"),
        write_emission_events=city.value("write_emission_events"),
        corridor_links_file=line.value("corridor_links_file"),
        bus_stop_links_file=line.value("bus_stop_links_file"),
        van_locker_stops=line.value("van_locker_stops") or (),
        pickup_link_ids=line.value("pickup_link_ids"),
        # -- which transit line this preset is about
        transit_line_id=line.value("transit_line_id"),
        hub_stop_link=line.value("hub_stop_link"),
        line_tag=line.value("line_tag") or "",
    )


def available_lines() -> dict[str, Path]:
    """Every line configuration found, keyed by BOTH its file stem and its `name:`.

    Up to three keys per file, on purpose. The file is named after the bus line a
    person would look for (rotterdam_line44.yaml); the `name:` inside it is the
    historical preset identifier that every existing run directory, config and CSV
    already carries ('rotterdam'); and `line_tag` is the short handle the generated
    filenames use ('L87'). All three were accepted before this move and all three keep
    working — derived from the files themselves rather than listed in an alias table
    that would go stale the first time a line was added.
    """
    found: dict[str, Path] = {}
    lines_dir = CONFIG_DIR / "lines"
    if not lines_dir.is_dir():
        return found
    for path in sorted(lines_dir.glob("*.y*ml")):
        found.setdefault(path.stem, path)
        try:
            import yaml
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001 - a broken file must not hide the good ones
            continue
        for key in ("name", "line_tag"):
            raw = data.get(key)
            declared = raw.get("value") if isinstance(raw, dict) else raw
            if isinstance(declared, str) and declared:
                found.setdefault(declared, path)
    return found


def line_config_path(name: str) -> Path:
    """Accept a path, a file stem, or a preset name; say what exists when none hits."""
    candidate = Path(name)
    if candidate.suffix in (".yaml", ".yml") and candidate.exists():
        return candidate
    found = available_lines()
    if name in found:
        return found[name]
    raise ConfigError(
        f"no line configuration called {name!r}.\n"
        f"  looked in {CONFIG_DIR / 'lines'}\n"
        f"  available: {', '.join(sorted(found)) if found else '(none)'}\n"
        f"  add one with:  python python_pipeline/derive_line.py --emit")


def load_scenario(name: str, machine_path: str | Path | None = None,
                  skip_validation: bool = False) -> ResolvedScenario:
    """The whole join: a line name (or path) in, a validated ResolvedScenario out."""
    machine = loader.load_machine(machine_path)
    line_path = line_config_path(name)
    line = loader.load_document(line_path, Family.LINE, {"machine": machine})

    city_name = line.value("city")
    city_path = CONFIG_DIR / "cities" / f"{city_name}.yaml"
    if not city_path.exists():
        available = sorted(p.stem for p in (CONFIG_DIR / "cities").glob("*.y*ml")) \
            if (CONFIG_DIR / "cities").is_dir() else []
        raise ConfigError(
            f"line {line_path.name} names city {city_name!r}, but "
            f"{city_path} does not exist. Available: "
            f"{', '.join(available) if available else '(none)'}")
    city = loader.load_document(city_path, Family.CITY, {"machine": machine})

    # Reloaded with the city in scope so a line may interpolate ${city.scenario_dir}
    # in its own paths instead of repeating the directory in every entry.
    line = loader.load_document(line_path, Family.LINE,
                                {"machine": machine, "city": city})

    preset = build_preset(machine, city, line)
    if not skip_validation:
        validate(preset, city, line)
    return ResolvedScenario(preset=preset, machine=machine, city=city, line=line)
