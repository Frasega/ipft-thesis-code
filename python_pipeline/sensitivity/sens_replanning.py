"""
Sensitivity 1 of 3 — does the result survive letting the travellers re-plan?

WHAT IS BEING ASKED. Every cell of the headline surface is a FROZEN day: the
background keeps the routes, departure times and modes of the equilibrium it was
handed, one iteration runs, and only plan selection is left alive. That is what
makes the baseline-minus-scenario difference the deterministic physical effect of
the freight and nothing else (Chapter 4, sec:method-warmstart). The obvious
objection is that a real population would re-arrange itself around the freed road
space, and that the re-arrangement might eat the saving. This battery answers it
by running the same two worlds with re-planning switched back on.

WHY IT NEEDS ITS OWN RUNS, AND MORE SEEDS. In a single iteration no agent has room
to re-arrange anything, so re-planning is not a flag to flip on the existing runs:
it needs real iterations. And the noise floor cannot be inherited from the frozen
day. Chapter 4 (sec:method-noise) states the expectation up front: with re-planning
active the seed-to-seed spread should WIDEN, most of all on the outer rings, because
an agent that re-routes carries its congestion onto a neighbouring street and which
agents react depends on the seed. A floor needs more than two points, hence three
seeds.

THE DESIGN, as declared in tab:campaign: alpha in {0, 1}, three seeds, 20
iterations, peak, medium parcels. Six runs.

WHAT THIS SCRIPT DOES NOT CHANGE. Nothing in the shared pipeline. The plans are the
HEADLINE campaign's own files, read from where they already are; the schedules are
the base dwell schedules; and the only difference in the config is that
`freeze_replanning` is not applied and lastIteration is 20 instead of 1. No number
in the thesis can move because of this script.

WHO ACTUALLY RE-PLANS. The background, and only the background. patch_config gives
the 'freight' subpopulation ChangeExpBeta alone and every van carries a single
plan, so no van ever re-routes or shifts its departure. That is the intended
counterfactual: the freight operation is exogenous, the travellers are not.

ONE THING TO WRITE DOWN NEXT TO THE RESULT. The base config sets
fractionOfIterationsToDisableInnovation = 0.8, so over 20 iterations innovation
runs for 16 and the last 4 are pure selection. That is the standard MATSim
pattern, not a choice made here, but it belongs beside the iteration count.

Usage, from the project root:

    python python_pipeline/sensitivity/sens_replanning.py --prepare
    # ... paste the printed scenario_runner command into the nohup queue ...
    python python_pipeline/sensitivity/sens_replanning.py --analyse
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import _common as C
from parameters import WEIGHT_REGIMES, c_van
from scenario_presets import get_preset

CAMP = C.Campaign(tag="RPLAN20", name="replanning")

ALPHAS = [0.0, 1.0]
# 4711 and 9876 are the headline surface's own seeds, so those two cells can be
# compared frozen-against-replanning as a PAIR. 1234 exists only on the
# re-planning side and is what turns a spread into a floor.
SEEDS = [4711, 9876, 1234]
PAIRED_SEEDS = [4711, 9876]
CONGESTION = "peak"
WEIGHT = "medium"
ITERATIONS = 20


def cell_name(alpha: float, seed: int) -> str:
    return f"alpha{int(round(alpha * 100)):03d}_{CONGESTION}_{WEIGHT}_seed{seed}"


def schedule_for(alpha: float) -> Path:
    astr = f"{int(round(alpha * 100)):03d}"
    return C.DWELL_SCHEDULES / f"ptSchedule_dwell_alpha{astr}_blocking.xml.gz"


# ── prepare ────────────────────────────────────────────────────────────────

def prepare(heap: str) -> None:
    preset = get_preset("rotterdam")
    weight_kg = WEIGHT_REGIMES[WEIGHT]
    cvan = c_van(weight_kg)
    n_freight = preset.n_freight_units_sim

    print(f"re-planning battery — {CONGESTION}, {WEIGHT} parcels, "
          f"{ITERATIONS} iterations, seeds {SEEDS}")
    print(f"line {preset.transit_line_id}, F={preset.bus_trips_per_day}, "
          f"N_sim={n_freight}, C_van={cvan}")

    written = []
    for alpha in ALPHAS:
        print(f"\n  alpha={alpha:.2f}")
        plans = C.reuse_headline_plans(alpha, CONGESTION, WEIGHT)
        print(f"    [plans] {plans.parent.name}/{plans.name}")

        sched = schedule_for(alpha)
        if not sched.exists():
            raise C.CheckFailed(f"{sched} not found — the base dwell schedules are "
                                f"the input this battery reuses.")
        dwell_vals, n_stamped = C.schedule_dwell_seconds(sched)
        # The dwell the schedule must carry, computed here from the formula rather
        # than trusted: 10 s of stop overhead plus 5 s per parcel handed over,
        # with the parcels per stop being alpha * N_real / F / stops.
        units = alpha * preset.n_freight_units_real / preset.bus_trips_per_day / preset.n_pickup_stops
        want = int(round(10.0 + 5.0 * units)) if alpha > 0 else 0
        C.check("schedule dwell [s/stop]",
                (sorted(dwell_vals)[0] if dwell_vals else 0), want)
        C.check("stops stamped", n_stamped, 0 if alpha == 0 else 64)

        # Reported, not checked: at alpha=1 there are no vans left, so the tour
        # count is zero on the scenario side and the whole van saving is the
        # baseline's tours.
        n_tours = math.ceil((1 - alpha) * n_freight / cvan) if (1 - alpha) * n_freight > 0 else 0
        print(f"    [vans]  {n_tours} van tours in this world")

        for seed in SEEDS:
            cell = cell_name(alpha, seed)
            cfg = C.write_config(CAMP, preset, cell, plans, sched, seed,
                                 iterations=ITERATIONS, frozen=False)
            written.append(cfg)

    # ── read one config back: the campaign is only as good as what it wrote ──
    print("\n  read-back of the first config written")
    facts = C.config_facts(written[0])
    C.check("lastIteration", facts["last_iteration"], ITERATIONS)
    C.check("background strategies", facts["strategies"].get("(background)"),
            ["ChangeExpBeta", "ReRoute", "TimeAllocationMutator"])
    C.check("freight strategies", facts["strategies"].get("freight"), ["ChangeExpBeta"])
    C.check("schedule in config", Path(facts["schedule"]).name, schedule_for(ALPHAS[0]).name)
    C.check("configs written", len(written), len(ALPHAS) * len(SEEDS))

    C.announce(CAMP, len(written), heap)
    print(f"expect ~3 h per run: {len(written)} runs ~ {3 * len(written)} h.")
    print("Then:  python python_pipeline/sensitivity/sens_replanning.py --analyse")


# ── analyse ────────────────────────────────────────────────────────────────

def analyse() -> None:
    import pandas as pd

    rows = []
    print(f"reading {CAMP.runs_dir}")
    for seed in SEEDS:
        base = C.find_events(CAMP.runs_dir, cell_name(0.0, seed))
        scen = C.find_events(CAMP.runs_dir, cell_name(1.0, seed))
        if not base or not scen:
            missing = [cell_name(a, seed) for a, p in ((0.0, base), (1.0, scen)) if not p]
            print(f"  [skip] seed {seed}: no events for {', '.join(missing)}")
            continue
        print(f"  [cell] seed {seed} — parsing both runs", flush=True)
        res = C.run_cell(base, scen, alpha=1.0, weight=WEIGHT, congestion=CONGESTION)
        row = dict(variant="replanning_on", seed=seed, alpha=1.0,
                   congestion=CONGESTION, weight_regime=WEIGHT,
                   iterations=ITERATIONS,
                   term_a_kg=res.get("term_a_kg"),
                   term_b_kg=res.get("term_b_kg"),
                   term_b_excl_deadlock_kg=res.get("term_b_excl_deadlock_kg"),
                   term_c_kg=res.get("term_c_kg"),
                   net_robust_kg_per_day=res.get("net_robust_kg_per_day"),
                   term_a_vans_kg=res.get("term_a_vans_kg"),
                   term_a_busstop_kg=res.get("term_a_busstop_kg"),
                   vanrow_delta_vehicle_hours=res.get("vanrow_delta_vehicle_hours"),
                   busstop_delta_vehicle_hours=res.get("busstop_delta_vehicle_hours"),
                   extra_dwell_s_per_trip=res.get("extra_dwell_s_per_trip"))
        rows.append(row)
        print(f"    net_robust = {row['net_robust_kg_per_day']:+.1f} kg/day", flush=True)

    if not rows:
        raise C.CheckFailed(f"no finished cell found in {CAMP.runs_dir} — "
                            f"has the batch been run?")

    # The frozen side is READ from the published surface, not recomputed: those
    # are the numbers the thesis carries, and re-deriving them here would only
    # introduce a way for the two to disagree.
    for seed in PAIRED_SEEDS:
        ref = C.headline_row(1.0, CONGESTION, WEIGHT, seed)
        rows.append(dict(variant="replanning_off", seed=seed, alpha=1.0,
                         congestion=CONGESTION, weight_regime=WEIGHT, iterations=1,
                         **{k: ref.get(k) for k in
                            ("term_a_kg", "term_b_kg", "term_b_excl_deadlock_kg",
                             "term_c_kg", "net_robust_kg_per_day", "term_a_vans_kg",
                             "term_a_busstop_kg", "vanrow_delta_vehicle_hours",
                             "busstop_delta_vehicle_hours", "extra_dwell_s_per_trip")}))

    df = pd.DataFrame(rows)
    CAMP.out_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(CAMP.out_csv, index=False)
    verdict(df)
    print(f"\nsaved -> {CAMP.out_csv}")


def verdict(df) -> None:
    on = df[df.variant == "replanning_on"].set_index("seed")
    off = df[df.variant == "replanning_off"].set_index("seed")

    print("\n=== re-planning on against the frozen day, alpha=1, peak, medium ===")
    print(f"{'quantity':<28} {'off (2 seeds)':>14} {'on (3 seeds)':>14} "
          f"{'difference':>12} {'floor (on)':>11}  verdict")
    for key, label in (("net_robust_kg_per_day", "net robust [kg/day]"),
                       ("term_b_excl_deadlock_kg", "S_van excl. deadlock [kg]"),
                       ("term_c_kg", "E_PT [kg/day]"),
                       ("term_a_vans_kg", "van row, CO2 [kg]"),
                       ("term_a_busstop_kg", "bus-stop row, CO2 [kg]"),
                       ("vanrow_delta_vehicle_hours", "van row [veh-h]"),
                       ("busstop_delta_vehicle_hours", "bus-stop row [veh-h]")):
        if key not in df.columns:
            continue
        on_vals = [v for v in on[key].tolist() if v is not None and v == v]
        off_vals = [v for v in off[key].tolist() if v is not None and v == v]
        if not on_vals or not off_vals:
            continue
        on_mean = sum(on_vals) / len(on_vals)
        off_mean = sum(off_vals) / len(off_vals)
        diff = on_mean - off_mean
        floor = C.seed_floor(on_vals)
        # The rule of Chapter 4: an effect smaller than the floor of its own set
        # is reported as unresolved, never as zero and never as a result.
        if floor != floor:
            mark = "no floor"
        elif floor == 0.0:
            # Tutti i semi danno lo stesso valore: succede davvero, per una
            # grandezza che non dipende dal seme (E_PT quando la sosta misurata
            # coincide, per esempio). Non c'e' un pavimento contro cui misurare,
            # e dividere per zero qui faceva morire la verdict() DOPO che il CSV
            # era gia' stato scritto — cioe' il caso peggiore: dati salvati,
            # lettura persa.
            mark = "floor is zero (i semi coincidono) -> nessun rapporto"
        elif abs(diff) <= floor:
            mark = "inside the floor -> unresolved"
        else:
            mark = f"outside the floor ({abs(diff) / floor:.1f}x)"
        print(f"{label:<28} {off_mean:>14.2f} {on_mean:>14.2f} "
              f"{diff:>+12.2f} {floor:>11.2f}  {mark}")

    # The prediction Chapter 4 makes about the floors themselves.
    print("\n=== does the seed spread widen, as Chapter 4 predicts? ===")
    for key, label in (("net_robust_kg_per_day", "net robust"),
                       ("term_a_vans_kg", "van row, CO2"),
                       ("term_a_busstop_kg", "bus-stop row, CO2")):
        if key not in df.columns:
            continue
        on_vals = [v for v in on[key].tolist() if v is not None and v == v]
        off_vals = [v for v in off[key].tolist() if v is not None and v == v]
        if len(on_vals) < 2 or len(off_vals) < 2:
            continue
        f_on, f_off = C.seed_floor(on_vals), C.seed_floor(off_vals)
        ratio = f_on / f_off if f_off else float("inf")
        print(f"  {label:<20} floor off {f_off:8.2f}  ->  on {f_on:8.2f}   "
              f"({ratio:.1f}x)   {'widened' if ratio > 1 else 'did NOT widen'}")
    print("\nNote: the off floor is 2 seeds and the on floor is 3, so the two are "
          "not measured on the same number of points — report both counts.")

    # The mean-against-mean comparison above mixes two things, because the third
    # seed exists only on the re-planning side: the effect of re-planning and the
    # effect of averaging over a different set of seeds. Paired on the two seeds
    # that exist in BOTH, the seed composition cancels and what is left is the
    # effect. This is the comparison to report; the means are context.
    print("\n=== paired on the seeds that exist in both worlds ===")
    print(f"{'quantity':<28} {'seed':>6} {'off':>10} {'on':>10} {'difference':>12}")
    for key, label in (("net_robust_kg_per_day", "net robust [kg/day]"),
                       ("term_b_excl_deadlock_kg", "S_van excl. deadlock [kg]"),
                       ("term_c_kg", "E_PT [kg/day]"),
                       ("term_a_vans_kg", "van row, CO2 [kg]"),
                       ("term_a_busstop_kg", "bus-stop row, CO2 [kg]")):
        if key not in df.columns:
            continue
        shown = False
        for seed in PAIRED_SEEDS:
            if seed not in on.index or seed not in off.index:
                continue
            a, b = off.loc[seed, key], on.loc[seed, key]
            if a is None or b is None or a != a or b != b:
                continue
            print(f"{label if not shown else '':<28} {seed:>6} {a:>10.2f} "
                  f"{b:>10.2f} {b - a:>+12.2f}")
            shown = True


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--prepare", action="store_true",
                   help="write the configs and print the scenario_runner command")
    g.add_argument("--analyse", action="store_true",
                   help="read the finished runs, write the CSV, print the verdict")
    ap.add_argument("--heap", default="7g", help="JVM heap for the printed command")
    args = ap.parse_args()
    C.use_project_root()
    try:
        if args.prepare:
            prepare(args.heap)
        else:
            analyse()
    except C.CheckFailed as exc:
        C.fail(exc)


if __name__ == "__main__":
    main()
