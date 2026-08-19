"""
Generate all 20 MATSim config files and their corresponding plans files.

Run matrix:
  alpha     in {0%, 25%, 50%, 75%, 100%}   — 5 levels
  congestion in {peak, offpeak}             — 2 levels
  seed       in {4711, 9876}                — 2 seeds
  Total: 5 × 2 × 2 = 20 runs

For each (alpha, congestion) combination:
  1. Create a plans file: base_plans + (1-alpha)*N_freight backup vans
  2. Create 2 config files: one per seed, pointing at that plans file

Works for BOTH the toy and the Rotterdam scenario (--scenario rotterdam):
the two base configs use different module names (toy: controller/replanning/
scoring/routing — new style; Rotterdam: controler/strategy/planCalcScore/
planscalcroute — old style). All lookups go through alias tables.

Usage:
    cd matsim-example-project-master
    python python_pipeline/generate_configs.py --scenario toy --iterations 10
    python python_pipeline/generate_configs.py --scenario rotterdam --iterations 100
"""

from __future__ import annotations

import argparse
import io
import xml.etree.ElementTree as ET
from pathlib import Path

from insert_vans import create_plans_file, create_offpeak_base
from parameters import (
    ALPHA_VALUES,
    RANDOM_SEEDS as SEEDS,
    BASELINE_EXTRA_SEEDS,
)
from scenario_presets import ScenarioPreset, get_preset

MATSIM_CONFIG_DOCTYPE = (
    '<?xml version="1.0" ?>\n'
    '<!DOCTYPE config SYSTEM "http://www.matsim.org/files/dtd/config_v2.dtd">\n'
)

# Module-name aliases: MATSim accepts both the historical and the current name.
# The toy base config uses the new names, the Rotterdam (XCARCITY) config the old ones.
MODULE_ALIASES = {
    "controller": ("controller", "controler"),
    "replanning": ("replanning", "strategy"),
    "scoring":    ("scoring", "planCalcScore"),
    "routing":    ("routing", "planscalcroute"),
}


def _write_config_with_doctype(tree: ET.ElementTree, path: str) -> None:
    """Write config XML with the MATSim DOCTYPE declaration that ConfigReader requires."""
    buf = io.BytesIO()
    tree.write(buf, xml_declaration=False, encoding="utf-8")
    xml_body = buf.getvalue().decode("utf-8")
    with open(path, "w", encoding="utf-8") as f:
        f.write(MATSIM_CONFIG_DOCTYPE)
        f.write(xml_body)
        f.write("\n")


# ── Config patcher ─────────────────────────────────────────────────────────

def _set_param(module_elem, name: str, value: str) -> None:
    """Set a <param> value inside a <module> element (direct children + nested)."""
    for p in module_elem.iter("param"):
        if p.get("name") == name:
            p.set("value", value)
            return
    ET.SubElement(module_elem, "param", name=name, value=value)


def _get_module(root, logical_name: str):
    """Find a module by logical name, accepting old/new MATSim aliases."""
    names = MODULE_ALIASES.get(logical_name, (logical_name,))
    for m in root.iter("module"):
        if m.get("name") in names:
            return m
    return None


def _get_module_exact(root, name: str):
    for m in root.iter("module"):
        if m.get("name") == name:
            return m
    return None


def _scoring_container(scoring_mod):
    """
    Return the element to which activityParams / modeParams parametersets must
    be appended. New-style configs nest them in a
    <parameterset type="scoringParameters"> block; old flat configs (and the
    toy) accept them directly under the module.
    """
    for ps in scoring_mod.findall("parameterset"):
        if ps.get("type") == "scoringParameters":
            return ps
    return scoring_mod


