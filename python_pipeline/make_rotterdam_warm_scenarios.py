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

DWELL-IN-MATSIM variant (--dwell-tag blocking|bay): every config points at the
per-alpha schedule from make_dwell_schedules.py, so the freight handover time is
simulated (minimumStopDuration) and the stopped bus holds the traffic lane
(isBlocking). Configs are named config_RDWELL_* and write to a SEPARATE output
tree, so the pre-dwell surface stays untouched:

    python python_pipeline/make_dwell_schedules.py --blocking true
    python python_pipeline/make_rotterdam_warm_scenarios.py --dwell-tag blocking
    python python_pipeline/scenario_runner.py --scenario rotterdam --filter RDWELLBLOCKING ...
        (--filter is a plain substring match: "RDWELL" would launch the bay
         variant too, i.e. 120 runs instead of 60)
    python python_pipeline/rotterdam_surface_robust.py \
        --runs-dir D:/TesiOutputs/ipft_rotterdam_dwell_blocking_runs \
        --out output/sensitivity_rotterdam_dwell_blocking --dwell-in-matsim

The alpha=0 BASELINE gets the same variant schedule (dwell 0 s, but isBlocking
set exactly as in the scenarios). That is deliberate: the bus stops for
passengers and blocks the lane on BOTH sides, so that part cancels in
baseline - scenario and what is measured is only the extra freight seconds.
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
# NOTE: this module-level value is the LINE-44 default. main() recomputes it from
# the preset it actually selected — otherwise a second line's configs land in line
# 44's generated/ directory, where Co2TotalsHandler would resolve '../corridor_links.txt'
# to line 44's link set and every bucket would be silently wrong. Bit us on 2026-08-20.
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
    """Path to the frozen longbase output_plans for this congestion level.

    The STRIPPED file wins when present. The LONGBASE ends with 5 plans per
    person, and one warm iteration does not stop ChangeExpBeta from picking a
    different one: inserting a different number of vans shifts MATSim's random
    stream, thousands of background agents re-pick, and on the 18 bus-stop links
    that noise is the size of the signal. Keeping only the plan each agent was
    already running changes nobody's behaviour and removes the re-pick.

    Produced by:
        python python_pipeline/strip_selected_plans.py IN.xml.zst OUT_stripped.xml.gz
    """
    d = Path("D:/TesiOutputs") / _SEED_DIRS[congestion]
    for name in ("MRDH_10pct.output_plans_stripped.xml.gz",
                 "output_plans_stripped.xml.gz"):
        if (d / name).exists():
            return str(d / name)
    for name in ("MRDH_10pct.output_plans.xml.zst", "output_plans.xml.zst",
                 "output_plans.xml.gz", "output_plans.xml"):
        if (d / name).exists():
            print(f"[warn] {congestion}: using the UNSTRIPPED longbase plans "
                  f"({name}) — ChangeExpBeta will re-pick between baseline and "
                  f"scenario. Run strip_selected_plans.py first.")
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


