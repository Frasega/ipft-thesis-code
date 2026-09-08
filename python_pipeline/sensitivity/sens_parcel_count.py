"""
Sensitivity 2 of 3 — how does the balance change with the number of parcels?

WHAT IS BEING ASKED, and it is not "is it linear". Most of the saving is linear in
N by construction: more parcels, proportionally more van tours removed. The
question worth asking is where it STOPS being linear, and there are three places:

  1. the ceil() on the tour count is a staircase, not a line. With four to nine
     tours on this corridor one step is a seventh to a quarter of the fleet;
  2. E_PT has a part that does not depend on N at all. The dwell written into the
     schedule is 10 s of stop overhead PLUS 5 s per parcel, and the overhead is
     charged on all 98 departures whatever N is. So the bus-side cost per parcel
     FALLS as N grows, and the net per parcel improves. That is an economy of
     scale, and it is the reading that matters for policy: IPFT pays where the
     catchment is dense — which is also the key to the comparison with line 87,
     whose catchment is smaller;
  3. the bus capacity, which on this design never binds — see below.

THE DESIGN, as declared in tab:campaign: two values of N crossed with
alpha in {0, 0.5}, two seeds, peak, medium parcels. Eight runs. N is varied at
alpha in {0, 0.5} rather than at full load because at alpha=1 there is no van left
for N to act on except through the baseline.

WHY THE CAPACITY DOES NOT BIND HERE, and why that has to be said carefully.
Chapter 4 (sec:method-capacity) declares a rebound: freight that does not fit is
returned to the van, alpha_eff = min(alpha, alpha_max). The code does NOT implement
it — feasibility.py flags a cell and term_c charges the full alpha*N*w regardless.
At alpha=0.5 that gap is harmless for every N used here: N=940 puts 48 parcels and
480 kg on a trip against a 70-parcel rack and a 1,000 kg allowance. The rack would
only start to bind above N=1372. So the honest sentence is "the cap does not bind
in this range", never "the cap does not matter".

WHAT THIS SCRIPT DOES NOT CHANGE. Nothing in the shared pipeline. N enters through
dataclasses.replace on the frozen scenario preset, which gives make_dwell_schedules
a variant preset without editing it, and run_pipeline already takes --n-freight.

THE THIRD POINT OF THE CURVE IS FREE. The base N=470 cell is computed from runs
that already exist, which serves twice: it is the middle point of the curve, and it
is the validation that the pipeline still reproduces the published surface before
any swept number is believed.

Usage, from the project root:

    python python_pipeline/sensitivity/sens_parcel_count.py --prepare
    # ... paste the printed scenario_runner commands into the nohup queue ...
    python python_pipeline/sensitivity/sens_parcel_count.py --analyse
"""

from __future__ import annotations

import argparse
import dataclasses
import math
from pathlib import Path

import _common as C
from make_dwell_schedules import (_load_schedule, _structure_counts, build_schedule,
                                  verify_schedule)
from parameters import (BUS_FREIGHT_CAPACITY_KG, BUS_FREIGHT_NMAX_PARCELS,
                        EXTRA_DWELL_FIXED_OVERHEAD_S, EXTRA_DWELL_PER_UNIT_S,
                        WEIGHT_REGIMES, c_van)
from scenario_presets import get_preset

# Half and double the base. Symmetric in scale, which is what a curve read on a
# log axis wants, and N=940 lands on a dwell of 40 s/stop — identical to the
# alpha=1 schedule already on disk, which makes one free cross-check.
N_VALUES = [235, 940]
BASE_N = 470

ALPHAS = [0.0, 0.5]
SEEDS = [4711, 9876]
CONGESTION = "peak"
WEIGHT = "medium"

SCEN_DIR = C.ROOT / "scenarios" / "ipft_rotterdam"


def campaign(n: int) -> C.Campaign:
    # RN235 / RN940: neither is a prefix of the other, which matters because
    # scenario_runner --filter is a plain substring match.
    return C.Campaign(tag=f"RN{n}", name="parcel_count")


def cell_name(alpha: float, seed: int) -> str:
    return f"alpha{int(round(alpha * 100)):03d}_{CONGESTION}_{WEIGHT}_seed{seed}"


