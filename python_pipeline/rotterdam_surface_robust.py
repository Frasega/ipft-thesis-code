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
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from scenario_presets import get_preset

preset = get_preset("rotterdam")
RUNS = "D:/TesiOutputs/ipft_rotterdam_runs"
NET = preset.network_file
OUT = Path("output/sensitivity_rotterdam")
OUT.mkdir(parents=True, exist_ok=True)
LONG = OUT / "results_long.csv"

CONG = ["peak", "offpeak"]
WEIGHTS = ["light", "medium", "heavy"]
SEEDS = ["4711", "9876"]
ALPHAS = ["025", "050", "075", "100"]
KEEP = ["term_a_kg", "term_b_kg", "term_c_kg", "net_saving_kg_per_day",
        "net_robust_kg_per_day", "term_a_corridor_kg", "alpha_max"]


def events(cell: str) -> str | None:
    hits = glob.glob(f"{RUNS}/{cell}/*output_events.xml.zst")
    return hits[0] if hits else None


def already_done() -> set:
    if not LONG.exists():
        return set()
    d = pd.read_csv(LONG)
    return {(r.congestion, r.weight_regime, int(r.seed), float(r.alpha))
            for r in d.itertuples()}


def main() -> None:
    done = already_done()
    rows = []
    if LONG.exists():
        rows = pd.read_csv(LONG).to_dict("records")

    for c in CONG:
        for w in WEIGHTS:
            for s in SEEDS:
                base = events(f"alpha000_{c}_{w}_seed{s}")
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
                    if (c, w, int(s), af) in done:
                        continue
                    scen = events(f"alpha{a}_{c}_{w}_seed{s}")
                    if not scen:
                        print(f"[skip] no scenario alpha{a} {c}/{w}/seed{s}", flush=True)
                        continue
                    tmp = str(OUT / f"_tmp_{c}_{w}_{s}_{a}.json")
                    cmd = ["python", "python_pipeline/run_pipeline.py", "--scenario", "rotterdam",
                           "--baseline", base, "--scenario-events", scen, "--network", NET,
                           "--alpha", str(af), "--weight", w, "--output", tmp]
                    r = subprocess.run(cmd, capture_output=True, text=True)
                    if not os.path.exists(tmp):
                        tail = (r.stderr or r.stdout)[-160:].replace("\n", " ")
                        print(f"[error] {c}/{w}/seed{s}/alpha{a}: {tail}", flush=True)
                        continue
                    d = json.load(open(tmp))
                    os.remove(tmp)
                    row = dict(alpha=af, congestion=c, weight_regime=w, seed=int(s),
                               **{k: d.get(k) for k in KEEP})
                    rows.append(row)
                    # persist immediately (crash-resilient)
                    pd.DataFrame(rows).to_csv(LONG, index=False)
                    print(f"[ok] {c}/{w}/seed{s}/alpha{a}  "
                          f"net={d.get('net_saving_kg_per_day'):+.1f}  "
                          f"robust={d.get('net_robust_kg_per_day'):+.1f}", flush=True)

    # aggregate -> mean over seeds
    df = pd.DataFrame(rows)
    agg = dict(net_mean=("net_saving_kg_per_day", "mean"),
               net_robust_mean=("net_robust_kg_per_day", "mean"),
               term_b_mean=("term_b_kg", "mean"), term_c_mean=("term_c_kg", "mean"),
               term_a_corridor_mean=("term_a_corridor_kg", "mean"),
               n_seeds=("seed", "count"))
    g = df.groupby(["alpha", "congestion", "weight_regime"], as_index=False).agg(**agg)
    g.to_csv(OUT / "results_mean.csv", index=False)
    print(f"\nWROTE {len(g)} cells -> {OUT/'results_mean.csv'}", flush=True)


if __name__ == "__main__":
    main()
