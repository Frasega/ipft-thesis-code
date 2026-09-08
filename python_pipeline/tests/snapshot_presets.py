"""Serialise every ScenarioPreset to JSON — the 'before' of the YAML conversion.

Step 0 of the parameter-driven restructure. The point is narrow and important: after
the presets move from Python into config/lines/*.yaml, the YAML loader must rebuild a
ScenarioPreset that is IDENTICAL FIELD BY FIELD to the one written in Python today.
Without this snapshot the conversion can only be believed; with it, it is checked.

The snapshot is taken from the live objects, not from the source text, so a field
added to ScenarioPreset after this file was written still appears.

    python python_pipeline/tests/snapshot_presets.py              # write the golden file
    python python_pipeline/tests/snapshot_presets.py --check      # compare against it

Run from the project root.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "python_pipeline"))

from scenario_presets import ScenarioPreset, legacy_preset  # noqa: E402

# Every preset get_preset() can return. Kept explicit rather than discovered, so that
# a preset silently dropped from get_preset() fails the check instead of vanishing
# from the comparison.
PRESET_NAMES = ["toy", "rotterdam", "rotterdam_L87"]

GOLDEN = Path(__file__).resolve().parent / "golden" / "presets.json"


def _encode(value):
    """JSON-safe, order-stable rendering of one preset field.

    frozenset is SORTED (its iteration order is not reproducible across runs, and a
    diff on an unordered dump would report false changes); tuple and list keep their
    order, because for pickup_link_ids and van_locker_stops the order IS the datum —
    it is route order, and Term C divides the freight mass along it.
    """
    if isinstance(value, frozenset) or isinstance(value, set):
        return {"__frozenset__": sorted(str(v) for v in value)}
    if isinstance(value, tuple):
        return {"__tuple__": [_encode(v) for v in value]}
    if isinstance(value, list):
        return [_encode(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value


def snapshot_one(name: str) -> dict:
    preset = legacy_preset(name)
    fields = {f.name: _encode(getattr(preset, f.name))
              for f in dataclasses.fields(ScenarioPreset)}
    # The two computed properties are included on purpose: `suffix` decides every
    # generated filename and `n_freight_units_real` is the scale Term C and the
    # feasibility check work in. A conversion that got line_tag or sample_rate wrong
    # would show up here even if every stored field matched.
    fields["__property_suffix"] = preset.suffix
    fields["__property_n_freight_units_real"] = preset.n_freight_units_real
    return fields


def snapshot_all() -> dict:
    return {name: snapshot_one(name) for name in PRESET_NAMES}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="compare the live presets against the golden file instead of "
                         "writing it; exit 1 on any difference")
    args = ap.parse_args()

    live = snapshot_all()

    if not args.check:
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(live, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")
        n_fields = sum(len(v) for v in live.values())
        print(f"wrote {GOLDEN.relative_to(_ROOT)}")
        print(f"  {len(live)} presets, {n_fields} fields: {', '.join(live)}")
        return 0

    if not GOLDEN.exists():
        print(f"golden file missing: {GOLDEN} — run without --check first")
        return 1
    stored = json.loads(GOLDEN.read_text(encoding="utf-8"))

    diffs = []
    for name in sorted(set(stored) | set(live)):
        if name not in stored:
            diffs.append(f"{name}: preset is new (not in the golden file)")
            continue
        if name not in live:
            diffs.append(f"{name}: preset DISAPPEARED from get_preset()")
            continue
        a, b = stored[name], live[name]
        for key in sorted(set(a) | set(b)):
            if key not in a:
                diffs.append(f"{name}.{key}: field added -> {b[key]!r}")
            elif key not in b:
                diffs.append(f"{name}.{key}: field removed (was {a[key]!r})")
            elif a[key] != b[key]:
                diffs.append(f"{name}.{key}:\n    was {a[key]!r}\n    now {b[key]!r}")

    if diffs:
        print(f"{len(diffs)} difference(s) against {GOLDEN.name}:")
        for d in diffs:
            print(f"  {d}")
        return 1
    print(f"presets identical to {GOLDEN.name} "
          f"({len(live)} presets, {sum(len(v) for v in live.values())} fields)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