def preset_for(n: int):
    """The line-44 preset with N replaced, and nothing else.

    ScenarioPreset is a frozen dataclass, so `replace` returns a new one and the
    original is untouched. Everything downstream that reads N reads it from the
    preset — the dwell formula through n_freight_units_real, the tour count
    through n_freight_units_sim — so this one line is the whole change, with no
    edit to make_dwell_schedules or to the warm-scenario generator.
    """
    return dataclasses.replace(get_preset("rotterdam"), n_freight_units_sim=n)


def expected_dwell(n: int, alpha: float) -> int:
    p = preset_for(n)
    units = alpha * p.n_freight_units_real / p.bus_trips_per_day / p.n_pickup_stops
    return int(round(EXTRA_DWELL_FIXED_OVERHEAD_S + EXTRA_DWELL_PER_UNIT_S * units))


def schedule_dir(n: int) -> Path:
    return SCEN_DIR / f"dwell_schedules_n{n}"


def schedule_for(n: int, alpha: float) -> Path:
    # alpha=0 carries no freight dwell at all, so its schedule is the same file
    # for every N and the base one is reused rather than copied.
    if alpha == 0.0:
        return C.DWELL_SCHEDULES / "ptSchedule_dwell_alpha000_blocking.xml.gz"
    astr = f"{int(round(alpha * 100)):03d}"
    return schedule_dir(n) / f"ptSchedule_dwell_alpha{astr}_blocking.xml.gz"


# ── prepare ────────────────────────────────────────────────────────────────