def dwell_schedule_for(alpha: float, tag: str, schedules_dir: Path) -> str:
    """Absolute path to the per-alpha dwell schedule; fails loudly if missing
    (a silent fall-back to the plain schedule would produce runs that look like
    dwell runs but contain no dwell at all)."""
    astr = f"{int(round(alpha * 100)):03d}"
    path = schedules_dir / f"ptSchedule_dwell_alpha{astr}_{tag}.xml.gz"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — run 'python python_pipeline/make_dwell_schedules.py "
            f"--blocking {'true' if tag == 'blocking' else 'false'}' first.")
    return str(path.resolve())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--congestion", choices=["peak", "offpeak", "both"], default="both")
    ap.add_argument("--dwell-tag", choices=["blocking", "bay"], default=None,
                    help="Generate the dwell-in-MATSim variant: point every config at "
                         "the per-alpha schedule (minimumStopDuration + isBlocking). "
                         "blocking = bus holds the lane (headline), bay = pulls off "
                         "the road (policy sensitivity). Omit for the pre-dwell runs.")
    ap.add_argument("--dwell-schedules-dir", default=None,
                    help="Default: scenarios/ipft_rotterdam/dwell_schedules")
    ap.add_argument("--alphas", nargs="*", type=float, default=None,
                    help="subset of ALPHA_VALUES (default: all five). Each alpha costs "
                         "one warm-plans file per weight and congestion level.")
    ap.add_argument("--scenario", default="rotterdam",
                    help="preset name: rotterdam (line 44) or rotterdam_L87. The "
                         "preset's line_tag goes into every generated name, so two "
                         "lines never share a warm-plans file, a config, a run "
                         "directory or a surface.")
    ap.add_argument("--output-base-dir", default=None,
                    help="Default: preset dir, or "
                         "D:/TesiOutputs/ipft_rotterdam_dwell_<tag>_runs with --dwell-tag")
    args = ap.parse_args()
    global preset, GEN
    preset = get_preset(args.scenario)
    GEN = Path(preset.generated_dir)
    GEN.mkdir(parents=True, exist_ok=True)
    sfx = preset.suffix
    levels = ["peak", "offpeak"] if args.congestion == "both" else [args.congestion]

    dwell_tag = args.dwell_tag
    sched_dir = (Path(args.dwell_schedules_dir) if args.dwell_schedules_dir
                 else Path(preset.base_config).parent / f"dwell_schedules{sfx}")
    # The tag MUST be in the config name: blocking and bay write to different
    # output trees but would otherwise share config file names and overwrite
    # each other. "--filter RDWELL" still matches both variants.
    cfg_prefix = (f"RDWELL{dwell_tag.upper()}" if dwell_tag else "RWARM") + sfx.replace("_", "")
    if args.output_base_dir:
        out_base = Path(args.output_base_dir)
    elif dwell_tag:
        out_base = Path("D:/TesiOutputs") / f"ipft_rotterdam{sfx}_dwell_{dwell_tag}_runs"
    else:
        out_base = Path(preset.output_base_dir)
    if dwell_tag:
        print(f"[dwell] variant={dwell_tag}  schedules={sched_dir}\n"
              f"[dwell] output tree={out_base}  configs=config_{cfg_prefix}_*")

    N = preset.n_freight_units_sim          # 470 simulated parcels
    runs = []
    for congestion in levels:
        base = rotterdam_seed(congestion)
        print(f"[{congestion}] seed = {base}")
        for weight_regime, weight_kg in WEIGHT_REGIMES.items():
            cvan = c_van(weight_kg)          # light 150, medium 110, heavy 44
            for alpha in (args.alphas if args.alphas else ALPHA_VALUES):
                astr = f"{int(alpha * 100):03d}"
                n_tours = math.ceil((1 - alpha) * N / cvan) if (1 - alpha) * N > 0 else 0
                plans_path = str(WARM_PLANS_DIR /
                                 f"warmplans{sfx}_alpha{astr}_{congestion}_{weight_regime}.xml.gz")
                if not Path(plans_path).exists():
                    create_plans_file(
                        base_plans_path=base, output_path=plans_path, alpha=alpha,
                        n_freight=N, congestion=congestion, verbose=False,
                        hub_link=preset.hub_link, terminal_link=preset.terminal_link,
                        hub_x=preset.hub_xy[0], hub_y=preset.hub_xy[1],
                        terminal_x=preset.terminal_xy[0], terminal_y=preset.terminal_xy[1],
                        van_mode=preset.van_mode, spread_minutes=preset.van_spread_minutes,
                        n_vans_override=n_tours,
                        locker_stops=preset.van_locker_stops,
                        # The departure grid is the BASELINE's: a van that
                        # survives keeps the exact time it had at alpha=0, so
                        # baseline - scenario is the missing tours and not the
                        # same tours moved elsewhere in the peak. At alpha=0
                        # grid == n_tours, so the baselines are unchanged.
                        n_slots=math.ceil(N / cvan),
                    )
                sched = (dwell_schedule_for(alpha, dwell_tag, sched_dir)
                         if dwell_tag else None)
                for seed in RANDOM_SEEDS:
                    run = f"alpha{astr}_{congestion}_{weight_regime}_seed{seed}"
                    out = str((out_base / run).resolve())
                    tree = patch_config(
                        base_config_path=preset.base_config,
                        plans_file=str(Path(plans_path).resolve()),
                        output_dir=out, seed=seed, last_iteration=WARM_ITERS, preset=preset,
                        transit_schedule_file=sched,
                    )
                    freeze_replanning_rotterdam(tree)
                    ET.indent(tree, space="  ")
                    cfg = str(GEN / f"config_{cfg_prefix}_{run}.xml")
                    _write_config_with_doctype(tree, cfg)
                    runs.append(run)
                dwell_note = (f" dwell={Path(sched).name}" if sched else "")
                print(f"  alpha={astr} {congestion:7s} {weight_regime:6s} "
                      f"(C_van={cvan}) -> {n_tours} van-tours{dwell_note}")
    surface_out = (f"output/sensitivity_rotterdam{sfx}_dwell_{dwell_tag}" if dwell_tag
                   else f"output/sensitivity_rotterdam{sfx}")
    dwell_flag = " --dwell-in-matsim" if dwell_tag else ""
    print(f"\nGenerated {len(runs)} Rotterdam warm configs (config_{cfg_prefix}_*). Run with:\n"
          f"  scenario_runner.py --scenario rotterdam --filter {cfg_prefix} --heap <fit> "
          f"--skip-existing\n"
          f"then rotterdam_surface_robust.py --runs-dir {out_base} "
          f"--out {surface_out}{dwell_flag}")


if __name__ == "__main__":
    main()
