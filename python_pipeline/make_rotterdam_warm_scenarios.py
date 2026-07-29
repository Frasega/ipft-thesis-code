"""
Rotterdam warm-start scenario generator — the core sensitivity surface.

Each scenario branches from the FROZEN Rotterdam longbase equilibrium (background
mode/route/departure-time equilibrated, ZERO vans) and adds the consolidated
(1-alpha)*N van tours on top, then runs ONE iteration with ALL innovation frozen
(only ChangeExpBeta plan selection kept). The baseline-minus-scenario delta is then
the deterministic physical road-space effect of the vans alone (clean S_cong),
the Rotterdam framing of decision #5 (HANDOFF §3.3).

Differences vs the toy generator:
  - branches from the Rotterdam longbase seeds on D: (read directly as .zst via the
    insert_vans _open_text adapter — no manual decompress);
  - Rotterdam preset: van_mode='car', spread 07:00-09:00 (120 min), N=470 simulated,
    hub/terminal depot links, 10% sample;
  - freeze removes ReRoute AND TimeAllocationMutator (the toy had neither time
    mutation); SubtourModeChoice is already off in the Rotterdam config.

Core surface generated here: alpha {0,.25,.5,.75,1} x congestion {peak,offpeak} x
weight {light,medium,heavy} = 30 warm runs (per-weight consolidated van counts, so
run names carry the weight; analyse with sensitivity_surface --scenario rotterdam
--per-weight-runs). The van-capacity / total-N / bus-capacity EXTRA slices are added
separately (the gate showed van-capacity x weight interacts -> that slice runs at
heavy).

Prereq: BOTH longbase runs DONE:
  peak    -> D:/TesiOutputs/ipft_rotterdam_longbase/...output_plans.xml.zst   (ready)
  offpeak -> D:/TesiOutputs/ipft_rotterdam_longbase_offpeak/...output_plans.xml.zst

Run from project root:  python python_pipeline/make_rotterdam_warm_scenarios.py
  [--congestion peak|offpeak|both]   (default both; use 'peak' to start the peak
                                       half while the offpeak longbase still runs)
"""
import argparse
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from insert_vans import create_plans_file
from generate_configs import patch_config, _write_config_with_doctype
from parameters import ALPHA_VALUES, RANDOM_SEEDS, WEIGHT_REGIMES, c_van
from scenario_presets import get_preset

WARM_ITERS = 1
preset = get_preset("rotterdam")
GEN = Path(preset.generated_dir)
# Warm plans = frozen seed (large) + vans -> write GZIPPED on D:, not in the synced tree.
WARM_PLANS_DIR = Path("D:/TesiOutputs/ipft_rotterdam_warm_plans")
WARM_PLANS_DIR.mkdir(parents=True, exist_ok=True)

_SEED_DIRS = {
    "peak": "ipft_rotterdam_longbase",
    "offpeak": "ipft_rotterdam_longbase_offpeak",
}
# Keep only this selector; everything else is innovation to freeze.
_KEEP_STRATEGY = "ChangeExpBeta"


def rotterdam_seed(congestion: str) -> str:
    """Path to the frozen longbase output_plans for this congestion level."""
    d = Path("D:/TesiOutputs") / _SEED_DIRS[congestion]
    for name in ("MRDH_10pct.output_plans.xml.zst", "output_plans.xml.zst",
                 "output_plans.xml.gz", "output_plans.xml"):
        if (d / name).exists():
            return str(d / name)
    raise FileNotFoundError(
        f"longbase output_plans not found in {d} — run the {congestion} LONGBASE first "
        f"(config_LONGBASE_{congestion}_seed4711.xml).")


def freeze_replanning_rotterdam(tree: ET.ElementTree) -> ET.ElementTree:
    """Freeze ALL background innovation for the warm run: keep only ChangeExpBeta,
    remove every other strategy parameterset (ReRoute, TimeAllocationMutator,
    SubtourModeChoice if present). The background then keeps its frozen equilibrium
    routes/modes/times; inserting the vans is the only change between baseline and
    scenario, so S_cong is the deterministic physical road-space effect of the vans."""
    root = tree.getroot()
    for mod in root.iter("module"):
        if mod.get("name") in ("replanning", "strategy"):
            for ps in list(mod.findall("parameterset")):
                names = [p.get("value") for p in ps.iter("param")
                         if p.get("name") == "strategyName"]
                if names and _KEEP_STRATEGY not in names:
                    mod.remove(ps)
    return tree


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--congestion", choices=["peak", "offpeak", "both"], default="both")
    args = ap.parse_args()
    levels = ["peak", "offpeak"] if args.congestion == "both" else [args.congestion]

    N = preset.n_freight_units_sim          # 470 simulated parcels
    runs = []
    for congestion in levels:
        base = rotterdam_seed(congestion)
        print(f"[{congestion}] seed = {base}")
        for weight_regime, weight_kg in WEIGHT_REGIMES.items():
            cvan = c_van(weight_kg)          # light 150, medium 110, heavy 44
            for alpha in ALPHA_VALUES:
                astr = f"{int(alpha * 100):03d}"
                n_tours = math.ceil((1 - alpha) * N / cvan) if (1 - alpha) * N > 0 else 0
                plans_path = str(WARM_PLANS_DIR /
                                 f"warmplans_alpha{astr}_{congestion}_{weight_regime}.xml.gz")
                if not Path(plans_path).exists():
                    create_plans_file(
                        base_plans_path=base, output_path=plans_path, alpha=alpha,
                        n_freight=N, congestion=congestion, verbose=False,
                        hub_link=preset.hub_link, terminal_link=preset.terminal_link,
                        hub_x=preset.hub_xy[0], hub_y=preset.hub_xy[1],
                        terminal_x=preset.terminal_xy[0], terminal_y=preset.terminal_xy[1],
                        van_mode=preset.van_mode, spread_minutes=preset.van_spread_minutes,
                        n_vans_override=n_tours,
                    )
                for seed in RANDOM_SEEDS:
                    run = f"alpha{astr}_{congestion}_{weight_regime}_seed{seed}"
                    out = str((Path(preset.output_base_dir) / run).resolve())
                    tree = patch_config(
                        base_config_path=preset.base_config,
                        plans_file=str(Path(plans_path).resolve()),
                        output_dir=out, seed=seed, last_iteration=WARM_ITERS, preset=preset,
                    )
                    freeze_replanning_rotterdam(tree)
                    ET.indent(tree, space="  ")
                    cfg = str(GEN / f"config_RWARM_{run}.xml")
                    _write_config_with_doctype(tree, cfg)
                    runs.append(run)
                print(f"  alpha={astr} {congestion:7s} {weight_regime:6s} "
                      f"(C_van={cvan}) -> {n_tours} van-tours")
    print(f"\nGenerated {len(runs)} Rotterdam warm configs (config_RWARM_*). Run with:\n"
          f"  scenario_runner.py --scenario rotterdam --filter RWARM --heap <fit> --skip-existing\n"
          f"then sensitivity_surface.py --scenario rotterdam "
          f"--runs-dir {preset.output_base_dir} --per-weight-runs --out output/sensitivity_rotterdam")


if __name__ == "__main__":
    main()
