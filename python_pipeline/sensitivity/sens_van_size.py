"""
Sensitivity 3 of 3 — does the answer depend on which van the operator runs?

WHAT IS BEING ASKED. The van-removal saving is the counterfactual: what the parcels
would have cost if vans had carried them. That counterfactual is a fleet decision,
not a fact of the corridor. A smaller van fills up sooner, so the same parcels need
MORE tours, each of them lighter and slimmer; a bigger one needs fewer, heavier,
draggier tours. Whether the balance survives that is the question, and it is the one
an operator with a different fleet will ask first.

WHY IT IS READ AT FULL LOAD, and this is a change from the sentence currently in
Chapter 4. At alpha=1 the scenario has NO vans left, so the scenario run is the same
simulation for every van size — it is already on disk. What the van size moves is
the BASELINE: how many tours it takes to deliver all N parcels by road. And the
baseline is where the whole saving lives at full load, since Term B at alpha=1 is
exactly the baseline's tours removed. So full load is both the cheapest place to ask
the question and the place where the size acts hardest. Twelve new runs, all of them
baselines; the scenario side costs nothing.

WHAT A VAN TYPE IS. parameters.VAN_TYPES holds tare, payload capacity, parcel cap,
frontal area and drag as ONE object, because varying the payload alone would test
the same van with a bigger cargo bay — a different cargo bay is not a different van.
The parcel cap moves with the size for the same reason: the bay is a volume. If it
did not, the light regime would come out identical for every size by pure
arithmetic, since C_van(3 kg) = min(cap, payload/3) saturates at the cap for any
payload above three times it.

WHAT THIS SCRIPT CHANGES IN THE SHARED PIPELINE, because it is the only one of the
three that does: parameters.py gained the VAN_TYPES table, term_b.py takes the
vehicle as an argument instead of reading four module constants, and run_pipeline.py
gained --van-type. All three are additive and the defaults are the old constants —
verified by running the committed term_b and the patched one on the same data and
comparing every returned number.

A TYPE WITHOUT A CITATION CANNOT BE USED. VAN_TYPES entries carry a `source`, and
van_type() raises when it is None — the rule euro_factors.py already applies to the
Euro-class factors. --dry-run will show the design with the placeholder figures so
they can be discussed; --prepare will not touch them.

Usage, from the project root:

    python python_pipeline/sensitivity/sens_van_size.py --dry-run
    python python_pipeline/sensitivity/sens_van_size.py --prepare
    # ... paste the printed scenario_runner commands into the nohup queue ...
    python python_pipeline/sensitivity/sens_van_size.py --analyse
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import _common as C
from parameters import WEIGHT_REGIMES, VanTypeNotSourced, van_type
from scenario_presets import get_preset

SIZES = ["small", "large"]        # against the headline "base"
BASE_SIZE = "base"
WEIGHTS = ["light", "medium", "heavy"]
SEEDS = [4711, 9876]
CONGESTION = "peak"
# The whole battery lives at full load: see the docstring. alpha=0 is what gets
# run (the baselines); alpha=1 is what gets read.
ALPHA_BASELINE = 0.0
ALPHA_READ = 1.0


def campaign(size: str) -> C.Campaign:
    # RVANSMALL / RVANLARGE — neither a prefix of the other, and neither a prefix
    # of any existing campaign tag, because --filter is a substring match.
    return C.Campaign(tag=f"RVAN{size.upper()}", name="van_size")


def cell_name(alpha: float, weight: str, seed: int) -> str:
    return f"alpha{int(round(alpha * 100)):03d}_{CONGESTION}_{weight}_seed{seed}"


def baseline_schedule() -> Path:
    # alpha=0 carries no freight dwell, so one schedule serves every size and
    # every weight — the same file the headline baselines were run from.
    return C.DWELL_SCHEDULES / "ptSchedule_dwell_alpha000_blocking.xml.gz"


# ── prepare ────────────────────────────────────────────────────────────────

def prepare(heap: str, dry: bool = False) -> None:
    preset = get_preset("rotterdam")
    n_freight = preset.n_freight_units_sim

    print(f"van-size sensitivity — {CONGESTION}, all three weights, read at "
          f"alpha={ALPHA_READ:.0f}" + ("   [DRY RUN — nothing is written]" if dry else ""))
    print(f"N_sim={n_freight}; the scenario side is the alpha=1 runs already on disk "
          f"({C.BLOCKING_RUNS.name}), which have no vans and so are the same "
          f"simulation for every van size.")

    # ── the design table, printed before anything is written ────────────────
    print(f"\n  tours needed to deliver all {n_freight} parcels by road (alpha=0):")
    print(f"    {'van':<8} {'tare':>6} {'payload':>8} {'cap':>5} {'area':>6}   "
          + "  ".join(f"{w:>12}" for w in WEIGHTS))
    for size in [BASE_SIZE] + SIZES:
        vt = van_type(size, allow_unsourced=True)
        cells = []
        for w in WEIGHTS:
            c = vt.c_van(WEIGHT_REGIMES[w])
            cells.append(f"C={c:3d} -> {math.ceil(n_freight / c):2d}")
        flag = "" if vt.source else "   [NO SOURCE]"
        print(f"    {size:<8} {vt.tare_kg:>6.0f} {vt.payload_capacity_kg:>8.0f} "
              f"{vt.parcels_per_tour_max:>5d} {vt.frontal_area_m2:>6.1f}   "
              + "  ".join(f"{c:>12}" for c in cells) + flag)

    # The arithmetic trap the design has to avoid, checked rather than remembered.
    light_cvans = {van_type(s, allow_unsourced=True).c_van(WEIGHT_REGIMES["light"])
                   for s in [BASE_SIZE] + SIZES}
    if len(light_cvans) == 1:
        print(f"\n  [WARN] every size gives C_van(light) = {light_cvans.pop()}, so the "
              f"light row will be identical across sizes by arithmetic, not by "
              f"physics. Move parcels_per_tour_max with the size in parameters.py, "
              f"or declare the flat row before running rather than after.")

    written_total = 0
    for size in SIZES:
        try:
            vt = van_type(size)          # refuses an unsourced specification
        except VanTypeNotSourced as exc:
            if dry:
                print(f"\n=== {size} ===  [DRY RUN: unsourced, would be refused by "
                      f"--prepare]")
                vt = van_type(size, allow_unsourced=True)
            else:
                raise C.CheckFailed(str(exc)) from None
        else:
            print(f"\n=== {size} ===  ({vt.source})")

        camp = campaign(size)
        written = []
        for weight in WEIGHTS:
            cvan = vt.c_van(WEIGHT_REGIMES[weight])
            # alpha=0: every parcel goes by road, so the tour count IS the
            # departure grid and there is nothing for the grid fix to protect
            # against here — but n_slots is passed explicitly all the same, so
            # this campaign reads the same as the others.
            n_tours = math.ceil(n_freight / cvan)
            if dry:
                print(f"  {weight:<7} C_van={cvan:3d}  would write "
                      f"{C.plans_path(camp, ALPHA_BASELINE, CONGESTION, weight, n_tours, n_tours).name}"
                      f"  ({n_tours} van tours)  + {len(SEEDS)} configs")
                continue
            print(f"  {weight:<7} C_van={cvan:3d}  {n_tours} van tours")
            plans = C.ensure_plans(camp, preset, ALPHA_BASELINE, CONGESTION, weight,
                                   n_freight=n_freight, n_tours=n_tours, n_slots=n_tours)
            for seed in SEEDS:
                written.append(C.write_config(
                    camp, preset, cell_name(ALPHA_BASELINE, weight, seed),
                    plans, baseline_schedule(), seed,
                    iterations=C.WARM_ITERS, frozen=True))
        if dry:
            written_total += len(WEIGHTS) * len(SEEDS)
            continue

        # Counted before the file is read back, so an empty batch fails with the
        # count rather than with an index error.
        C.check("configs written", len(written), len(WEIGHTS) * len(SEEDS))
        facts = C.config_facts(written[-1])
        C.check("lastIteration", facts["last_iteration"], C.WARM_ITERS)
        C.check("background strategies", facts["strategies"].get("(background)"),
                ["ChangeExpBeta"])
        C.check("schedule in config", Path(facts["schedule"]).name,
                baseline_schedule().name)
        written_total += len(written)
        C.announce(camp, len(written), heap)

    # The scenario side must already be there, or the campaign has nothing to be
    # subtracted from.
    missing = [cell_name(ALPHA_READ, w, s) for w in WEIGHTS for s in SEEDS
               if not C.find_events(C.BLOCKING_RUNS, cell_name(ALPHA_READ, w, s))]
    C.check("alpha=1 scenario runs already on disk",
            len(WEIGHTS) * len(SEEDS) - len(missing), len(WEIGHTS) * len(SEEDS))

    print(f"\n{written_total} runs in total, ~4 h — all of them baselines.")
    if dry:
        print("Dry run: nothing was written. Fill in the two `source` fields in "
              "parameters.py, then run --prepare.")
    else:
        print("Then:  python python_pipeline/sensitivity/sens_van_size.py --analyse")


# ── analyse ────────────────────────────────────────────────────────────────

def _row(res: dict, size: str, weight: str, seed: int) -> dict:
    row = dict(van_type=res.get("van_type") or size, requested_van_type=size,
               weight_regime=weight, seed=seed, alpha=ALPHA_READ,
               congestion=CONGESTION,
               van_tare_kg=res.get("van_tare_kg"),
               van_payload_capacity_kg=res.get("van_payload_capacity_kg"),
               van_frontal_area_m2=res.get("van_frontal_area_m2"),
               parcels_per_tour=res.get("parcels_per_tour"),
               tours_baseline=res.get("tours_baseline"),
               tours_scenario=res.get("tours_scenario"),
               term_a_kg=res.get("term_a_kg"),
               term_b_kg=res.get("term_b_kg"),
               term_b_excl_deadlock_kg=res.get("term_b_excl_deadlock_kg"),
               term_c_kg=res.get("term_c_kg"),
               net_robust_kg_per_day=res.get("net_robust_kg_per_day"))
    row["net_excl_deadlock_kg"] = C.net_excl_deadlock(row)
    return row


def analyse() -> None:
    import pandas as pd

    rows = []

    # ── the base van, from runs that already exist: the middle column of the
    #    table AND the check that the pipeline still reproduces the surface ────
    print("base van — from the published campaign, which doubles as the validation")
    for weight in WEIGHTS:
        for seed in SEEDS:
            base = C.find_events(C.BLOCKING_RUNS, cell_name(ALPHA_BASELINE, weight, seed))
            scen = C.find_events(C.BLOCKING_RUNS, cell_name(ALPHA_READ, weight, seed))
            if not base or not scen:
                print(f"  [skip] {weight}/seed{seed}: events missing")
                continue
            print(f"  [cell] {weight}/seed{seed}", flush=True)
            res = C.run_cell(base, scen, alpha=ALPHA_READ, weight=weight,
                             congestion=CONGESTION)
            C.validate_base(res, ALPHA_READ, CONGESTION, weight, seed)
            rows.append(_row(res, BASE_SIZE, weight, seed))

    if not rows:
        raise C.CheckFailed(
            "the base van could not be computed, so there is nothing to compare the "
            "other sizes against. Check the headline runs are still on disk.")

    # ── the swept sizes: new baselines, the SAME alpha=1 scenario ───────────
    for size in SIZES:
        camp = campaign(size)
        print(f"\n{size} — baselines from {camp.runs_dir.name}, "
              f"scenario from {C.BLOCKING_RUNS.name}")
        for weight in WEIGHTS:
            for seed in SEEDS:
                base = C.find_events(camp.runs_dir, cell_name(ALPHA_BASELINE, weight, seed))
                scen = C.find_events(C.BLOCKING_RUNS, cell_name(ALPHA_READ, weight, seed))
                if not base or not scen:
                    print(f"  [skip] {weight}/seed{seed}: baseline not finished")
                    continue
                print(f"  [cell] {weight}/seed{seed}", flush=True)
                res = C.run_cell(base, scen, alpha=ALPHA_READ, weight=weight,
                                 congestion=CONGESTION,
                                 extra=["--van-type", size])
                # The run inserted a van count derived from this size; the analysis
                # must be pricing the same vehicle, or the two disagree silently.
                C.check("van type reaching the analysis", res.get("van_type"), size)
                # The vans the baseline run ACTUALLY holds, against what this van
                # size implies. Not tours_baseline, which is the formula and would
                # only be the formula checked against itself: term_b averages over
                # the vans it finds and scales by the formula's count, so a stale
                # warm-plans file gives a plausible wrong number with no other sign.
                vt = van_type(size, allow_unsourced=True)
                C.check("vans in the baseline run", res.get("n_vans_observed_baseline"),
                        math.ceil(get_preset("rotterdam").n_freight_units_sim
                                  / vt.c_van(WEIGHT_REGIMES[weight])))
                # At full load the scenario carries none, which is also the check
                # that the reused alpha=1 run really is the one with no vans.
                C.check("vans in the scenario run",
                        res.get("n_vans_observed_scenario"), 0)
                rows.append(_row(res, size, weight, seed))

    df = pd.DataFrame(rows)
    out = campaign(SIZES[0]).out_csv
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    verdict(df)
    print(f"\nsaved -> {out}")


def verdict(df) -> None:
    order = [BASE_SIZE] + SIZES
    print("\n=== the van-removal saving against the vehicle (alpha=1, peak) ===")
    print(f"{'weight':<8} {'van':<8} {'C_van':>6} {'tours':>6} {'S_van':>9} "
          f"{'S_van excl':>11} {'E_PT':>8} {'net excl':>9} {'floor':>7}")
    table = {}
    for weight in WEIGHTS:
        for size in order:
            grp = df[(df.weight_regime == weight) & (df.requested_van_type == size)]
            if grp.empty:
                continue
            m = {k: grp[k].mean() for k in
                 ("parcels_per_tour", "tours_baseline", "term_b_kg",
                  "term_b_excl_deadlock_kg", "term_c_kg", "net_excl_deadlock_kg")}
            table[(weight, size)] = m
            floor = C.seed_floor(grp["net_excl_deadlock_kg"].tolist())
            print(f"{weight:<8} {size:<8} {m['parcels_per_tour']:>6.0f} "
                  f"{m['tours_baseline']:>6.0f} {m['term_b_kg']:>9.2f} "
                  f"{m['term_b_excl_deadlock_kg']:>11.2f} {m['term_c_kg']:>8.2f} "
                  f"{m['net_excl_deadlock_kg']:>9.2f} {floor:>7.2f}")
        print()

    print("=== how much the fleet decision is worth, against the base van ===")
    for weight in WEIGHTS:
        ref = table.get((weight, BASE_SIZE))
        if not ref:
            continue
        parts = []
        for size in SIZES:
            m = table.get((weight, size))
            if not m or not ref["net_excl_deadlock_kg"]:
                continue
            pct = (m["net_excl_deadlock_kg"] / ref["net_excl_deadlock_kg"] - 1) * 100
            parts.append(f"{size} {pct:+.1f}%")
        if parts:
            print(f"  {weight:<8} net against the base van: " + ",  ".join(parts))

    # The one row that can be flat for a reason that is not physical.
    # Only a claim worth making once at least two sizes have finished: with one
    # size in the table the set of caps is trivially of size one, and the note
    # would announce a flat row that nothing has been measured against.
    caps = {size: table[(WEIGHTS[0], size)]["parcels_per_tour"]
            for size in order if (WEIGHTS[0], size) in table}
    if len(caps) >= 2 and len(set(caps.values())) == 1:
        print(f"\n  NOTE: the light row is identical across sizes because C_van(3 kg) "
              f"saturates at the parcel cap for all of them ({int(list(caps.values())[0])} "
              f"parcels). That is arithmetic, not a finding — say so before the table, "
              f"not after it.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--prepare", action="store_true",
                   help="write the baseline plans and configs; print the runner commands")
    g.add_argument("--dry-run", action="store_true", dest="dry_run",
                   help="show the design and the tour counts, writing nothing — works "
                        "even while the van specifications are still unsourced")
    g.add_argument("--analyse", action="store_true",
                   help="read the finished runs, write the CSV, print the verdict")
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
