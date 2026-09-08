"""
Robust, memory-isolated Rotterdam surface post-processing.

The single-process sensitivity_surface parses every cell in one Python process, so
its working set (baseline + scenario event DataFrames, ~6-8 GB) plus the user's open
apps thrashes or OOMs a 16.9 GB machine. This driver instead runs EACH cell in its
own run_pipeline.py subprocess: memory is released when the subprocess exits, each
cell's result is written immediately (crash-resilient), and a cell that OOMs under
RAM pressure fails alone (retried later) without losing the rest.

Run from project root:  python python_pipeline/rotterdam_surface_robust.py
Re-run is safe: cells already in results_long.csv are skipped.

Dwell-in-MATSim runs (schedules from make_dwell_schedules.py) live in their own
runs dir and get their own output dir, so the pre-dwell surface stays intact:
    python python_pipeline/rotterdam_surface_robust.py \
        --runs-dir <output_root>/ipft_rotterdam_dwell_runs \
        --out output/sensitivity_rotterdam_dwell --dwell-in-matsim
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import glob
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from scenario_presets import OUTPUT_ROOT, get_preset

DEFAULT_RUNS = str(OUTPUT_ROOT / "ipft_rotterdam_runs")
DEFAULT_OUT = "output/sensitivity_rotterdam"

CONG = ["peak", "offpeak"]
WEIGHTS = ["light", "medium", "heavy"]
SEEDS = ["4711", "9876"]
ALPHAS = ["025", "050", "075", "100"]
# The NOx block comes back nested, one entry per fleet world, because that is
# the shape the question has: "does the ratio hold for an old fleet AND a new
# one". A CSV wants scalars, so it is flattened to nox_<world>_<field>. Written
# as a loop over whatever worlds euro_factors declares, so adding a third world
# needs no change here.
NOX_FIELDS = ("s_van_nox_g_per_day", "e_pt_nox_g_per_day",
              "e_pt_nox_mass_g_per_day", "e_pt_nox_dwell_g_per_day",
              "idle_rate_g_per_h", "extra_dwell_h_per_day", "ratio",
              "links_outside_ef_speed_range", "crosscheck_ec_over_model",
              "legacy_e_pt_nox_low_g_per_day", "legacy_e_pt_nox_high_g_per_day")


def flatten_nox(nox: dict | None) -> dict:
    """nox_<world>_<field> columns, or nothing when the cell had --nox off."""
    if not nox:
        return {}
    out = {}
    for world, r in nox.items():
        for f in NOX_FIELDS:
            out[f"nox_{world}_{f}"] = r.get(f)
    return out


KEEP = ["term_a_kg", "term_b_kg", "term_b_excl_deadlock_kg", "n_deadlock_links",
        "term_c_kg", "net_saving_kg_per_day",
        "net_robust_kg_per_day", "term_a_corridor_kg", "alpha_max",
        # dwell-in-MATSim split (None on pre-dwell runs)
        "term_a_vans_kg", "term_a_busstop_kg",
        "vanrow_delta_vehicle_hours", "busstop_delta_vehicle_hours",
        "corridor_delta_vehicle_hours", "corridor_speed_change_ms",
        # Speed on the two disjoint rows: the corridor mixes van relief and bus
        # cost and averages them away, so only the split says which leg moved.
        "vanrow_speed_change_ms", "busstop_speed_change_ms",
        # Dead-link-free variants: the ones that go in the thesis.
        "corridor_delta_vehicle_hours_excl_deadlock", "corridor_speed_change_ms_excl_deadlock",
        "vanrow_delta_vehicle_hours_excl_deadlock", "vanrow_speed_change_ms_excl_deadlock",
        "busstop_delta_vehicle_hours_excl_deadlock", "busstop_speed_change_ms_excl_deadlock",
        "corridor_n_deadlock_links", "vanrow_n_deadlock_links", "busstop_n_deadlock_links",
        # Passenger travel time (scenario − baseline, positive = worse off) and
        # the schedule deviation that awaitDeparture lets through.
        "pax_n_paired", "pax_n_baseline_only", "pax_n_scenario_only",
        "pax_d_wait_s", "pax_d_invehicle_s", "pax_d_total_s", "pax_d_total_pct",
        "bus_d_arrival_delay_s", "bus_d_departure_delay_s", "bus_n_stops_with_delay",
        "idle_mode", "extra_dwell_s_per_trip", "dwell_in_matsim"]


def events(runs_dir: str, cell: str) -> str | None:
    hits = glob.glob(f"{runs_dir}/{cell}/*output_events.xml.zst")
    return hits[0] if hits else None


def already_done(long_csv: Path) -> set:
    if not long_csv.exists():
        return set()
    d = pd.read_csv(long_csv)
    return {(r.congestion, r.weight_regime, int(r.seed), float(r.alpha))
            for r in d.itertuples()}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--runs-dir", default=DEFAULT_RUNS)
    ap.add_argument("--out", default=DEFAULT_OUT)
    # A re-measurement changes the SCENARIO cells and reuses the alpha=0 runs:
    # the van departure grid and the handling time are both identical at alpha=0,
    # so re-running the baselines would burn hours to reproduce them. Point this
    # at the campaign that holds them; default is --runs-dir itself.
    ap.add_argument("--baseline-dir", default=None,
                    help="Where the alpha=0 runs live, when they are not in "
                         "--runs-dir (default: --runs-dir).")
    # Which corridor these runs belong to. It was hardcoded to line 44, and that
    # is silent when wrong: line 44 runs in EVERY Rotterdam simulation, so L87
    # runs analysed as 'rotterdam' come back with line-44 trips, stops, parcels
    # and bus ids, and nothing complains.
    ap.add_argument("--scenario", default="rotterdam",
                    help="rotterdam = line 44 (default), rotterdam_L87 = the "
                         "second corridor. Must match the runs in --runs-dir.")
    # Layer-3 knobs, passed straight through to each cell's subprocess, which
    # accepts them only on the line-44 scenario (the gate lives in
    # scenario_presets.check_sensitivity_allowed; this driver is line 44 by
    # construction, so it always passes). Use a different --out per sweep: the
    # driver RESUMES from results_long.csv, so reusing one directory would skip
    # every cell and silently reproduce the base sweep.
    ap.add_argument("--alphas", type=float, nargs="*", default=None,
                    help="Only these alpha values (e.g. --alphas 0.5 1.0). Default: all.")
    ap.add_argument("--van-stop-idle", choices=["low", "high", "zero"], default=None)
    ap.add_argument("--van-load", choices=["mean", "full"], default=None)
    ap.add_argument("--recon-seed", type=int, default=None)
    ap.add_argument("--extra-dwell-s", type=float, default=None)
    ap.add_argument("--deadlock-links-dir", default="scenarios/ipft_rotterdam",
                    help="Where deadlock_links.txt and deadlock_links_offpeak.txt "
                         "live. The right one is picked per congestion level and "
                         "passed to each cell; Term B is then reported twice, with "
                         "and without those links.")
    ap.add_argument("--nox", action="store_true",
                    help="Also compute NOx per cell for the two fleet worlds. "
                         "Each cell is its own subprocess, so this costs one "
                         "read of the 11 MB EMEP/EEA workbook per cell (~20 s). "
                         "The reported quantity is the van-saving / bus-cost "
                         "RATIO, and the bus side is a bracket — PIANO.md 4.2quater.")
    ap.add_argument("--jobs", type=int, default=1,
                    help="Cells to analyse at the same time (default 1, the "
                         "historical behaviour). Each cell is still its own "
                         "subprocess; this only says how many run at once. The "
                         "cost is one events parse per file and it is "
                         "single-threaded, so the limit is RAM, not CPU: size it "
                         "on the peak resident set of one cell, and leave room "
                         "for the machine.")
    ap.add_argument("--dwell-in-matsim", action="store_true",
                    help="Runs simulate the freight dwell in the schedule: Term C "
                         "idle uses the measured extra standing (scenario - baseline).")
    args = ap.parse_args()

    preset = get_preset(args.scenario)
    NET = preset.network_file
    RUNS = args.runs_dir
    BASE_RUNS = args.baseline_dir or RUNS
    OUT = Path(args.out)
    OUT.mkdir(parents=True, exist_ok=True)
    LONG = OUT / "results_long.csv"

    done = already_done(LONG)
    rows = []
    tasks: list = []      # one entry per cell to analyse; filled by the loops below
    if LONG.exists():
        rows = pd.read_csv(LONG).to_dict("records")

    for c in CONG:
        for w in WEIGHTS:
            for s in SEEDS:
                base = events(BASE_RUNS, f"alpha000_{c}_{w}_seed{s}")
                if not base:
                    print(f"[skip] no baseline {c}/{w}/seed{s}", flush=True)
                    continue
                # alpha=0 baseline row (net 0 by definition)
                if (c, w, int(s), 0.0) not in done:
                    rows.append(dict(alpha=0.0, congestion=c, weight_regime=w, seed=int(s),
                                     term_a_kg=0.0, term_b_kg=0.0, term_c_kg=0.0,
                                     net_saving_kg_per_day=0.0, net_robust_kg_per_day=0.0))
                for a in ALPHAS:
                    af = int(a) / 100
                    # --alphas: a Layer-3 sweep costs one full surface (~6.5 h),
                    # so it is usually run only where the headline lives.
                    if args.alphas and af not in args.alphas:
                        continue
                    if (c, w, int(s), af) in done:
                        continue
                    scen = events(RUNS, f"alpha{a}_{c}_{w}_seed{s}")
                    if not scen:
                        print(f"[skip] no scenario alpha{a} {c}/{w}/seed{s}", flush=True)
                        continue
                    tmp = str(OUT / f"_tmp_{c}_{w}_{s}_{a}.json")
                    cmd = ["python", "python_pipeline/run_pipeline.py",
                           "--scenario", args.scenario,
                           "--baseline", base, "--scenario-events", scen, "--network", NET,
                           "--alpha", str(af), "--weight", w, "--output", tmp]
                    if args.dwell_in_matsim:
                        cmd.append("--dwell-in-matsim")
                    if args.nox:
                        cmd.append("--nox")
                    dl = (Path(args.deadlock_links_dir) /
                          ("deadlock_links.txt" if c == "peak"
                           else "deadlock_links_offpeak.txt"))
                    if dl.exists():
                        cmd += ["--deadlock-links", str(dl)]
                    for flag, val in (("--van-stop-idle", args.van_stop_idle),
                                      ("--van-load", args.van_load),
                                      ("--recon-seed", args.recon_seed),
                                      ("--extra-dwell-s", args.extra_dwell_s)):
                        if val is not None:
                            cmd += [flag, str(val)]
                    tasks.append((c, w, s, a, af, tmp, cmd))

    # ── Run the cells, --jobs at a time ────────────────────────────────────
    # Threads, not processes: the work happens in the subprocess, and
    # subprocess.run releases the GIL while it waits. Rows are written as they
    # land, so a crash costs the cells still in flight and nothing else.
    write_lock = threading.Lock()

    def run_cell(task):
        _c, _w, _s, _a, _af, tmp, cmd = task
        r = subprocess.run(cmd, capture_output=True, text=True)
        if not os.path.exists(tmp):
            return task, None, (r.stderr or r.stdout)[-160:].replace(chr(10), " ")
        d = json.load(open(tmp))
        os.remove(tmp)
        return task, d, None

    print(f"[driver] {len(tasks)} cells to analyse, {args.jobs} at a time", flush=True)
    n_ok = n_err = 0
    with cf.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        futures = [pool.submit(run_cell, t) for t in tasks]
        for fut in cf.as_completed(futures):
            task, d, err = fut.result()
            c, w, s, a, af, _tmp, _cmd = task
            if err is not None:
                n_err += 1
                print(f"[error] {c}/{w}/seed{s}/alpha{a}: {err}", flush=True)
                continue
            row = dict(alpha=af, congestion=c, weight_regime=w, seed=int(s),
                       **{k: d.get(k) for k in KEEP})
            row.update(flatten_nox(d.get("nox")))
            with write_lock:
                rows.append(row)
                n_ok += 1
                # persist immediately (crash-resilient)
                pd.DataFrame(rows).to_csv(LONG, index=False)
                print(f"[ok {n_ok}/{len(tasks)}] {c}/{w}/seed{s}/alpha{a}  "
                      f"net={d.get('net_saving_kg_per_day'):+.1f}  "
                      f"robust={d.get('net_robust_kg_per_day'):+.1f}", flush=True)
    if n_err:
        print(f"[driver] {n_err} cells failed and are NOT in the CSV — re-run the "
              f"same command, finished cells are skipped", flush=True)

    # aggregate -> mean over seeds
    df = pd.DataFrame(rows)
    agg = dict(net_mean=("net_saving_kg_per_day", "mean"),
               net_robust_mean=("net_robust_kg_per_day", "mean"),
               term_b_mean=("term_b_kg", "mean"), term_c_mean=("term_c_kg", "mean"),
               term_a_corridor_mean=("term_a_corridor_kg", "mean"),
               n_seeds=("seed", "count"))
    for col, label in [("term_a_vans_kg", "term_a_vans_mean"),
                       ("term_a_busstop_kg", "term_a_busstop_mean"),
                       ("vanrow_delta_vehicle_hours", "vanrow_dvh_mean"),
                       ("busstop_delta_vehicle_hours", "busstop_dvh_mean"),
                       ("extra_dwell_s_per_trip", "extra_dwell_s_mean")]:
        if col in df.columns and df[col].notna().any():
            agg[label] = (col, "mean")
    # Every NOx column averages over seeds like the rest. The two RATIO columns
    # are averaged as ratios, which is not the ratio of the averages — with two
    # seeds the difference is immaterial, and the per-seed values stay in
    # results_long.csv for anyone who wants to check.
    for col in [c for c in df.columns if c.startswith("nox_")]:
        if df[col].notna().any():
            agg[f"{col}_mean"] = (col, "mean")
    g = df.groupby(["alpha", "congestion", "weight_regime"], as_index=False).agg(**agg)
    g.to_csv(OUT / "results_mean.csv", index=False)
    print(f"\nWROTE {len(g)} cells -> {OUT/'results_mean.csv'}", flush=True)


if __name__ == "__main__":
    main()
