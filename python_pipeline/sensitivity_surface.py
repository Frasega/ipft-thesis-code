"""
Steps 5 & 6 of the pipeline — apply the weight regimes and assemble the
sensitivity surface (α × congestion × weight).

Reads the MATSim runs of a scenario preset (toy or Rotterdam; with
--per-weight-runs each weight regime has its own warm runs, because the
consolidated van count depends on the weight), runs
`run_pipeline.run_scenario` once for each (α, congestion, weight, seed),
aggregates the results, and writes:
  - results_long.csv     one row per (α, congestion, weight, seed)
  - results_mean.csv     mean ± std collapsed across seeds
  - sensitivity_surface.png    heatmap of net CO2 saving across (α, weight)

Post-processing sensitivity knobs (no new MATSim runs needed):
  --van-stop-idle {low,high,zero}   delivery-stop idle bracket
  --van-load {mean,full}            tour evaluation mass (mean = tare+payload/2)
  --recon-seed                      micro-trip reconstruction RNG seed
  --extra-dwell-s                   per-parcel freight-handling dwell (TCQSM 3-15 s)

Usage:
    python sensitivity_surface.py --scenario toy --per-weight-runs \
        --runs-dir D:/TesiOutputs/ipft_toy_warm_runs --out output/sensitivity_results_warm_perweight
    python sensitivity_surface.py --scenario rotterdam --per-weight-runs \
        --out output/sensitivity_rotterdam
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

# Ensure local imports work
sys.path.insert(0, str(Path(__file__).parent))

from parameters import (
    ALPHA_VALUES,
    RANDOM_SEEDS,
    BASELINE_EXTRA_SEEDS,
    WEIGHT_REGIMES,
    N_FREIGHT_UNITS_TOY,
    VAN_STOP_IDLE_S,
    VAN_STOP_IDLE_LOW_S,
    VAN_STOP_IDLE_HIGH_S,
)
from run_pipeline import run_scenario


CONGESTION_LEVELS = ["peak", "offpeak"]


def _find_events(run_dir: Path) -> Path | None:
    """Locate the final events file (.zst or .gz) inside a MATSim run dir.

    Handles the optional runId prefix (e.g. MRDH_10pct.output_events.xml.zst —
    the Rotterdam config sets runId, the toy does not).
    """
    if not run_dir.is_dir():
        return None
    for pattern in ("*output_events.xml.zst", "*output_events.xml.gz"):
        hits = sorted(run_dir.glob(pattern))
        if hits:
            return hits[0]
    # Fall back to the highest-numbered ITERS/it.N/<runId.>N.events.xml.{zst,gz}
    iters_dir = run_dir / "ITERS"
    if iters_dir.is_dir():
        iter_dirs = sorted(iters_dir.glob("it.*"), key=lambda p: int(p.name.split(".")[1]))
        if iter_dirs:
            top = iter_dirs[-1]
            n = top.name.split(".")[1]
            for pattern in (f"*{n}.events.xml.zst", f"*{n}.events.xml.gz"):
                hits = sorted(top.glob(pattern))
                if hits:
                    return hits[0]
    return None


def _run_name(alpha: float, congestion: str, seed: int, weight: str | None = None) -> str:
    a = int(round(alpha * 100))
    if weight is not None:
        return f"alpha{a:03d}_{congestion}_{weight}_seed{seed}"
    return f"alpha{a:03d}_{congestion}_seed{seed}"


def build_surface(
    runs_dir: str,
    network_path: str,
    n_freight: int,
    out_dir: str,
    n_pickup_stops: int = 5,
    verbose: bool = True,
    preset=None,
    per_weight: bool = False,
    van_stop_idle_s: float = VAN_STOP_IDLE_S,
    van_load_factor: float | None = None,
    recon_seed: int = 42,
    extra_dwell_per_unit_s: float | None = None,
    dwell_in_matsim: bool = False,
    resume: bool = True,
) -> pd.DataFrame:
    """
    For every (α, congestion, weight, seed) combination:
      1. Locate the corresponding MATSim run dir.
      2. Find the α=0 (baseline) run for Terms A/B.
      3. Call `run_scenario` and collect the results.

    per_weight=False (default): one run per (α, congestion, seed) is reused for all 3
    weight regimes (weight is a pure post-processing axis; the sim has a single van count).
    per_weight=True: each weight has its OWN runs (the simulated van count = consolidated
    tours, which depends on weight), so the baseline/scenario are looked up per weight and
    Term A + the van speeds reflect the right number of vans (run names carry the weight).

    Returns the long-form DataFrame (one row per individual scenario × seed).
    """
    runs_dir_path = Path(runs_dir)
    out_dir_path = Path(out_dir)
    out_dir_path.mkdir(parents=True, exist_ok=True)
    long_csv = out_dir_path / "results_long.csv"

    all_seeds = sorted(set(RANDOM_SEEDS) | set(BASELINE_EXTRA_SEEDS))

    # Resume support. A full Rotterdam sweep is ~60 event parses (hours), so it
    # must survive an interrupted laptop: every finished cell is appended to
    # results_long.csv immediately, and a re-run skips what is already there.
    # Delete results_long.csv to force a clean recomputation (which is what you
    # want after a change that moves the numbers, e.g. the V_mean correction).
    rows = []
    done: set = set()
    if resume and long_csv.exists():
        prev = pd.read_csv(long_csv)
        rows = prev.to_dict("records")
        done = {(str(r["congestion"]), str(r["weight_regime"]),
                 int(r["seed"]), round(float(r["alpha"]), 4))
                for _, r in prev.iterrows()}
        if verbose:
            print(f"[resume] {len(done)} cells already in {long_csv} — skipping them")

    preset_kwargs = {}
    if preset is not None:
        preset_kwargs = dict(
            sample_rate=preset.sample_rate,
            bus_trips_per_day=preset.bus_trips_per_day,
            transit_prefixes=preset.transit_prefixes,
            bus_id_allowlist=preset.term_c_bus_ids,
            hb_route_prefixes=preset.hb_route_prefixes,
            corridor_links_file=preset.corridor_links_file,
            bus_stop_links_file=preset.bus_stop_links_file,
        )

    KEEP = ("term_a_kg", "term_b_kg", "term_c_kg", "net_saving_kg_per_day",
            "net_robust_kg_per_day", "feasible", "binding_constraint", "hbefa_enabled",
            "term_b_proxy", "consolidated", "parcels_per_tour", "tours_baseline",
            "tours_scenario", "alpha_max", "term_a_corridor_kg",
            "corridor_delta_vehicle_hours", "corridor_speed_change_ms",
            "term_a_vans_kg", "term_a_busstop_kg",
            "vanrow_delta_vehicle_hours", "vanrow_speed_change_ms",
            "busstop_delta_vehicle_hours", "busstop_speed_change_ms",
            "dwell_in_matsim", "idle_mode", "extra_dwell_s_per_trip")

    # (regime, weight-in-run-name) pairs to iterate. per_weight → each regime has its own
    # run dirs; else a single set of runs (weight=None in the name) reused for all regimes.
    regime_specs = ([(r, r) for r in WEIGHT_REGIMES]
                    if per_weight else [(r, None) for r in WEIGHT_REGIMES])

    for congestion in CONGESTION_LEVELS:
        for seed in all_seeds:
            for regime, wtag in regime_specs:
                baseline_dir = runs_dir_path / _run_name(0.0, congestion, seed, wtag)
                baseline_events = _find_events(baseline_dir)
                if baseline_events is None:
                    if verbose:
                        print(f"[skip] no baseline events for {baseline_dir}")
                    continue

                for alpha in ALPHA_VALUES:
                    if alpha > 0 and seed not in RANDOM_SEEDS:
                        continue
                    if (congestion, regime, seed, round(alpha, 4)) in done:
                        continue
                    scenario_dir = runs_dir_path / _run_name(alpha, congestion, seed, wtag)
                    scenario_events = _find_events(scenario_dir)
                    if scenario_events is None:
                        if verbose:
                            print(f"[skip] no events for {scenario_dir}")
                        continue

                    try:
                        result = run_scenario(
                            baseline_events_path=str(baseline_events),
                            network_path=network_path,
                            scenario_events_path=str(scenario_events),
                            alpha=alpha,
                            weight_regime=regime,
                            n_freight_units=n_freight,
                            n_pickup_stops=n_pickup_stops,
                            van_stop_idle_s=van_stop_idle_s,
                            van_load_factor=van_load_factor,
                            recon_seed=recon_seed,
                            extra_dwell_per_unit_s=extra_dwell_per_unit_s,
                            dwell_in_matsim=dwell_in_matsim,
                            verbose=False,
                            **preset_kwargs,
                        )
                    except Exception as exc:
                        if verbose:
                            print(f"[error] {scenario_dir.name} / {regime}: {exc}")
                        continue

                    row = {
                        "alpha": alpha,
                        "congestion": congestion,
                        "weight_regime": regime,
                        "seed": seed,
                        **{k: v for k, v in result.items() if k in KEEP},
                    }
                    rows.append(row)
                    # Persist after EVERY cell: an interrupted sweep resumes
                    # from here instead of losing hours of parsing.
                    pd.DataFrame(rows).to_csv(long_csv, index=False)
                    if verbose:
                        print(f"  alpha={alpha:.0%} {congestion:<7s} {regime:<6s} seed={seed}  "
                              f"net={result['net_saving_kg_per_day']:+.2f} kg/day "
                              f"[{len(rows)} cells written]", flush=True)

    if not rows:
        raise RuntimeError("No scenarios processed. Check runs-dir + n-freight.")

    df = pd.DataFrame(rows)
    df.to_csv(long_csv, index=False)
    if verbose:
        print(f"\nWrote {len(df)} rows -> {long_csv}")

    # Collapse seeds → mean ± std for each (α, congestion, weight) cell
    agg_spec = dict(
        term_a_mean=("term_a_kg", "mean"),
        term_a_std=("term_a_kg", "std"),
        term_b_mean=("term_b_kg", "mean"),
        term_b_std=("term_b_kg", "std"),
        term_c_mean=("term_c_kg", "mean"),
        term_c_std=("term_c_kg", "std"),
        net_mean=("net_saving_kg_per_day", "mean"),
        net_std=("net_saving_kg_per_day", "std"),
        net_robust_mean=("net_robust_kg_per_day", "mean"),
        net_robust_std=("net_robust_kg_per_day", "std"),
        n_seeds=("seed", "count"),
        feasible=("feasible", "all"),
    )
    # Rotterdam-only columns (None/absent on toy runs)
    for col, label in [("alpha_max", "alpha_max_mean"),
                       ("term_a_corridor_kg", "term_a_corridor_mean"),
                       ("corridor_delta_vehicle_hours", "corridor_dvh_mean"),
                       ("corridor_speed_change_ms", "corridor_dspeed_mean"),
                       ("term_a_vans_kg", "term_a_vans_mean"),
                       ("term_a_busstop_kg", "term_a_busstop_mean"),
                       ("vanrow_delta_vehicle_hours", "vanrow_dvh_mean"),
                       ("busstop_delta_vehicle_hours", "busstop_dvh_mean"),
                       ("extra_dwell_s_per_trip", "extra_dwell_s_mean")]:
        if col in df.columns and df[col].notna().any():
            agg_spec[label] = (col, "mean")
            agg_spec[label.replace("_mean", "_std")] = (col, "std")

    grouped = (
        df.groupby(["alpha", "congestion", "weight_regime"], as_index=False)
          .agg(**agg_spec)
    )
    grouped.to_csv(out_dir_path / "results_mean.csv", index=False)
    if verbose:
        print(f"Wrote aggregated -> {out_dir_path/'results_mean.csv'}")

    _plot_surface(grouped, out_dir_path)
    return df


# ── Plotting ───────────────────────────────────────────────────────────────

def _plot_surface(grouped: pd.DataFrame, out_dir: Path) -> None:
    """Two heatmaps (peak / off-peak) of robust net CO2 saving across (α × weight).

    Plots net_robust = Term B − Term C (the quantities the toy resolves). Term A is
    excluded: on the toy it is seed noise (no frozen baseline), so the full net would
    be noise-dominated. The clean Term A arrives on Rotterdam (warm start, WS9).
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("[plot] matplotlib not available — skipping figure.")
        return

    value_col = "net_robust_mean" if "net_robust_mean" in grouped.columns else "net_mean"
    fig, axes = plt.subplots(1, 2, figsize=(13, 5), sharey=True)
    for ax, congestion in zip(axes, CONGESTION_LEVELS):
        sub = grouped[grouped["congestion"] == congestion]
        if sub.empty:
            ax.set_title(f"{congestion}: no data")
            continue
        pivot = sub.pivot(index="weight_regime", columns="alpha", values=value_col)
        # Order weight regimes light → heavy
        pivot = pivot.reindex(["light", "medium", "heavy"])
        im = ax.imshow(pivot.values, aspect="auto", cmap="RdBu",
                       vmin=-abs(pivot.values).max(), vmax=abs(pivot.values).max())
        ax.set_xticks(range(len(pivot.columns)))
        ax.set_xticklabels([f"{a:.0%}" for a in pivot.columns])
        ax.set_yticks(range(len(pivot.index)))
        ax.set_yticklabels(pivot.index)
        ax.set_xlabel("alpha (load success rate)")
        ax.set_title(f"{congestion}: robust net = Term B - Term C (kg/day)")
        # Annotate values
        for i, row_label in enumerate(pivot.index):
            for j, col_label in enumerate(pivot.columns):
                v = pivot.iat[i, j]
                if pd.notna(v):
                    ax.text(j, i, f"{v:+.1f}", ha="center", va="center",
                            color="black", fontsize=8)
        plt.colorbar(im, ax=ax, fraction=0.04)

    axes[0].set_ylabel("Weight regime")
    plt.tight_layout()
    out_path = out_dir / "sensitivity_surface.png"
    plt.savefig(out_path, dpi=130)
    print(f"Wrote figure -> {out_path}")