def prepare(heap: str, dry: bool = False) -> None:
    """Write every input this campaign needs, checking each against the formula.

    dry=True walks the same path and writes NOTHING. It exists because the
    expensive step is the warm plans — insert_vans reads the whole stripped
    population into memory and writes ~140 MB per cell — and that is worth
    checking before rather than after, and worth not doing at all while the
    machine is busy with an analysis.
    """
    base_preset = get_preset("rotterdam")
    weight_kg = WEIGHT_REGIMES[WEIGHT]
    cvan = c_van(weight_kg)
    hb_ids = frozenset((SCEN_DIR / "line44_hb_vehicle_ids.txt").read_text().split())
    base_sched = Path(base_preset.base_transit_schedule)
    base_counts = _structure_counts(_load_schedule(base_sched))

    print(f"parcel-count sensitivity — {CONGESTION}, {WEIGHT} parcels, "
          f"N in {N_VALUES} against a base of {BASE_N}"
          + ("   [DRY RUN — nothing is written]" if dry else ""))
    print(f"C_van({weight_kg:.0f} kg) = {cvan} parcels/tour, F={base_preset.bus_trips_per_day}, "
          f"{base_preset.n_pickup_stops} delivery stops")

    camps = []
    for n in N_VALUES:
        camp = campaign(n)
        camps.append(camp)
        p = preset_for(n)
        print(f"\n=== N_sim = {n}  (N_real = {p.n_freight_units_real:.0f}) ===")

        # ── the feasibility arithmetic, reported before anything is written ──
        parcels_per_trip = 0.5 * p.n_freight_units_real / p.bus_trips_per_day
        print(f"  at alpha=0.5: {parcels_per_trip:.1f} parcels/trip against a "
              f"{BUS_FREIGHT_NMAX_PARCELS}-parcel rack, "
              f"{parcels_per_trip * weight_kg:.0f} kg against "
              f"{BUS_FREIGHT_CAPACITY_KG:.0f} kg")
        # Both caps, because either one binding puts the cell in the same trap:
        # Chapter 4 (sec:method-capacity) declares a rebound, alpha_eff =
        # min(alpha, alpha_max) with the overflow returned to the van, and the code
        # does NOT implement it — feasibility.py flags and term_c charges alpha*N*w
        # regardless. A cell above either cap would be priced as if the bus carried
        # freight it cannot hold.
        n_cap = int(BUS_FREIGHT_NMAX_PARCELS * p.bus_trips_per_day / 0.5 * p.sample_rate)
        w_cap = int(BUS_FREIGHT_CAPACITY_KG / weight_kg * p.bus_trips_per_day
                    / 0.5 * p.sample_rate)
        if parcels_per_trip > BUS_FREIGHT_NMAX_PARCELS:
            raise C.CheckFailed(
                f"N={n} puts {parcels_per_trip:.1f} parcels on a trip, above the "
                f"{BUS_FREIGHT_NMAX_PARCELS}-parcel rack, and the capacity rebound "
                f"Chapter 4 declares is not implemented. Pick an N below {n_cap}, "
                f"or implement the rebound first.")
        if parcels_per_trip * weight_kg > BUS_FREIGHT_CAPACITY_KG:
            raise C.CheckFailed(
                f"N={n} puts {parcels_per_trip * weight_kg:.0f} kg on a trip, above "
                f"the {BUS_FREIGHT_CAPACITY_KG:.0f} kg cargo allowance, and the "
                f"capacity rebound Chapter 4 declares is not implemented. Pick an N "
                f"below {w_cap} for {WEIGHT} parcels, or implement the rebound first.")

        # ── the alpha=0.5 schedule: the only new input on the bus side ──────
        for alpha in ALPHAS:
            if alpha == 0.0:
                sched = schedule_for(n, alpha)
                vals, n_stamped = C.schedule_dwell_seconds(sched)
                print(f"  alpha=0.00  [reuse] {sched.name}")
                C.check("schedule dwell [s/stop]", (sorted(vals)[0] if vals else 0), 0)
                C.check("stops stamped", n_stamped, 0)
                continue
            out = schedule_for(n, alpha)
            verb = "would write" if dry else "[write]"
            print(f"  alpha={alpha:.2f}  {verb} {out.parent.name}/{out.name}")
            if dry:
                print(f"    [--  ] schedule dwell [s/stop]: {expected_dwell(n, alpha)} "
                      f"(from the formula; the file is not written in a dry run)")
                continue
            report = build_schedule(base_sched, alpha, True, out, preset_for(n),
                                    hb_ids, EXTRA_DWELL_PER_UNIT_S)
            verify_schedule(base_counts, report, preset_for(n))
            vals, n_stamped = C.schedule_dwell_seconds(out)
            C.check("schedule dwell [s/stop]", sorted(vals)[0], expected_dwell(n, alpha))
            C.check("stops stamped", n_stamped, 64)

        # The free cross-check: N=940 at alpha=0.5 asks the bus for exactly the
        # same parcels per stop as N=470 at alpha=1, so the two schedules must be
        # the same file. If they are not, the dwell formula did not do what this
        # script believes it does.
        if expected_dwell(n, 0.5) == expected_dwell(BASE_N, 1.0) and not dry:
            twin = C.DWELL_SCHEDULES / "ptSchedule_dwell_alpha100_blocking.xml.gz"
            C.check(f"schedule identical to {twin.name} (same parcels/stop)",
                    C.same_content(schedule_for(n, 0.5), twin), True)

        # ── warm plans and configs ──────────────────────────────────────────
        n_slots = math.ceil(n / cvan)          # the alpha=0 tour count = the grid
        written = []
        for alpha in ALPHAS:
            n_tours = math.ceil((1 - alpha) * n / cvan) if (1 - alpha) * n > 0 else 0
            if dry:
                print(f"  alpha={alpha:.2f}  would write "
                      f"{C.plans_path(camp, alpha, CONGESTION, WEIGHT, n_tours, n_slots).name}"
                      f"  ({n_tours} van tours on a {n_slots}-slot grid)"
                      f"  + {len(SEEDS)} configs")
                continue
            plans = C.ensure_plans(camp, base_preset, alpha, CONGESTION, WEIGHT,
                                   n_freight=n, n_tours=n_tours, n_slots=n_slots)
            for seed in SEEDS:
                written.append(C.write_config(camp, base_preset, cell_name(alpha, seed),
                                              plans, schedule_for(n, alpha), seed,
                                              iterations=C.WARM_ITERS, frozen=True))
        if dry:
            continue

        # Counted before the file is read back, so an empty batch fails with the
        # count rather than with an index error.
        C.check("configs written", len(written), len(ALPHAS) * len(SEEDS))
        facts = C.config_facts(written[-1])
        C.check("lastIteration", facts["last_iteration"], C.WARM_ITERS)
        C.check("background strategies", facts["strategies"].get("(background)"),
                ["ChangeExpBeta"])
        C.check("schedule in config", Path(facts["schedule"]).name,
                schedule_for(n, ALPHAS[-1]).name)
        C.announce(camp, len(written), heap)

    print(f"{len(camps) * len(ALPHAS) * len(SEEDS)} runs in total, ~3 h.")
    if dry:
        print("Dry run: nothing was written. Re-run with --prepare when the machine "
              "is free — the warm plans are the slow part.")
    else:
        print("Then:  python python_pipeline/sensitivity/sens_parcel_count.py --analyse")


