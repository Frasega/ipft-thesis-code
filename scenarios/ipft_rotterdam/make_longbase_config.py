"""Build the two equilibration ("longbase") configs: peak and off-peak.

The longbase is the run every scenario branches from: 80 iterations of the background
population with ZERO vans, so the road network settles into the state the freight
scenarios are then added on top of. Peak and off-peak cannot share one - the off-peak
population is half the size and settles differently - so there are two, and both are
built here.

WHAT THIS USED TO DO, AND WHY IT WAS REPLACED (2026-09). It text-substituted two
parameters into `generated/config_alpha100_peak_seed4711.xml`, i.e. into a config that
some earlier run of the generator had left on disk. That made it unusable for a port to
another machine: the file it cloned carried absolute Windows paths, it carried whatever
transit vehicles file (i.e. whatever bus PCE) was current when it was written, and it
existed for the peak only - the off-peak longbase config had been made by hand and could
not be reproduced at all. Both configs also pointed at plans files that no longer
existed. This version builds them from the CALIBRATED base config through the same
patch_config every other generated config goes through, so there is one code path and no
stale intermediate.

    python scenarios/ipft_rotterdam/make_longbase_config.py
    python scenarios/ipft_rotterdam/make_longbase_config.py --pt-vehicles ptVehiclePCE028.xml

The second form is the campaign at the corrected bus PCE: it writes its own configs and
its own output directories, so the two equilibria exist side by side. Whatever tag it
derives from the file name must then be passed on to the warm generator - it is printed
at the end, and make_rotterdam_warm_scenarios.py takes it as --pt-vehicles too.
"""
from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python_pipeline"))

from generate_configs import patch_config, _write_config_with_doctype  # noqa: E402
from insert_vans import create_plans_file  # noqa: E402
from make_rotterdam_warm_scenarios import pt_vehicles_for  # noqa: E402
from scenario_presets import OUTPUT_ROOT, get_preset  # noqa: E402

# The equilibration carries no freight: alpha = 1 means every parcel goes by bus, so
# zero van tours are inserted and what equilibrates is the background population alone.
LONGBASE_ALPHA = 1.0
LONGBASE_SEED = 4711
LONGBASE_ITERATIONS = 80

_RUN_DIRS = {"peak": "ipft_rotterdam_longbase",
             "offpeak": "ipft_rotterdam_longbase_offpeak"}


def longbase_plans(preset, congestion: str, generated: Path) -> Path:
    """The zero-van population for this demand level, created if it is not there.

    Same call the scenario generator makes at alpha = 1, so the equilibration starts
    from exactly the population the scenarios do.
    """
    ext = ".xml.gz" if str(preset.peak_base_plans).endswith(".gz") else ".xml"
    path = generated / f"plans_alpha100_{congestion}{ext}"
    if path.exists():
        print(f"[plans] {congestion}: reusing {path.name}")
        return path
    base = preset.peak_base_plans if congestion == "peak" else preset.offpeak_base_plans
    if not Path(base).exists():
        raise SystemExit(
            f"{congestion}: the base population {base} is missing. It is scenario data "
            f"(XCARCITY), not code, and is transferred separately - see the README.")
    print(f"[plans] {congestion}: building {path.name} from {Path(base).name}")
    create_plans_file(
        base_plans_path=str(base), output_path=str(path), alpha=LONGBASE_ALPHA,
        n_freight=preset.n_freight_units_sim, congestion=congestion, verbose=False,
        hub_link=preset.hub_link, terminal_link=preset.terminal_link,
        hub_x=preset.hub_xy[0], hub_y=preset.hub_xy[1],
        terminal_x=preset.terminal_xy[0], terminal_y=preset.terminal_xy[1],
        van_mode=preset.van_mode, spread_minutes=preset.van_spread_minutes,
        locker_stops=preset.van_locker_stops,
    )
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scenario", default="rotterdam",
                    help="Scenario preset (default: rotterdam, i.e. line 44)")
    ap.add_argument("--congestion", choices=["peak", "offpeak", "both"], default="both")
    ap.add_argument("--iterations", type=int, default=LONGBASE_ITERATIONS,
                    help=f"Iterations to equilibrate for (default {LONGBASE_ITERATIONS}). "
                         f"Lower it only for a timing probe - the campaign needs the full "
                         f"count, and a shorter equilibration is not a cheaper one, it is "
                         f"a different network state.")
    ap.add_argument("--seed", type=int, default=LONGBASE_SEED)
    ap.add_argument("--pt-vehicles", default=None,
                    help="Transit vehicles file, i.e. the bus PCE - e.g. "
                         "ptVehiclePCE028.xml. Its name becomes a tag in the config "
                         "name and in the output directory, so a second PCE gets a "
                         "second equilibrium instead of overwriting the first.")
    ap.add_argument("--output-root", default=None,
                    help="Override the run output root for this invocation "
                         "(otherwise IPFT_OUTPUT_ROOT / config/machine.yaml).")
    args = ap.parse_args()

    preset = get_preset(args.scenario)
    generated = Path(preset.generated_dir)
    generated.mkdir(parents=True, exist_ok=True)
    out_root = Path(args.output_root) if args.output_root else OUTPUT_ROOT
    pt_vehicles, pt_tag = pt_vehicles_for(args.pt_vehicles, preset)
    sfx = preset.suffix

    levels = ["peak", "offpeak"] if args.congestion == "both" else [args.congestion]
    written = []
    for congestion in levels:
        plans = longbase_plans(preset, congestion, generated)
        run_dir = _RUN_DIRS[congestion] + sfx + (f"_{pt_tag.lower()}" if pt_tag else "")
        out_dir = str((out_root / run_dir).resolve())
        tree = patch_config(
            base_config_path=preset.base_config,
            plans_file=str(plans.resolve()),
            output_dir=out_dir,
            seed=args.seed,
            last_iteration=args.iterations,
            preset=preset,
            transit_vehicles_file=pt_vehicles,
        )
        ET.indent(tree, space="  ")
        name = (f"config_LONGBASE{sfx.upper().replace('_', '')}{pt_tag}"
                f"_{congestion}_seed{args.seed}.xml")
        cfg = generated / name
        _write_config_with_doctype(tree, str(cfg))
        written.append((cfg, out_dir))
        # Machine-readable, for the batch script that has to find this file again.
        # Globbing for it does not work once a second PCE campaign exists: both
        # config_LONGBASE_peak_*.xml and config_LONGBASEPCE028_peak_*.xml match, and
        # which one a glob returns last is an accident of ASCII ordering.
        print(f"LONGBASE_CONFIG {congestion} {cfg}")
        print(f"[longbase] {congestion:7s} -> {cfg.name}\n"
              f"           plans  {plans}\n"
              f"           output {out_dir}\n"
              f"           {args.iterations} iterations, seed {args.seed}"
              + (f", transit vehicles {pt_vehicles}" if pt_vehicles else ""))

    print("\nRun them - they are independent, so launch both at once if you can:")
    for cfg, _ in written:
        print(f"  python python_pipeline/scenario_runner.py --config {cfg} --heap 12g")
    if pt_tag:
        print(f"\nThen the warm scenarios must branch from THESE equilibria, not from "
              f"the old ones:\n"
              f"  python python_pipeline/make_rotterdam_warm_scenarios.py "
              f"--dwell-tag blocking --pt-vehicles {Path(args.pt_vehicles).name}\n"
              f"which resolves the longbase directory with the same '{pt_tag.lower()}' "
              f"suffix. Check the [seed] line it prints.")


if __name__ == "__main__":
    main()
