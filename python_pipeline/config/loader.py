"""Read a config YAML file into validated, described entries.

Every entry may be written in one of two ways, and both mean the same thing:

    bus_trips_per_day: 98                      # bare, when the value speaks for itself

    bus_trips_per_day:                         # with the reasoning attached
      value: 98
      note: >
        Outbound departures only, 06:05-24:01. The line has 197 vehicles in total;
        the 99 return departures carry no freight, so counting them would double
        Term C.

The `note:` form exists because the values being moved out of Python are carried
there by comments that exist NOWHERE else - why 16 stops and not 19, why two links
are pickup stops but not lockers, why the generated directory sits where it does. A
bare YAML would silently delete that knowledge, which would make the refactor a net
loss even if every number stayed the same. `source:` is the same idea for a value
that needs a citation rather than an explanation.

INTERPOLATION. A string value may reference something declared elsewhere:

    output_base_dir: ${machine.output_root}/ipft_rotterdam_runs
    bus_trips_per_day: ${parameters.BUS_TRIPS_PER_DAY}

Available scopes are `machine`, `parameters` (any module-level constant of
parameters.py) and `city` (when loading a line). Referencing parameters.py rather
than copying its number is what keeps the sandbox's F, N and vehicle prefixes from
drifting away from the constants file that documents them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from . import schema
from .schema import Family, Param

_INTERP = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\}")

# Keys that describe the document itself rather than a parameter of it.
_META_KEYS = {"schema", "description"}


class ConfigError(ValueError):
    """A configuration file is wrong, and the message says which file and which key."""


@dataclass(frozen=True)
class Entry:
    """One value, plus everything known about where it came from and why."""
    name: str
    value: Any
    note: str | None
    source: str | None
    origin: str          # "<file>:<key>", or "<schema default>"
    param: Param | None

    @property
    def described(self) -> str:
        return self.param.description if self.param else ""


@dataclass(frozen=True)
class Document:
    path: Path
    family: Family
    entries: dict[str, Entry]

    def value(self, name: str, default: Any = None) -> Any:
        entry = self.entries.get(name)
        return default if entry is None else entry.value

    def note(self, name: str) -> str | None:
        entry = self.entries.get(name)
        return None if entry is None else entry.note

    def __contains__(self, name: str) -> bool:
        return name in self.entries


# -- raw parsing ---------------------------------------------------------------

def _read_yaml(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"{path} does not exist")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from None
    if data is None:
        raise ConfigError(f"{path} is empty")
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must be a mapping at the top level, "
                          f"got {type(data).__name__}")
    return data


def _split_entry(path: Path, key: str, raw: Any) -> tuple[Any, str | None, str | None]:
    """Return (value, note, source) for either the bare or the annotated form.

    A mapping counts as the annotated form only when it actually carries a `value`
    key; without that rule a genuine mapping-valued parameter would be mistaken for
    an annotation and lose its contents.
    """
    if isinstance(raw, dict) and "value" in raw:
        unknown = set(raw) - {"value", "note", "source"}
        if unknown:
            raise ConfigError(
                f"{path}:{key} has unexpected field(s) {sorted(unknown)}. An annotated "
                f"entry may hold only value, note and source.")
        return raw["value"], raw.get("note"), raw.get("source")
    return raw, None, None


# -- interpolation -------------------------------------------------------------

def _lookup(scope: str, key: str, context: dict[str, Any], where: str) -> Any:
    if scope == "parameters":
        import parameters as _parameters
        if not hasattr(_parameters, key):
            raise ConfigError(f"{where}: ${{parameters.{key}}} - parameters.py has no "
                              f"constant called {key}")
        return getattr(_parameters, key)
    if scope not in context:
        raise ConfigError(
            f"{where}: ${{{scope}.{key}}} - no scope {scope!r} is available here "
            f"(have: {', '.join(sorted(set(context) | {'parameters'}))})")
    holder = context[scope]
    getter = getattr(holder, "value", None)
    found = getter(key, _MISSING) if callable(getter) else holder.get(key, _MISSING)
    if found is _MISSING:
        raise ConfigError(f"{where}: ${{{scope}.{key}}} - {scope} declares no {key!r}")
    return found


_MISSING = object()


def _interpolate(value: Any, context: dict[str, Any], where: str) -> Any:
    """Substitute ${scope.key} inside strings, recursing into lists and tuples.

    A string that is EXACTLY one reference keeps the referenced object's type, so
    ${parameters.BUS_ID_PREFIXES} stays a tuple instead of becoming its repr. A
    reference embedded in a longer string is rendered into it, which is what a path
    like ${machine.output_root}/ipft_rotterdam_runs needs.
    """
    if isinstance(value, (list, tuple)):
        rendered = [_interpolate(v, context, where) for v in value]
        return type(value)(rendered) if isinstance(value, tuple) else rendered
    if not isinstance(value, str):
        return value
    whole = _INTERP.fullmatch(value.strip())
    if whole:
        return _lookup(whole.group(1), whole.group(2), context, where)
    return _INTERP.sub(
        lambda m: str(_lookup(m.group(1), m.group(2), context, where)), value)


# -- type coercion -------------------------------------------------------------

def _coerce(param: Param, value: Any, where: str) -> Any:
    """Bring a YAML value to the exact type ScenarioPreset expects.

    Tuples rather than lists throughout, because ScenarioPreset is a frozen dataclass
    whose fields are compared field by field against the pre-refactor snapshot: a list
    where the original had a tuple is a difference, and it should be.
    """
    if value is None:
        return None
    t = param.type
    try:
        if t == "int":
            return int(value)
        if t == "float":
            return float(value)
        if t == "bool":
            if isinstance(value, bool):
                return value
            raise TypeError(f"expected true/false, got {value!r}")
        if t in ("str", "path"):
            return str(value)
        if t == "list[str]":
            return tuple(str(v) for v in value)
        if t == "list[float]":
            return tuple(float(v) for v in value)
        if t == "xy":
            x, y = value
            return (float(x), float(y))
        if t == "stops":
            out = []
            for item in value:
                link, x, y = item
                out.append((str(link), float(x), float(y)))
            return tuple(out)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{where}: cannot read {value!r} as {t} ({exc})") from None
    return value


# -- the public entry point ----------------------------------------------------

def load_document(path: str | Path, family: Family,
                  context: dict[str, Any] | None = None) -> Document:
    """Parse, interpolate, type-check and validate one config file against the schema.

    Everything the schema declares for this family is either present in the file or
    filled from its declared default; anything the file declares that the schema does
    not know is an error, not an ignored key, because a typo in a parameter name would
    otherwise mean the value silently never arrives.
    """
    path = Path(path)
    raw = _read_yaml(path)
    context = dict(context or {})

    unknown = set(raw) - schema.known_names(family) - _META_KEYS
    if unknown:
        known = ", ".join(sorted(schema.known_names(family)))
        raise ConfigError(
            f"{path} declares {sorted(unknown)}, which the {family.value} schema does "
            f"not know. Valid keys: {known}")

    entries: dict[str, Entry] = {}
    # Ordered by the schema, not by the file, so an entry that interpolates another
    # can rely on the earlier one already being resolved.
    for param in schema.params_for(family):
        where = f"{path.name}:{param.name}"
        if param.name in raw:
            value, note, source = _split_entry(path, param.name, raw[param.name])
            value = _interpolate(value, {**context, "self": entries}, where)
            value = _coerce(param, value, where)
            origin = f"{path.name}:{param.name}"
        elif param.required:
            raise ConfigError(
                f"{path} is missing the required {family.value} key {param.name!r}.\n"
                f"  what it is: {param.description}")
        else:
            value, note, source, origin = param.default, None, None, "schema default"
        entries[param.name] = Entry(param.name, value, note, source, origin, param)

    missing_notes = [p.name for p in schema.params_for(family)
                     if p.note_recommended and entries[p.name].note is None
                     and p.name in raw]
    if missing_notes:
        print(f"[config] {path.name}: no note on {', '.join(missing_notes)} - these "
              f"carry a judgement that is not recoverable from the value alone")

    return Document(path=path, family=family, entries=entries)


def load_machine(path: str | Path | None = None) -> Document:
    """Machine settings, with the committed example as the fallback.

    A missing machine.yaml is normal on a fresh checkout, so the example is used and
    said out loud rather than treated as an error - but IPFT_OUTPUT_ROOT still wins if
    it is set, because that is the escape hatch that already existed.
    """
    import os
    root = Path(__file__).resolve().parents[2]
    if path is None:
        path = root / "config" / "machine.yaml"
        if not Path(path).exists():
            path = root / "config" / "machine.example.yaml"
            print(f"[config] no config/machine.yaml - using {Path(path).name}")
    doc = load_document(path, Family.MACHINE)
    env_root = os.environ.get("IPFT_OUTPUT_ROOT")
    if env_root:
        entry = doc.entries["output_root"]
        doc.entries["output_root"] = Entry(
            entry.name, env_root, entry.note, entry.source,
            "IPFT_OUTPUT_ROOT (environment)", entry.param)
    return doc
