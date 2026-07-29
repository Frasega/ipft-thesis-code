"""
Toy SCREENING scenario generator — the fixed-variable GATE (HANDOFF §7/§8).

Generates the cheap toy warm runs that screen whether the Rotterdam sensitivity
SLICES can legitimately hold an axis fixed. This is the gate that runs FIRST:
if a swept axis interacts with a held-fixed axis on the toy, the held-fixed axis
must be CROSSED on Rotterdam instead, which changes the Rotterdam run-list.

Two axes only (Patrick-agreed scope, 2026-06-24/26):
  A) van-capacity x weight  — does the van-size effect interact with parcel weight?
       van payload {750, 1100, 1400} kg x weight {light, heavy}, held at alpha=1, peak.
       Net at alpha=1 = Term B (all baseline tours removed) - Term C. Term B reads the
       van speeds from the alpha=0 baseline, whose van COUNT is cap-specific; the
       alpha=1 scenario has zero vans (one shared zero-van run, reused for every cell).
  B) total-N x alpha        — does the demand-volume effect interact with alpha?
       N {1000, 4000} x alpha {0.5, 1.0}, held at medium weight, peak. Each N needs
       its own alpha=0 baseline. (The N=2000 mid-point + base alphas already live in
       the main warm surface; this screens the two extreme N values around it.)

All runs BRANCH from the existing frozen toy longbase (peak) — NO new longbase.
Run names carry cap/N tags so screening_analysis.py can locate the pairs.

Tare is held fixed across van types: the screening lever is consolidation CAPACITY,
not vehicle mass (declared as a sandbox simplification, HANDOFF §8).

Prereq: the peak longbase run must be DONE (output_plans in
D:/TesiOutputs/ipft_toy_longbase_peak).

Run from project root:  python python_pipeline/make_toy_screening_scenarios.py
"""
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from insert_vans import create_plans_file
from generate_configs import patch_config, _write_config_with_doctype
from parameters import (RANDOM_SEEDS, WEIGHT_REGIMES, VAN_PARCELS_PER_TOUR_MAX,
                        N_FREIGHT_UNITS_TOY, c_van)
from make_toy_warm_scenarios import frozen_plans, freeze_replanning, WARM_ITERS
from scenario_presets import get_preset

# ── Screening axes ──────────────────────────────────────────────────────────
CONGESTION = "peak"                       # both axes are held at peak
VAN_PAYLOAD_CAPS = [750, 1100, 1400]      # kg payload: Caddy / Transit Custom / Sprinter
SCREEN_WEIGHTS = ["light", "heavy"]       # the two weight extremes (axis A)
SCREEN_N = [1000, 4000]                   # demand bracket x0.5 / x2 around the 2000 base
SCREEN_ALPHAS = [0.5, 1.0]               # alpha points for axis B

OUT_BASE = "D:/TesiOutputs/ipft_toy_screening_runs"
SCREEN_PLANS_DIR = Path("D:/TesiOutputs/ipft_toy_screening_plans")

preset = get_preset("toy")
GEN = Path(preset.generated_dir)
SCREEN_PLANS_DIR.mkdir(parents=True, exist_ok=True)


def _make_plans(base: str, alpha: float, n_freight: int, n_vans: int, tag: str) -> str:
    """Write a warm-plans file (frozen background + n_vans tours) and return its path."""
    plans_path = str(SCREEN_PLANS_DIR / f"screenplans_{tag}.xml.gz")
    if not Path(plans_path).exists():
        create_plans_file(
            base_plans_path=base, output_path=plans_path, alpha=alpha,
            n_freight=n_freight, congestion=CONGESTION, verbose=False,
            hub_link=preset.hub_link, terminal_link=preset.terminal_link,
            hub_x=preset.hub_xy[0], hub_y=preset.hub_xy[1],
            terminal_x=preset.terminal_xy[0], terminal_y=preset.terminal_xy[1],
            van_mode=preset.van_mode, spread_minutes=preset.van_spread_minutes,
            n_vans_override=n_vans,
        )
    return plans_path


def _emit_config(plans_path: str, run: str, seed: int, runs: list[str]) -> None:
    out = str((Path(OUT_BASE) / run).resolve())
    tree = patch_config(
        base_config_path=preset.base_config,
        plans_file=str(Path(plans_path).resolve()),
        output_dir=out, seed=seed, last_iteration=WARM_ITERS, preset=preset,
    )
    freeze_replanning(tree)
    ET.indent(tree, space="  ")
    _write_config_with_doctype(tree, str(GEN / f"config_SCREEN_{run}.xml"))
    runs.append(run)


def main() -> None:
    base = frozen_plans(CONGESTION)
    N_base = N_FREIGHT_UNITS_TOY              # 2000 — fixed N for axis A
    runs: list[str] = []

    # ── Axis A: van-capacity x weight (held alpha=1, peak) ───────────────────
    # One shared zero-van alpha=1 run (identical events regardless of cap/weight).
    zv_plans = _make_plans(base, alpha=1.0, n_freight=N_base, n_vans=0, tag="zerovan_peak")
    for seed in RANDOM_SEEDS:
        _emit_config(zv_plans, f"screenA_zerovan_peak_seed{seed}", seed, runs)

    for cap in VAN_PAYLOAD_CAPS:
        for weight in SCREEN_WEIGHTS:
            w_kg = WEIGHT_REGIMES[weight]
            cvan = c_van(w_kg, cap, VAN_PARCELS_PER_TOUR_MAX)
            n_tours = math.ceil(N_base / cvan)         # alpha=0 baseline van count
            tag = f"A_cap{cap}_{weight}_alpha000_peak"
            plans = _make_plans(base, alpha=0.0, n_freight=N_base, n_vans=n_tours, tag=tag)
            for seed in RANDOM_SEEDS:
                _emit_config(plans, f"screen{tag}_seed{seed}", seed, runs)
            print(f"  [A] cap={cap:4d} {weight:5s} C_van={cvan:3d} -> {n_tours} baseline tours")

    # ── Axis B: total-N x alpha (held medium weight, peak, base van cap) ──────
    w_med = WEIGHT_REGIMES["medium"]
    cvan_med = c_van(w_med)                            # base capacity
    for N in SCREEN_N:
        for alpha in [0.0] + SCREEN_ALPHAS:           # 0.0 baseline + the alpha points
            astr = f"{int(alpha * 100):03d}"
            n_tours = math.ceil((1 - alpha) * N / cvan_med) if (1 - alpha) * N > 0 else 0
            tag = f"B_N{N}_medium_alpha{astr}_peak"
            plans = _make_plans(base, alpha=alpha, n_freight=N, n_vans=n_tours, tag=tag)
            for seed in RANDOM_SEEDS:
                _emit_config(plans, f"screen{tag}_seed{seed}", seed, runs)
            print(f"  [B] N={N:4d} alpha={astr} C_van={cvan_med} -> {n_tours} tours")

    print(f"\nGenerated {len(runs)} screening configs in {GEN} (config_SCREEN_*.xml). "
          f"Run with:\n"
          f"  scenario_runner.py --config-dir {preset.generated_dir} --filter SCREEN --heap 8g\n"
          f"then analyse with screening_analysis.py.")


if __name__ == "__main__":
    main()
