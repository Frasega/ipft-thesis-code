# Running the campaign, and checking it ran

Cheat-sheet: what launches what, in which order, and how each step is verified.

## The shape of it

```
Python  →  writes MATSim input files (plans, configs, transit schedules)
Java    →  runs the simulation, writes output_events.xml.zst
Python  →  reads the events, computes the three terms, writes the CSVs
```

Nothing is written by hand: ~90 config XMLs are cloned from a base config by script,
overwriting only seed, plans file, output dir, iterations, and the frozen strategies.

## How MATSim is launched

MATSim is a Java program. The literal command is:

```bash
java -Xmx10g -jar matsim-example-project-0.0.1-SNAPSHOT.jar run --config <config.xml>
```

I never type that. `scenario_runner.py` builds it as a subprocess: it finds the JAR and the
`java` executable, sets the heap, runs configs one by one, deletes each run's `ITERS/`
directory, and with `--skip-existing` skips runs that already have `output_events.xml.zst`
so an interrupted batch resumes.

```bash
python python_pipeline/scenario_runner.py --jar $JAR --config <one config> --heap 10g
python python_pipeline/scenario_runner.py --scenario rotterdam --filter RDWELLBLOCKING --heap 7g --skip-existing
```

`--filter` is a plain substring match on the config filename.

## How Python is launched

From the project root `matsim-example-project-master/`, always:

```bash
python python_pipeline/<script>.py [flags]
```

Root, not the `python_pipeline/` folder — the scripts import `parameters.py` and
`scenario_presets.py` as siblings and resolve paths relative to the root.
`PYTHONIOENCODING=utf-8` on Windows, or non-ASCII output crashes on cp1252.

Two constants files hold every number: `parameters.py` (physics, weights, alphas, seeds) and
`scenario_presets.py` (what is Rotterdam vs toy: network, plans, corridor, N, depots).

## Setup, once

```bash
export JAVA_HOME=/c/Users/frare/.jdks/ms-25.0.2
./mvnw.cmd package -DskipTests          # → matsim-example-project-0.0.1-SNAPSHOT.jar (~219 MB)
JAR=matsim-example-project-0.0.1-SNAPSHOT.jar
```

Rebuild after every Java change — the CO2 handlers and the idle/dwell buckets live on the Java side.

---

## 1. Equilibration — the expensive step

80 iterations, no freight, one run per demand level. Peak and off-peak cannot share an
equilibrium: the off-peak population is half the size.

```bash
python python_pipeline/scenario_runner.py --jar $JAR \
  --config scenarios/ipft_rotterdam/generated/config_LONGBASE_peak_seed4711.xml --heap 10g
python python_pipeline/scenario_runner.py --jar $JAR \
  --config scenarios/ipft_rotterdam/generated/config_LONGBASE_offpeak_seed4711.xml --heap 10g
```

5–7 min/iteration → 7–9 h each. Output on `D:/TesiOutputs/ipft_rotterdam_longbase[_offpeak]/`.

**Check.** Mode choice is off in Rotterdam, so convergence is read from total network
vehicle-hours: stable within a fraction of a per cent over the final iterations.
`plot_convergence.py` draws that curve from the run's own `traveldistancestats`/`scorestats`.
`longbase_corridor_check.py` prints corridor speed at iteration 80, `corridor_bus_check.py`
compares the bus's own speed against the background on each route link.
**Failure mode:** an undersized heap does not crash, it thrashes the garbage collector — the run
just gets slower and slower. 10 g for Rotterdam, 8 g for the sandbox.

## 2. Freeze the day

The equilibration ends with five plans per agent, and one warm iteration does not stop
ChangeExpBeta from re-picking among them — inserting a different number of vans shifts the
random stream and thousands of background agents choose differently.

```bash
python python_pipeline/strip_selected_plans.py \
  D:/TesiOutputs/ipft_rotterdam_longbase/MRDH_10pct.output_plans.xml.zst \
  D:/TesiOutputs/ipft_rotterdam_longbase/MRDH_10pct.output_plans_stripped.xml.gz
```

Same for the off-peak run. The generator prefers the stripped file automatically.

**Check.** The script prints plans before/after: it must be exactly one plan per person, and the
agent count must be unchanged (256,447 peak / 127,862 off-peak). Same agents, no alternatives left.

## 3. Dwell schedules

One transit schedule per load rate, freight handover written in as `minimumStopDuration`,
once per stop variant:

```bash
python python_pipeline/make_dwell_schedules.py --blocking true    # in-lane, headline
python python_pipeline/make_dwell_schedules.py --blocking false   # bay, sensitivity
```

5 alphas × 2 variants = 10 files in `scenarios/ipft_rotterdam/dwell_schedules/`. They cover the
whole 30-cell surface, because the dwell depends on α only — not on weight, not on demand level.

**Check.** The script prints the stamped seconds per stop against the table in the thesis
(40 s/stop, 320 s/trip at α=1) and touches only the 9 H→B facilities of line 44; the rest of the
city keeps its own schedule.

## 4. Generate the warm configs

```bash
python python_pipeline/make_rotterdam_warm_scenarios.py --dwell-tag blocking
python python_pipeline/make_rotterdam_warm_scenarios.py --dwell-tag bay
```