# ── analyse ────────────────────────────────────────────────────────────────

def _row(res: dict, n: int, seed: int, alpha: float) -> dict:
    p = preset_for(n)
    cvan = c_van(WEIGHT_REGIMES[WEIGHT])
    row = dict(n_freight_units_sim=n, n_freight_units_real=p.n_freight_units_real,
               seed=seed, alpha=alpha, congestion=CONGESTION, weight_regime=WEIGHT,
               tours_removed=math.ceil(n / cvan) - math.ceil((1 - alpha) * n / cvan),
               parcels_per_trip=alpha * p.n_freight_units_real / p.bus_trips_per_day,
               feasible=res.get("feasible"),
               term_a_kg=res.get("term_a_kg"),
               term_b_kg=res.get("term_b_kg"),
               term_b_excl_deadlock_kg=res.get("term_b_excl_deadlock_kg"),
               term_c_kg=res.get("term_c_kg"),
               net_robust_kg_per_day=res.get("net_robust_kg_per_day"),
               extra_dwell_s_per_trip=res.get("extra_dwell_s_per_trip"),
               alpha_max=res.get("alpha_max"))
    row["net_excl_deadlock_kg"] = C.net_excl_deadlock(row)
    # The number the whole check is about: what one parcel is worth once the bus
    # has been paid for. Real parcels, so N_real and not N_sim.
    delivered = alpha * p.n_freight_units_real
    row["net_g_per_parcel"] = (row["net_excl_deadlock_kg"] * 1000.0 / delivered
                               if row["net_excl_deadlock_kg"] is not None and delivered else None)
    return row


def analyse() -> None:
    import pandas as pd

    rows = []

    # ── the base point, from runs that already exist ────────────────────────
    print(f"N={BASE_N} (base) — from the published campaign, which is also the "
          f"validation that nothing in the pipeline moved")
    for seed in SEEDS:
        base = C.find_events(C.BLOCKING_RUNS, cell_name(0.0, seed))
        scen = C.find_events(C.BLOCKING_FIX_RUNS, cell_name(0.5, seed))
        if not base or not scen:
            print(f"  [skip] seed {seed}: base or alpha=0.5 events missing")
            continue
        print(f"  [cell] seed {seed}", flush=True)
        res = C.run_cell(base, scen, alpha=0.5, weight=WEIGHT, congestion=CONGESTION)
        C.validate_base(res, 0.5, CONGESTION, WEIGHT, seed)
        rows.append(_row(res, BASE_N, seed, 0.5))

    if not rows:
        raise C.CheckFailed(
            "the base cell could not be computed, so there is nothing to validate "
            "the swept cells against. Check the headline runs are still on disk.")

    # ── the swept points ────────────────────────────────────────────────────
    for n in N_VALUES:
        camp = campaign(n)
        print(f"\nN={n} — {camp.runs_dir}")
        for seed in SEEDS:
            base = C.find_events(camp.runs_dir, cell_name(0.0, seed))
            scen = C.find_events(camp.runs_dir, cell_name(0.5, seed))
            if not base or not scen:
                print(f"  [skip] seed {seed}: run not finished")
                continue
            print(f"  [cell] seed {seed}", flush=True)
            res = C.run_cell(base, scen, alpha=0.5, weight=WEIGHT,
                             congestion=CONGESTION, extra=["--n-freight", str(n)])
            # The run and the analysis must agree about N, and this is the place
            # a mismatch would otherwise pass unnoticed: term_c's guard only fires
            # when the measured dwell is BELOW a tenth of the a-priori value.
            C.check("N reaching the analysis", res.get("n_freight_units_sim"), n)
            # The vans the two runs ACTUALLY contain, against what this N implies.
            # Not the same as comparing tours_baseline to the formula — that would
            # be the formula against itself. term_b averages over the vans it finds
            # and multiplies by the formula's tour count, so a stale warm-plans
            # file (the generator reuses one whenever the name exists) yields a
            # plausible wrong number with no other signal.
            cvan = c_van(WEIGHT_REGIMES[WEIGHT])
            C.check("vans in the baseline run", res.get("n_vans_observed_baseline"),
                    math.ceil(n / cvan))
            C.check("vans in the scenario run", res.get("n_vans_observed_scenario"),
                    math.ceil(0.5 * n / cvan))
            rows.append(_row(res, n, seed, 0.5))

    df = pd.DataFrame(rows).sort_values(["n_freight_units_sim", "seed"])
    camp0 = campaign(N_VALUES[0])
    camp0.out_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(camp0.out_csv, index=False)
    verdict(df)
    print(f"\nsaved -> {camp0.out_csv}")