def patch_config(
    base_config_path: str,
    plans_file: str,
    output_dir: str,
    seed: int,
    last_iteration: int,
    preset: ScenarioPreset,
    flow_capacity_factor: float | None = None,
    storage_capacity_factor: float | None = None,
    transit_schedule_file: str | None = None,
) -> ET.ElementTree:
    """
    Return a modified copy of the base config XML tree with:
      - plans file / output directory / random seed / lastIteration updated
      - input file paths re-prefixed for the generated/ subdirectory
      - freight subpopulation replanning added (ChangeExpBeta only)
      - freight_hub and freight_delivery activity scoring added
      - emissions module (HBEFA average factors) added
      - qsim vehiclesSource=modeVehicleTypesFromVehiclesData + vehicles file
      - capacity factors set (defaults from the scenario preset)
      - transitScheduleFile overridden when transit_schedule_file is given
        (dwell-in-MATSim: the per-alpha schedule from make_dwell_schedules.py).
        Pass an ABSOLUTE path — it is applied after the ../ re-prefixing, so a
        relative one would be resolved against generated/ and break.
    """
    tree = ET.parse(base_config_path)
    root = tree.getroot()

    flow = flow_capacity_factor if flow_capacity_factor is not None else preset.flow_capacity_factor
    storage = (storage_capacity_factor if storage_capacity_factor is not None
               else preset.storage_capacity_factor)

    # ── Fix relative input file paths (config is in generated/ subdir,
    #    input files live in the parent scenarios/<scenario>/ directory) ────
    RELATIVE_INPUTS = {
        "network":    ["inputNetworkFile"],
        "facilities": ["inputFacilitiesFile"],
        "transit":    ["transitScheduleFile", "vehiclesFile"],
    }
    for mod_name, param_names in RELATIVE_INPUTS.items():
        mod = _get_module_exact(root, mod_name)
        if mod is None:
            continue
        for pname in param_names:
            for p in mod.iter("param"):
                if p.get("name") == pname:
                    val = p.get("value", "")
                    if val and not val.startswith(("../", "/", "C:", "D:")):
                        p.set("value", f"../{val}")

    # ── Transit schedule override (dwell-in-MATSim per-alpha schedules) ───
    # Applied AFTER the ../ re-prefixing above so an absolute path survives.
    if transit_schedule_file is not None:
        transit_mod = _get_module_exact(root, "transit")
        if transit_mod is None:
            raise ValueError(f"No transit module in {base_config_path} — cannot "
                             f"override transitScheduleFile")
        _set_param(transit_mod, "transitScheduleFile", transit_schedule_file)

    # ── Global: seed ──────────────────────────────────────────────────────
    global_mod = _get_module_exact(root, "global")
    if global_mod is not None:
        _set_param(global_mod, "randomSeed", str(seed))

    # ── Plans file ────────────────────────────────────────────────────────
    plans_mod = _get_module_exact(root, "plans")
    if plans_mod is not None:
        _set_param(plans_mod, "inputPlansFile", plans_file)

    # ── Controller: output dir + iterations ───────────────────────────────
    ctrl_mod = _get_module(root, "controller")
    if ctrl_mod is None:
        raise ValueError(f"No controller/controler module found in {base_config_path}")
    _set_param(ctrl_mod, "outputDirectory", output_dir)
    _set_param(ctrl_mod, "lastIteration", str(last_iteration))

    # ── Replanning: add freight subpopulation (ChangeExpBeta only) ────────
    replan_mod = _get_module(root, "replanning")
    if replan_mod is not None:
        existing = [
            ps for ps in replan_mod.iter("parameterset")
            if ps.get("type") == "strategysettings"
            and any(p.get("name") == "subpopulation" and p.get("value") == "freight"
                    for p in ps.iter("param"))
        ]
        if not existing:
            ps = ET.SubElement(replan_mod, "parameterset")
            ps.set("type", "strategysettings")
            ET.SubElement(ps, "param", name="subpopulation", value="freight")
            ET.SubElement(ps, "param", name="strategyName", value="ChangeExpBeta")
            ET.SubElement(ps, "param", name="weight", value="1.0")

    # ── Scoring: add freight activity types (+ 'freight' mode for the toy) ─
    scoring_mod = _get_module(root, "scoring")
    if scoring_mod is not None:
        container = _scoring_container(scoring_mod)
        existing_types = {
            p.get("value")
            for ps in scoring_mod.iter("parameterset")
            if ps.get("type") == "activityParams"
            for p in ps.iter("param")
            if p.get("name") == "activityType"
        }
        # freight_locker: the kerbside handover stops the van drives through.
        # Zero-duration in the plan; typicalDuration only has to exist for scoring
        # not to reject the activity type.
        for act_type, duration in [("freight_hub", "01:00:00"),
                                   ("freight_locker", "00:01:00"),
                                   ("freight_delivery", "08:00:00")]:
            if act_type not in existing_types:
                ps = ET.SubElement(container, "parameterset")
                ps.set("type", "activityParams")
                ET.SubElement(ps, "param", name="activityType", value=act_type)
                ET.SubElement(ps, "param", name="typicalDuration", value=duration)

        if preset.add_freight_mode:
            existing_modes = {
                p.get("value")
                for ps in scoring_mod.iter("parameterset")
                if ps.get("type") == "modeParams"
                for p in ps.iter("param")
                if p.get("name") == "mode"
            }
            if "freight" not in existing_modes:
                ps = ET.SubElement(container, "parameterset")
                ps.set("type", "modeParams")
                ET.SubElement(ps, "param", name="mode", value="freight")
                ET.SubElement(ps, "param", name="constant", value="0.0")
                ET.SubElement(ps, "param", name="marginalUtilityOfTraveling_util_hr", value="-6.0")
                ET.SubElement(ps, "param", name="monetaryDistanceRate", value="-0.00015")

    # ── Emissions module (HBEFA 2020 average factors) ─────────────────────
    # Paths relative to generated/ dir → ../ ; the CSVs must sit in the
    # scenario directory (toy: already there; rotterdam: copied from toy).
    em_mod = _get_module_exact(root, "emissions")
    if em_mod is None:
        em_mod = ET.SubElement(root, "module")
        em_mod.set("name", "emissions")
    _set_param(em_mod, "averageFleetWarmEmissionFactorsFile",
               "../sample_41_EFA_HOT_vehcat_2020average.csv")
    _set_param(em_mod, "averageFleetColdEmissionFactorsFile",
               "../sample_41_EFA_ColdStart_vehcat_2020average.csv")
    _set_param(em_mod, "detailedVsAverageLookupBehavior", "directlyTryAverageTable")
    _set_param(em_mod, "nonScenarioVehicles", "ignore")
    # The factor set shipped with this scenario holds TWO traffic situations, not
    # four. MATSim's default lookup (AverageSpeed) picks a single situation, which
    # with two rows degenerates into a step at 12.49 km/h: above it every speed
    # yields the same factor, so a bus slowing traffic from 30 to 20 km/h registers
    # nothing. StopAndGoFraction splits the link into a free-flow and a stop&go
    # share whose times reproduce the observed speed, and blends the same two
    # factors — a documented option of the model, not a modification.
    _set_param(em_mod, "emissionsComputationMethod", "StopAndGoFraction")
    # Rotterdam: emission events would blow the events file up to ~8 GB/run.
    # CO2 totals are aggregated by the Java Co2TotalsHandler into co2_totals.csv
    # (term_a.py reads that CSV when present).
    _set_param(em_mod, "isWritingEmissionsEvents",
               "true" if preset.write_emission_events else "false")

    # ── Vehicles: mode vehicle types (HBEFA categories live in this file) ──
    veh_mod = _get_module_exact(root, "vehicles")
    if veh_mod is None:
        veh_mod = ET.SubElement(root, "module")
        veh_mod.set("name", "vehicles")
    _set_param(veh_mod, "vehiclesFile", preset.emission_vehicles_file)

    qsim_mod = _get_module_exact(root, "qsim")
    if qsim_mod is not None:
        _set_param(qsim_mod, "vehiclesSource", "modeVehicleTypesFromVehiclesData")
        # Capacity factors must match the population sampling rate
        # (toy 100% → 1.0; Rotterdam 10% sample → 0.1).
        _set_param(qsim_mod, "flowCapacityFactor", f"{flow:.3f}")
        _set_param(qsim_mod, "storageCapacityFactor", f"{storage:.3f}")
        if preset.add_freight_mode:
            mm = None
            for p in qsim_mod.iter("param"):
                if p.get("name") == "mainMode":
                    mm = p
                    break
            if mm is not None:
                modes = [m.strip() for m in mm.get("value", "").split(",") if m.strip()]
                if "freight" not in modes:
                    modes.append("freight")
                    mm.set("value", ",".join(modes))

    # ── Routing: ensure 'freight' is in networkModes (toy only) ───────────
    if preset.add_freight_mode:
        route_mod = _get_module(root, "routing")
        if route_mod is not None:
            nm = None
            for p in route_mod.iter("param"):
                if p.get("name") == "networkModes":
                    nm = p
                    break
            if nm is not None:
                modes = [m.strip() for m in nm.get("value", "").split(",") if m.strip()]
                if "freight" not in modes:
                    modes.append("freight")
                    nm.set("value", ",".join(modes))

    return tree


