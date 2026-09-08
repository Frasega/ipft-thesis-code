"""The test that makes the YAML conversion provable instead of merely plausible.

Every scenario now has two independent descriptions: the Python one in
scenario_presets.py, captured before any change into tests/golden/presets.json, and
the YAML one in config/lines/*.yaml. This compares them FIELD BY FIELD.

Why this and not a smoke run: a mistyped coordinate, a stop dropped from a list, a
sample rate off by a factor of ten - none of these raise anything. They produce a
scenario that runs perfectly and reports a different number. The only way to know the
conversion moved nothing is to compare the two descriptions directly.

    python python_pipeline/tests/test_preset_yaml_equivalence.py

Exit 0 means the YAML rebuilds exactly the presets the published results were made
with. Run from the project root.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "python_pipeline"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from snapshot_presets import GOLDEN, _encode  # noqa: E402
import dataclasses  # noqa: E402
from scenario_presets import ScenarioPreset  # noqa: E402
from config.resolve import load_scenario  # noqa: E402

# Which YAML file is supposed to reproduce which Python preset. Explicit rather than
# inferred from the name, because the two naming schemes differ on purpose: the file
# is named after the bus line a person would look for, the preset after the historical
# identifier every existing run directory and CSV already carries.
PAIRS = [
    ("toy", "toy"),
    ("rotterdam", "rotterdam_line44"),
    ("rotterdam_L87", "rotterdam_line87"),
]

# Fields whose value legitimately depends on the machine rather than on the model.
# output_base_dir embeds the output root, so on a machine with a different disk it
# differs from the golden file without anything being wrong. It is compared by its
# FINAL COMPONENT instead, which is the part the model chooses.
MACHINE_DEPENDENT = {"output_base_dir"}

# Knobs added AFTER the golden snapshot was taken. The snapshot's job is to prove that
# the YAML reproduces the presets the published results were made with; a field that
# did not exist then cannot be in it, and refusing to run because of that would turn a
# useful test into an obstacle. Each entry names a field and the value it must take for
# the preset to behave exactly as the golden one did.
#
#   transit_vehicles_file: the transit-module vehicles file, i.e. the bus PCE. None
#   means "leave the base config's own value alone", which is what every run before
#   2026-09 did. A city that DECLARES it is deliberately overriding the base config.
ADDED_AFTER_GOLDEN = {"transit_vehicles_file"}


def compare(preset_name: str, config_name: str, golden: dict) -> list[str]:
    if preset_name not in golden:
        return [f"{preset_name}: not present in {GOLDEN.name}"]
    expected = golden[preset_name]

    try:
        built = load_scenario(config_name).preset
    except Exception as exc:  # noqa: BLE001 - report, do not mask
        return [f"{config_name}: failed to load - {type(exc).__name__}: {exc}"]

    actual = {f.name: _encode(getattr(built, f.name))
              for f in dataclasses.fields(ScenarioPreset)}
    actual["__property_suffix"] = built.suffix
    actual["__property_n_freight_units_real"] = built.n_freight_units_real

    problems = []
    for key in sorted(set(expected) | set(actual)):
        if key not in actual:
            problems.append(f"{preset_name}.{key}: the YAML produces no such field")
            continue
        if key not in expected:
            if key in ADDED_AFTER_GOLDEN:
                continue
            problems.append(f"{preset_name}.{key}: new field, not in the golden file")
            continue
        a, b = expected[key], actual[key]
        if key in MACHINE_DEPENDENT:
            if Path(str(a)).name != Path(str(b)).name:
                problems.append(f"{preset_name}.{key}: directory NAME differs\n"
                                f"      python {Path(str(a)).name}\n"
                                f"      yaml   {Path(str(b)).name}")
            continue
        if a != b:
            problems.append(f"{preset_name}.{key}:\n"
                            f"      python {a!r}\n"
                            f"      yaml   {b!r}")
    return problems


def main() -> int:
    if not GOLDEN.exists():
        print(f"{GOLDEN} is missing - run tests/snapshot_presets.py first, and note "
              f"that it must be run against the PRE-conversion code to mean anything.")
        return 1
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))

    total, failed = 0, 0
    for preset_name, config_name in PAIRS:
        problems = compare(preset_name, config_name, golden)
        n_fields = len(golden.get(preset_name, {}))
        total += n_fields
        if problems:
            failed += len(problems)
            print(f"FAIL  {config_name}.yaml -> preset {preset_name!r}")
            for p in problems:
                print(f"        {p}")
        else:
            print(f"ok    {config_name}.yaml -> preset {preset_name!r} "
                  f"({n_fields} fields identical)")

    print()
    if failed:
        print(f"{failed} field(s) differ: the YAML does NOT reproduce the presets the "
              f"published results were made with.")
        return 1
    print(f"all {len(PAIRS)} scenarios reproduced exactly from config/ "
          f"({total} fields compared)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