# ── CLI ────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description="Assemble the 30-scenario sensitivity surface")
    p.add_argument("--scenario", default="toy", choices=["toy", "rotterdam"],
                   help="Scenario preset: sets defaults for runs-dir, network, "
                        "n-freight, pickup stops, transit filters, F, sample rate")
    p.add_argument("--runs-dir", default=None,
                   help="Directory containing alpha*/scenario MATSim outputs "
                        "(default: preset output dir)")
    p.add_argument("--network", default=None,
                   help="Path to the network XML (default: preset network)")
    p.add_argument("--n-freight", type=int, default=None,
                   help="SIMULATED freight units (default: preset value)")
    p.add_argument("--n-pickup-stops", type=int, default=None)
    p.add_argument("--out", default="results/",
                   help="Output directory for CSVs and figures")
    p.add_argument("--per-weight-runs", action="store_true",
                   help="Each weight regime has its own MATSim runs (run names carry the "
                        "weight; sim van count = consolidated per-weight tours).")
    p.add_argument("--van-stop-idle", choices=["low", "high", "zero"], default="low",
                   help="Van delivery-stop idle bracket (HANDOFF §3.4): low=engine off+restart "
                        "~10s/stop (conservative headline), high=idle ~100s/stop (upper "
                        "sensitivity), zero=omitted (old design). Default: low.")
    p.add_argument("--van-load", choices=["mean", "full"], default="mean",
                   help="Van tour evaluation mass (HANDOFF §9): mean=tare+payload/2, the "
                        "time-averaged declining load (headline, exact since P is affine in M); "
                        "full=tare+payload, the historical departure-mass evaluation "
                        "(overstates S_van 4-10%%, kept as a sensitivity). Default: mean.")
    p.add_argument("--recon-seed", type=int, default=42,
                   help="RNG seed of the micro-trip kinematic reconstruction (Ch4 layer-3 "
                        "sweep: rerun with different seeds to verify the emission terms and "
                        "the cell ranking do not depend on the draw). Default: 42.")
    p.add_argument("--extra-dwell-s", type=float, default=None,
                   help="Per-parcel freight-handling dwell seconds (Ch4 layer-3 sweep, "
                        "TCQSM range 3-15 s). Default: parameters.EXTRA_DWELL_PER_UNIT_S.")
    p.add_argument("--no-resume", action="store_true",
                   help="Ignore an existing results_long.csv and recompute every cell. "
                        "By default the sweep RESUMES: cells already in that file are "
                        "skipped, so an interrupted run continues instead of restarting. "
                        "Note: after a change that moves the numbers you must delete the "
                        "file (or pass this) — otherwise stale cells survive.")
    p.add_argument("--dwell-in-matsim", action="store_true",
                   help="The runs simulate the freight dwell in the schedule "
                        "(make_dwell_schedules.py): Term C idle uses the MEASURED "
                        "extra standing (scenario − baseline fleet mean) instead of "
                        "the a-priori convention. ONLY for runs generated with the "
                        "dwell schedules — refuses (raises) on runs without "
                        "facility standing data.")
    args = p.parse_args()
    _idle_map = {"low": VAN_STOP_IDLE_LOW_S, "high": VAN_STOP_IDLE_HIGH_S, "zero": 0.0}
    van_stop_idle_s = _idle_map[args.van_stop_idle]
    van_load_factor = {"mean": 0.5, "full": 1.0}[args.van_load]

    from scenario_presets import get_preset
    preset = get_preset(args.scenario)

    build_surface(
        runs_dir=args.runs_dir or preset.output_base_dir,
        network_path=args.network or preset.network_file,
        n_freight=args.n_freight if args.n_freight is not None else preset.n_freight_units_sim,
        out_dir=args.out,
        n_pickup_stops=(args.n_pickup_stops if args.n_pickup_stops is not None
                        else preset.n_pickup_stops),
        preset=preset,
        per_weight=args.per_weight_runs,
        van_stop_idle_s=van_stop_idle_s,
        van_load_factor=van_load_factor,
        recon_seed=args.recon_seed,
        extra_dwell_per_unit_s=args.extra_dwell_s,
        dwell_in_matsim=args.dwell_in_matsim,
        resume=not args.no_resume,
    )


if __name__ == "__main__":
    main()