# ── Main generation logic ──────────────────────────────────────────────────

def generate_all(
    preset: ScenarioPreset,
    n_freight: int | None = None,
    last_iteration: int = 10,
    generated_dir: str | None = None,
    output_base_dir: str | None = None,
    flow_capacity_factor: float | None = None,
    storage_capacity_factor: float | None = None,
    offpeak_sample_fraction: float = 0.50,
    verbose: bool = True,
) -> list[dict]:
    """
    Generate all plans files and config files for the 20-run matrix.

    Returns a list of run descriptors:
      [{alpha, congestion, seed, config_path, plans_path, output_dir}, ...]
    """
    n_freight = n_freight if n_freight is not None else preset.n_freight_units_sim
    generated_dir = generated_dir or preset.generated_dir
    output_base_dir = output_base_dir or preset.output_base_dir
    Path(generated_dir).mkdir(parents=True, exist_ok=True)

    # Plans extension follows the base plans (gz for Rotterdam)
    plans_ext = ".xml.gz" if str(preset.peak_base_plans).endswith(".gz") else ".xml"

    # Off-peak base: create on demand if missing (50% background subsample)
    offpeak_base = Path(preset.offpeak_base_plans)
    if not offpeak_base.exists():
        print(f"[generate_configs] off-peak base {offpeak_base} missing — creating "
              f"({offpeak_sample_fraction:.0%} background sample)")
        create_offpeak_base(
            preset.peak_base_plans, str(offpeak_base),
            sample_fraction=offpeak_sample_fraction,
        )

    runs = []
    for congestion in ["peak", "offpeak"]:
        base_plans = preset.peak_base_plans if congestion == "peak" else preset.offpeak_base_plans

        for alpha in ALPHA_VALUES:
            alpha_str = f"{int(alpha * 100):03d}"

            # ── Create plans file for this (alpha, congestion) ─────────────
            plans_filename = f"plans_alpha{alpha_str}_{congestion}{plans_ext}"
            plans_path = str(Path(generated_dir) / plans_filename)
            n_vans = create_plans_file(
                base_plans_path=base_plans,
                output_path=plans_path,
                alpha=alpha,
                n_freight=n_freight,
                congestion=congestion,
                verbose=verbose,
                hub_link=preset.hub_link,
                terminal_link=preset.terminal_link,
                hub_x=preset.hub_xy[0], hub_y=preset.hub_xy[1],
                terminal_x=preset.terminal_xy[0], terminal_y=preset.terminal_xy[1],
                van_mode=preset.van_mode,
                spread_minutes=preset.van_spread_minutes,
                locker_stops=preset.van_locker_stops,
            )

            seeds_for_this_run = list(SEEDS)
            if abs(alpha) < 1e-9 and BASELINE_EXTRA_SEEDS:
                seeds_for_this_run = list(SEEDS) + list(BASELINE_EXTRA_SEEDS)

            for seed in seeds_for_this_run:
                run_name = f"alpha{alpha_str}_{congestion}_seed{seed}"
                config_filename = f"config_{run_name}.xml"
                config_path = str(Path(generated_dir) / config_filename)
                out_dir = str(Path(output_base_dir) / run_name)

                plans_abs = str(Path(plans_path).resolve())
                out_dir_abs = str(Path(out_dir).resolve())

                patched = patch_config(
                    base_config_path=preset.base_config,
                    plans_file=plans_abs,
                    output_dir=out_dir_abs,
                    seed=seed,
                    last_iteration=last_iteration,
                    preset=preset,
                    flow_capacity_factor=flow_capacity_factor,
                    storage_capacity_factor=storage_capacity_factor,
                )
                ET.indent(patched, space="  ")
                _write_config_with_doctype(patched, config_path)

                runs.append({
                    "alpha": alpha,
                    "congestion": congestion,
                    "seed": seed,
                    "n_vans": n_vans,
                    "config_path": config_path,
                    "plans_path": plans_path,
                    "output_dir": out_dir,
                    "run_name": run_name,
                })

                if verbose:
                    print(f"  config: {config_filename}  vans={n_vans}  seed={seed}")

    if verbose:
        print(f"\nGenerated {len(runs)} configs in {generated_dir}/")
    return runs