def verdict(df) -> None:
    print("\n=== the balance against the parcel count (alpha=0.5, peak, medium) ===")
    print(f"{'N_sim':>7} {'N_real':>8} {'tours':>6} {'S_van excl':>11} {'E_PT':>8} "
          f"{'net':>9} {'floor':>7} {'g/parcel':>9}")
    per_n = {}
    for n, grp in df.groupby("n_freight_units_sim"):
        mean = {k: grp[k].mean() for k in
                ("term_b_excl_deadlock_kg", "term_c_kg", "net_excl_deadlock_kg",
                 "net_g_per_parcel", "n_freight_units_real", "tours_removed")}
        floor = C.seed_floor(grp["net_excl_deadlock_kg"].tolist())
        per_n[n] = mean
        print(f"{n:>7} {mean['n_freight_units_real']:>8.0f} "
              f"{mean['tours_removed']:>6.0f} {mean['term_b_excl_deadlock_kg']:>11.2f} "
              f"{mean['term_c_kg']:>8.2f} {mean['net_excl_deadlock_kg']:>9.2f} "
              f"{floor:>7.2f} {mean['net_g_per_parcel']:>9.1f}")

    print("\n=== is it a line, and where does it stop being one? ===")
    ns = sorted(per_n)
    if len(ns) >= 2:
        ref = per_n[ns[0]]
        for n in ns:
            m = per_n[n]
            scale = m["n_freight_units_real"] / ref["n_freight_units_real"]
            for key, label in (("term_b_excl_deadlock_kg", "S_van excl. deadlock"),
                               ("term_c_kg", "E_PT"),
                               ("net_excl_deadlock_kg", "net")):
                if n == ns[0]:
                    continue
                linear = ref[key] * scale
                dev = (m[key] / linear - 1) * 100 if linear else float("nan")
                print(f"  N={n:<5} {label:<22} {m[key]:8.2f} against {linear:8.2f} "
                      f"if it scaled with N   ({dev:+.1f}%)")
    print("\nThe bus-side cost is the one that must fall short of proportional: the "
          "10 s stop overhead is charged on all 98 departures whatever N is, so "
          "E_PT carries a fixed part and the net per parcel improves with N.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--prepare", action="store_true",
                   help="write the schedules, plans and configs; print the runner commands")
    g.add_argument("--analyse", action="store_true",
                   help="read the finished runs, write the CSV, print the verdict")
    g.add_argument("--dry-run", action="store_true", dest="dry_run",
                   help="walk the prepare path and write nothing — check the dwell "
                        "values and the tour counts before spending an hour on the "
                        "warm plans, or while the machine is busy")
    ap.add_argument("--heap", default="7g", help="JVM heap for the printed command")
    args = ap.parse_args()
    C.use_project_root()
    try:
        if args.prepare or args.dry_run:
            prepare(args.heap, dry=args.dry_run)
        else:
            analyse()
    except C.CheckFailed as exc:
        C.fail(exc)


if __name__ == "__main__":
    main()
