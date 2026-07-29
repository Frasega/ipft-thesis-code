"""
Toy warm-start, step 2 — generate the warm-started scenario configs + plans.

Each scenario starts from the FROZEN longbase equilibrium plans (background mode/route
equilibrated, ZERO vans) and adds the (1-alpha)*N backup vans on top. Two changes vs the
from-scratch configs make Term A clean (decision #5, HANDOFF §9/§14b):
  - inputPlansFile = the frozen longbase output_plans  → every scenario shares the same
    background starting point, so seed noise cancels in the baseline-minus-scenario delta;
  - SubtourModeChoice REMOVED → mode choice is frozen (no induced demand) → Term A is the
    pure physical road-space congestion-relief effect of the vans (the Rotterdam framing).
ReRoute is KEPT (route choice on, as on Rotterdam). Few iterations suffice from the frozen
start (the vans perturb the network immediately).

Prereq: the two longbase runs must be DONE (output_plans in
D:/TesiOutputs/ipft_toy_longbase_{peak,offpeak}).

Run from project root:  python python_pipeline/make_toy_warm_scenarios.py
"""
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from insert_vans import create_plans_file
from generate_configs import patch_config, _write_config_with_doctype
from parameters import ALPHA_VALUES, RANDOM_SEEDS, WEIGHT_REGIMES, c_van
from scenario_presets import get_preset

WARM_ITERS = 1                        # frozen routes -> just execute (no innovation)
OUT_BASE = "D:/TesiOutputs/ipft_toy_warm_runs"
# Warm plans = frozen plans (510 MB) + vans → big; write them GZIPPED on D:, NOT in the
# OneDrive-synced generated/ dir (configs themselves are small and stay in generated/).
WARM_PLANS_DIR = Path("D:/TesiOutputs/ipft_toy_warm_plans")

preset = get_preset("toy")
GEN = Path(preset.generated_dir)
WARM_PLANS_DIR.mkdir(parents=True, exist_ok=True)


def frozen_plans(congestion: str) -> str:
    """Return the frozen longbase plans path. MATSim writes .zst (insert_vans needs
    .xml/.gz) → auto-decompress the .zst to .xml once."""
    d = Path(f"D:/TesiOutputs/ipft_toy_longbase_{congestion}")
    for name in ("output_plans.xml", "output_plans.xml.gz"):
        if (d / name).exists():
            return str(d / name)
    zst = d / "output_plans.xml.zst"
    if zst.exists():
        import zstandard
        out = d / "output_plans.xml"
        with open(zst, "rb") as f, open(out, "wb") as o:
            zstandard.ZstdDecompressor().copy_stream(f, o)
        return str(out)
    raise FileNotFoundError(
        f"longbase output_plans not found in {d} — run config_TOY_LONGBASE_{congestion}.xml first."
    )


def freeze_replanning(tree: ET.ElementTree) -> ET.ElementTree:
    """Freeze ALL background innovation: remove SubtourModeChoice AND ReRoute
    (keep only ChangeExpBeta = plan selection). The background then keeps its
    frozen equilibrium routes/modes; adding the vans is the ONLY change between
    baseline and scenario, so Term A = the deterministic physical road-space
    effect of the vans (decision #5: clean bounded UPPER estimate). Without this
    (ReRoute left on) seed-to-seed routing divergence swamps the small Term A
    signal (observed: term_a_std up to ±181 kg)."""
    root = tree.getroot()
    FROZEN = {"SubtourModeChoice", "ReRoute"}
    for mod in root.iter("module"):
        if mod.get("name") in ("replanning", "strategy"):
            for ps in list(mod.findall("parameterset")):
                if any(p.get("name") == "strategyName" and p.get("value") in FROZEN
                       for p in ps.iter("param")):
                    mod.remove(ps)
    return tree


def main() -> None:
    N = preset.n_freight_units_sim          # parcels (2000 on the toy)
    runs = []
    for congestion in ("peak", "offpeak"):
        base = frozen_plans(congestion)
        for weight_regime, weight_kg in WEIGHT_REGIMES.items():
            cvan = c_van(weight_kg)          # parcels/tour: light 150, medium 110, heavy 44
            for alpha in ALPHA_VALUES:
                astr = f"{int(alpha * 100):03d}"
                # CONSOLIDATED van count = number of tours on the road (NOT one per parcel).
                n_tours = math.ceil((1 - alpha) * N / cvan) if (1 - alpha) * N > 0 else 0
                plans_path = str(WARM_PLANS_DIR /
                                 f"warmplans_alpha{astr}_{congestion}_{weight_regime}.xml.gz")
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
                    out = str((Path(OUT_BASE) / run).resolve())
                    tree = patch_config(
                        base_config_path=preset.base_config,
                        plans_file=str(Path(plans_path).resolve()),
                        output_dir=out, seed=seed, last_iteration=WARM_ITERS, preset=preset,
                    )
                    freeze_replanning(tree)
                    ET.indent(tree, space="  ")
                    cfg = str(GEN / f"config_WARM_{run}.xml")
                    _write_config_with_doctype(tree, cfg)
                    runs.append(run)
                print(f"  alpha={astr} {congestion:7s} {weight_regime:6s} "
                      f"(C_van={cvan}) -> {n_tours} van-tours")
    print(f"\nGenerated {len(runs)} warm configs (per-weight consolidated van counts). "
          f"Run with scenario_runner --filter WARM, then "
          f"sensitivity_surface --runs-dir {OUT_BASE} --per-weight-runs.")


if __name__ == "__main__":
    main()