30 configs each — 5 α × 2 demand levels × 3 weight regimes — branching from the frozen plans,
with the consolidated van tours injected by `insert_vans.py` and all innovation removed
(no ReRoute, no TimeAllocationMutator). Named `config_RDWELLBLOCKING_*` / `config_RDWELLBAY_*`,
writing to separate output trees. `--congestion peak` does half the surface first.

**Check.** The generator refuses to fall back to the plain schedule if a dwell file is missing —
loudly, because a run that silently lost its dwell would look valid and report the wrong bus cost.
The α=0 baseline gets the same variant schedule (dwell 0 s, `isBlocking` identical), so the
passenger-side blocking cancels in baseline − scenario and only the freight seconds are measured.

## 5. Run the surface

```bash
python python_pipeline/scenario_runner.py --scenario rotterdam --filter RDWELLBLOCKING --heap 7g --skip-existing
python python_pipeline/scenario_runner.py --scenario rotterdam --filter RDWELLBAY --heap 7g --skip-existing
```

One MATSim iteration per cell. `RDWELL` alone would match both variants (120 runs).

**Check.** No `stuckAndAbort` event in the freight sub-population — a van that fails to finish its
tour biases the van-removal saving downward. None occurred in any accepted run; a run that failed
this, or exited abnormally, was excluded and repeated.

## 6. Post-processing

```bash
python python_pipeline/rotterdam_surface_robust.py \
  --runs-dir D:/TesiOutputs/ipft_rotterdam_dwell_blocking_runs \
  --out output/sensitivity_rotterdam_dwell_blocking --dwell-in-matsim
```

Each cell in its own subprocess, interruptible and resumable; writes `results_long.csv` and
`results_mean.csv`. Repeat on `..._dwell_bay_runs`; blocking minus bay is the bus-blocking effect
on its own.

**Check.** `--dwell-in-matsim` tells the bus term to measure the standing from the stop events
instead of charging it a priori. Pointing it at pre-dwell runs would silently collapse the idle
component to ~0, so `test_dwell_idle_guard.py` exercises exactly that branch and no MATSim run is
needed to run it. `toy_regression_check.py` recomputes one sandbox cell against the stored CSV:
the congestion term must return identical to the hundredth of a gram, because it reads HBEFA
events no pipeline revision has touched. If it moves, something touched the vehicle filter or the
scaling.

## 7. Rings and bus-stop rows

```bash
python python_pipeline/make_khop_links.py --hops 3     # corridor, +1, +2, +3 link sets
python python_pipeline/make_bus_stop_links.py          # bus-stop set, upstream-oriented
python python_pipeline/khop_congestion_deltas.py \
  --runs-dir D:/TesiOutputs/ipft_rotterdam_dwell_blocking_runs --out output/khop_congestion_deltas.csv
python python_pipeline/busstop_row_decomp.py           # van relief vs bus cost, per row
```

No new simulation: these read the stored event files.

**Check.** Every ring figure is reported next to its own seed-to-seed noise floor, computed by
re-running the identical scenario under a second seed (4711, 9876). Anything below its noise floor
is reported as unresolved — never as zero, never as a result.

## 8. Free sweeps

Anything that does not change the vehicles on the road only re-prices runs already made, through
`sensitivity_surface.py`. No simulation involved.

---

## The sandbox, same shape, one minute per run

```bash
python python_pipeline/make_toy_longbase.py
python python_pipeline/scenario_runner.py --jar $JAR --config scenarios/ipft_toy/generated/config_TOY_LONGBASE_peak.xml --heap 8g
python python_pipeline/scenario_runner.py --jar $JAR --config scenarios/ipft_toy/generated/config_TOY_LONGBASE_offpeak.xml --heap 8g
python python_pipeline/make_toy_warm_scenarios.py
python python_pipeline/scenario_runner.py --jar $JAR --config-dir scenarios/ipft_toy/generated --filter WARM --heap 8g
python python_pipeline/sensitivity_surface.py --scenario toy --runs-dir D:/TesiOutputs/ipft_toy_warm_runs \
  --per-weight-runs --out output/sensitivity_results_warm_perweight
```

Sandbox convergence is stricter because mode choice is on: average score must move by less than
0.01 and mode shares by less than 0.0001 over the final five iterations
(`scorestats.csv`, `modestats.csv`).

## Checks that stand outside the campaign

| Script | What it settles |
|---|---|
| `validate_van_consumption.py` | Van emission model vs the official Ford Transit WLTP figure: 6.45 vs ~6.85 L/100 km, within ~6%, no calibration multiplier |
| `bus_maxload_check.py` | Max simultaneous passengers on the real line-44 buses — confirms the 1,000 kg cargo allowance binds before the gross weight |
| `test_dwell_idle_guard.py` | The measured-vs-a-priori idle branch, without needing a run |
| `toy_regression_check.py` | One sandbox cell recomputed against the stored CSV |
| `smoke_post_check.py` | Whole post-processing on a proxy run where baseline = scenario, so every delta must be 0 |

## Three gates every accepted run passes

1. The equilibration it branches from is converged.
2. No `stuckAndAbort` in the freight sub-population.
3. The freight dwell is present in the run that claims to have it — both the generator and the
   bus term refuse to fall back silently.

## Practical

- Everything heavy on `D:/TesiOutputs`, outside the OneDrive-synced tree.
- Rotterdam event files ~560 MB compressed: streamed, never fully parsed.
- Full regeneration of the campaign ≈ two days of compute.
