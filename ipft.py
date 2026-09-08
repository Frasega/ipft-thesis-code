#!/usr/bin/env python
"""IPFT - one place to put the inputs in, one place to read the outputs.

    python ipft.py lines                      what scenarios exist
    python ipft.py describe rotterdam         every input value, what it means, where it came from
    python ipft.py check                      validate every configuration file
    python ipft.py check rotterdam_L87        validate one

`describe` is the answer to "what can I change, and what happens if I do". It prints
the RESOLVED configuration - the values the model would actually use, after the city
and the line have been joined and every ${...} reference has been followed - alongside
the description of each one and the file it came from. Nothing is hidden in Python.

Run from the project root.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT / "python_pipeline"))

from config import loader, resolve  # noqa: E402
from config.loader import ConfigError  # noqa: E402
from config.schema import Family  # noqa: E402

_WIDTH = 88


def _wrap(text: str, indent: str, width: int = _WIDTH) -> str:
    import textwrap
    return textwrap.fill(" ".join(text.split()), width=width,
                         initial_indent=indent, subsequent_indent=indent)


def _render(value: object) -> str:
    """A value a person can read, without a 16-line coordinate dump."""
    if value is None:
        return "(not set)"
    if isinstance(value, frozenset):
        return f"{len(value)} ids"
    if isinstance(value, tuple) and value and isinstance(value[0], tuple):
        return f"{len(value)} entries, first {value[0]}"
    if isinstance(value, tuple):
        rendered = ", ".join(str(v) for v in value)
        return f"[{rendered}]" if len(rendered) <= 70 else f"{len(value)} entries"
    return str(value)


def cmd_lines(_args: argparse.Namespace) -> int:
    found = resolve.available_lines()
    if not found:
        print(f"no line configurations in {resolve.CONFIG_DIR / 'lines'}")
        return 1
    by_path: dict[Path, list[str]] = {}
    for alias, path in found.items():
        by_path.setdefault(path, []).append(alias)
    print(f"{len(by_path)} line configuration(s) in {resolve.CONFIG_DIR / 'lines'}:\n")
    for path, aliases in sorted(by_path.items()):
        try:
            rs = resolve.load_scenario(path.stem, skip_validation=True)
            city = rs.city.value("name")
            detail = (f"city {city}, line {rs.preset.transit_line_id or '-'}, "
                      f"F={rs.preset.bus_trips_per_day}, "
                      f"{rs.preset.n_pickup_stops} stops, "
                      f"N={rs.preset.n_freight_units_sim}")
        except Exception as exc:  # noqa: BLE001
            detail = f"UNREADABLE - {type(exc).__name__}"
        print(f"  {path.name}")
        print(f"      {detail}")
        print(f"      accepted names: {', '.join(sorted(aliases))}\n")
    return 0


def _print_family(title: str, doc, subset: list[str] | None = None) -> None:
    print(f"\n{'=' * _WIDTH}\n{title}\n{'=' * _WIDTH}")
    for name, entry in doc.entries.items():
        if subset is not None and name not in subset:
            continue
        param = entry.param
        unit = f" [{param.unit}]" if param and param.unit else ""
        print(f"\n  {name}{unit} = {_render(entry.value)}")
        if param and param.description:
            print(_wrap(param.description, "        "))
        if entry.note:
            print(_wrap(f"NOTE: {entry.note}", "        "))
        if entry.source:
            print(_wrap(f"SOURCE: {entry.source}", "        "))
        if param and param.affects:
            print(f"        affects: {param.affects}")
        print(f"        from: {entry.origin}")


def cmd_describe(args: argparse.Namespace) -> int:
    try:
        rs = resolve.load_scenario(args.scenario)
    except ConfigError as exc:
        print(f"ipft describe: {exc}")
        return 1

    preset = rs.preset
    print(f"\nSCENARIO {preset.name!r}")
    print(f"  city   {rs.city.path.relative_to(_ROOT)}")
    print(f"  line   {rs.line.path.relative_to(_ROOT)}")
    print(f"  machine{'':1s}{rs.machine.path.relative_to(_ROOT)}")
    print(f"\n  {preset.bus_trips_per_day} freight-carrying departures/day, "
          f"{preset.n_pickup_stops} delivery stops, "
          f"{preset.n_freight_units_sim} simulated parcels/day "
          f"({preset.n_freight_units_real:,.0f} real at a "
          f"{preset.sample_rate:.0%} population sample)")

    if not args.all:
        _print_family("LINE - change these to model a different corridor", rs.line)
        print(f"\n{'-' * _WIDTH}")
        print("city and machine settings are hidden; add --all to show them, or read")
        print(f"  {rs.city.path.relative_to(_ROOT)}")
        return 0

    _print_family("MACHINE - where this computer keeps things", rs.machine)
    _print_family("CITY - the simulated world, shared by every line of this city", rs.city)
    _print_family("LINE - change these to model a different corridor", rs.line)

    print(f"\n{'=' * _WIDTH}")
    print("PHYSICS - vehicles, fuel, parcels")
    print(f"{'=' * _WIDTH}")
    print(_wrap(
        "Not listed here, and not a free input. These values live in "
        "python_pipeline/parameters.py, which is a sourced document rather than a "
        "settings file: every constant carries its citation, its plausible range and "
        "the reason for the value chosen. Changing one is a code change with a "
        "rationale, and a van specification with no source is refused outright rather "
        "than allowed to reach a result.", "  "))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    names = ([args.scenario] if args.scenario
             else sorted({p.stem for p in resolve.available_lines().values()}))
    if not names:
        print("no line configurations to check")
        return 1
    failures = 0
    for name in names:
        try:
            rs = resolve.load_scenario(name)
            print(f"ok    {name:22s} -> preset {rs.preset.name!r}")
        except ConfigError as exc:
            failures += 1
            print(f"FAIL  {name}")
            for line in str(exc).splitlines():
                print(f"        {line}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"ERROR {name}: {type(exc).__name__}: {exc}")
    print()
    if failures:
        print(f"{failures} of {len(names)} configuration(s) are not usable")
        return 1
    print(f"all {len(names)} configuration(s) valid")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="ipft", description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n".join(__doc__.splitlines()[1:]))
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("lines", help="list the available scenarios")
    p.set_defaults(func=cmd_lines)

    p = sub.add_parser("describe", help="print every input value with its meaning")
    p.add_argument("scenario", help="line name, config file stem, or path")
    p.add_argument("--all", action="store_true",
                   help="also show the city and machine settings, not just the line")
    p.set_defaults(func=cmd_describe)

    p = sub.add_parser("check", help="validate configuration files")
    p.add_argument("scenario", nargs="?", default=None,
                   help="one scenario; omit to check every one")
    p.set_defaults(func=cmd_check)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