# ── CLI ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent))

    p = argparse.ArgumentParser(description="Generate the 20-run MATSim config matrix")
    p.add_argument("--scenario", default="toy", choices=["toy", "rotterdam"],
                   help="Scenario preset (default: toy)")
    p.add_argument("--n-freight", type=int, default=None,
                   help="SIMULATED freight units (default: preset — toy 2000, rotterdam 470)")
    p.add_argument("--iterations", type=int, default=10,
                   help="lastIteration in each config (default: 10 for smoke-test)")
    p.add_argument("--profile", choices=["toy", "sensitivity", "thesis"], default=None,
                   help="Preset iteration counts: toy=10, sensitivity=150, thesis=300. "
                        "Overrides --iterations if given.")
    p.add_argument("--flow-capacity", type=float, default=None,
                   help="qsim flowCapacityFactor (default: preset — toy 1.0, rotterdam 0.1)")
    p.add_argument("--storage-capacity", type=float, default=None,
                   help="qsim storageCapacityFactor (default: preset)")
    p.add_argument("--generated-dir", default=None)
    p.add_argument("--output-base-dir", default=None)
    args = p.parse_args()

    preset = get_preset(args.scenario)

    profile_presets = {
        "toy":         {"iterations": 10},
        "sensitivity": {"iterations": 150},
        "thesis":      {"iterations": 300},
    }
    iterations = args.iterations
    if args.profile:
        iterations = profile_presets[args.profile]["iterations"]
        print(f"[generate_configs] profile={args.profile} -> lastIteration={iterations}")

    runs = generate_all(
        preset=preset,
        n_freight=args.n_freight,
        last_iteration=iterations,
        generated_dir=args.generated_dir,
        output_base_dir=args.output_base_dir,
        flow_capacity_factor=args.flow_capacity,
        storage_capacity_factor=args.storage_capacity,
    )
    print(f"\nRun manifest ({len(runs)} entries):")
    for r in runs:
        print(f"  {r['run_name']}  vans={r['n_vans']}")
