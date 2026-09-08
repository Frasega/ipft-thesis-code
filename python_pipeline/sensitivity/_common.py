"""
Shared plumbing for the three run-costing sensitivity checks.

The checks that need their own MATSim runs — re-planning, the parcel count N and
the van size — are declared in the thesis (Chapter 4, tab:campaign) and each is
driven by one script in this directory. They all do the same two things:

    --prepare   write the inputs (schedules, warm plans, configs) and PRINT the
                scenario_runner.py command; the runs themselves are launched by
                hand into the nohup queue, because one machine runs one batch.
    --analyse   read the finished runs through run_pipeline.py, write one CSV and
                print a verdict.

This module holds what the three have in common, and — more to the point — the
guards that stop a campaign from silently becoming a copy of another one. Three
things in the pipeline are name-based and none of them knows about N or the van
type:

  * make_rotterdam_warm_scenarios reuses a warm-plans file whenever it already
    exists, and the name carries only alpha/congestion/weight. A campaign with a
    different N would silently run the old vans. `plans_path` here puts the
    campaign tag IN the filename, and `ensure_plans` says out loud whether it
    wrote or reused.
  * rotterdam_surface_robust.already_done keys on (congestion, weight, seed,
    alpha), so two campaigns in one --out directory would skip each other. Each
    campaign here gets its own CSV.
  * scenario_runner --filter is a plain substring, so campaign tags must not be
    prefixes of one another. The tags in use: RPLAN20, RN235/RN940,
    RVANSMALL/RVANLARGE.

And the pact borrowed from layer3_baseline_variants.py: the BASE variant of every
sweep must reproduce the headline surface before any swept number is believed. If
it does not, the script stops and writes nothing.
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

# The pipeline modules import each other flat (`from term_b import ...`) because
# each puts its own directory on sys.path. From this subdirectory the parent has
# to be added once, here, so both this module and its callers can do the same.
_PIPE = Path(__file__).resolve().parents[1]
if str(_PIPE) not in sys.path:
    sys.path.insert(0, str(_PIPE))

from generate_configs import _write_config_with_doctype, patch_config  # noqa: E402
from insert_vans import create_plans_file  # noqa: E402
from scenario_presets import OUTPUT_ROOT, get_preset  # noqa: E402
from config.paths import run_file  # resolves the optional MATSim runId prefix

# Windows: a REDIRECTED stdout defaults to cp1252, which cannot encode the dashes
# and arrows in these messages -> UnicodeEncodeError, and a --prepare that has
# already written its configs dies while reporting them. Same fix, and same
# reason, as run_pipeline.py: a terminal never shows this because it is UTF-8,
# but every nohup queue redirects.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:
        pass

PIPE = _PIPE
ROOT = _PIPE.parent
JAR = "matsim-example-project-0.0.1-SNAPSHOT.jar"

# The headline surfaces every sweep validates itself against. Line 44, in-lane,
# van departure grid corrected, deadlock links excluded — the two halves of the
# surface that goes in the thesis (HANDOFF 2026-08-22 section 1).
HEADLINE_A1 = ROOT / "output" / "sensitivity_rotterdam_dwell_blocking_a1" / "results_long.csv"
HEADLINE_FIX = ROOT / "output" / "sensitivity_rotterdam_dwell_blocking_fix" / "results_long.csv"
# The runs those surfaces were computed from: alpha=0 and alpha=1 for all three
# weights, both seeds, peak and off-peak.
BLOCKING_RUNS = OUTPUT_ROOT / "ipft_rotterdam_dwell_blocking_runs"
# The alpha<1 half of the headline surface: re-run in August with the corrected
# van departure grid, so the alpha=0.5 cells live here and NOT in BLOCKING_RUNS.
BLOCKING_FIX_RUNS = OUTPUT_ROOT / "ipft_rotterdam_dwell_blockingfix_runs"
# The corrected warm plans (grid fix, 2026-08-21) and the base dwell schedules.
WARM_PLANS_GRID = OUTPUT_ROOT / "ipft_rotterdam_warm_plans_grid"
DWELL_SCHEDULES = ROOT / "scenarios" / "ipft_rotterdam" / "dwell_schedules"
DEADLOCK_LINKS = {
    "peak": ROOT / "scenarios" / "ipft_rotterdam" / "deadlock_links.txt",
    "offpeak": ROOT / "scenarios" / "ipft_rotterdam" / "deadlock_links_offpeak.txt",
}

WARM_ITERS = 1  # the frozen-day default; the re-planning battery overrides it


def fail(exc: Exception) -> None:
    """Report a guard failure the way the pipeline does — one line, no traceback.

    These are expected conditions (the batch has not run yet, an input is
    missing, a check did not hold), not crashes, and a traceback buries the one
    sentence that says what to do.
    """
    print()
    sys.exit(f"{type(exc).__name__}: {exc}")


def use_project_root() -> None:
    """Run from the project root whatever directory the user invoked us from.

    Every path in the presets is relative to it — base_config, network_file, the
    link sets, the '../' re-prefixing patch_config applies. The pipeline scripts
    all say "run from project root" in their usage lines; this makes it true
    instead of hoping.
    """
    os.chdir(ROOT)


class CheckFailed(RuntimeError):
    """A --prepare or --analyse guard did not hold. Nothing is written after it."""


# ── Campaign identity ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class Campaign:
    """Every name a campaign owns, derived from one tag so they cannot drift.

    tag goes into the config prefix (what --filter matches), the run directory,
    the warm-plans filenames and the output CSV. Giving them one source is the
    difference between "this campaign is separate" and "I remembered to keep it
    separate everywhere".
    """
    tag: str            # e.g. "RN235" — must not be a prefix of another tag
    name: str           # e.g. "parcel_count" — used for the CSV filename

    @property
    def config_prefix(self) -> str:
        return self.tag

    @property
    def generated_dir(self) -> Path:
        # Same directory as every other line-44 config: patch_config re-prefixes
        # the network/schedule/vehicles paths with ../ assuming exactly this depth,
        # and Co2TotalsHandler resolves the bare link-set filenames from here.
        return ROOT / "scenarios" / "ipft_rotterdam" / "generated"

    @property
    def runs_dir(self) -> Path:
        return OUTPUT_ROOT / f"ipft_rotterdam_sens_{self.tag.lower()}_runs"

    @property
    def warm_plans_dir(self) -> Path:
        return OUTPUT_ROOT / f"ipft_rotterdam_sens_{self.tag.lower()}_plans"

    @property
    def out_csv(self) -> Path:
        return ROOT / "output" / f"sensitivity_{self.name}.csv"


# ── Checks that print themselves ───────────────────────────────────────────

def check(label: str, got, expected, tol: float = 0.0) -> None:
    """Assert a prepared value against what it must be, and say so on screen.

    Every --prepare step is checked against a number computed independently
    (the dwell formula, ceil((1-alpha)N/C_van), the config count), because the
    failure mode these campaigns actually have is not a crash: it is a plausible
    wrong number that nobody looks at. Printing the check makes it evidence.
    """
    if isinstance(got, (int, float)) and isinstance(expected, (int, float)):
        ok = abs(float(got) - float(expected)) <= tol
    else:
        ok = got == expected
    mark = "OK " if ok else "FAIL"
    print(f"    [{mark}] {label}: {got}" + ("" if ok else f"  (expected {expected})"))
    if not ok:
        raise CheckFailed(f"{label}: got {got!r}, expected {expected!r}")


def _content_hash(path: Path) -> str:
    """SHA-256 of a file's CONTENT, transparently decompressing .gz.

    Raw bytes are the wrong comparison for the gzipped inputs here: gzip stores
    a modification timestamp in its header, so two files written from identical
    data at different times differ in bytes and agree in content. Measured on
    warmplans_alpha100_peak_medium.xml.gz, which exists in two campaign
    directories with the same size, different bytes and the same SHA-256 once
    decompressed.
    """
    import gzip
    import hashlib

    opener = gzip.open if path.suffix == ".gz" else open
    h = hashlib.sha256()
    with opener(path, "rb") as f:
        while True:
            chunk = f.read(1 << 22)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def same_content(a: Path, b: Path) -> bool:
    """True when two files hold the same data (gz header ignored).

    Used where two campaigns must produce the same input by construction — a
    free cross-check that costs one comparison and catches a whole class of
    wrong-input mistakes.
    """
    return a.exists() and b.exists() and _content_hash(a) == _content_hash(b)


# ── Warm plans, with the campaign tag in the name ──────────────────────────

def plans_path(camp: Campaign, alpha: float, congestion: str, weight: str,
               n_tours: int, n_slots: int) -> Path:
    """Warm-plans filename that carries the campaign tag AND the fleet it holds.

    make_rotterdam_warm_scenarios names these
    warmplans_alpha{a}_{congestion}_{weight}.xml.gz — alpha, congestion and
    weight and nothing else. Two campaigns that differ in N or in van size
    produce DIFFERENT vans under the SAME name, and the generator skips a file
    that already exists, so the second campaign silently runs the first one's
    vans.

    The campaign tag alone is not enough, and that was this module's own bug: it
    separates one campaign from another but not one campaign from ITSELF after a
    correction. Fill in a van type's real payload, re-run --prepare, and the tag
    is unchanged while the tour count is not — so the old fleet would come back.
    Putting the tour count and the departure grid in the name means a corrected
    spec asks for a file that does not exist yet, which is the only version of
    this guard that does not depend on remembering anything.
    """
    astr = f"{int(round(alpha * 100)):03d}"
    return (camp.warm_plans_dir /
            f"warmplans_{camp.tag.lower()}_alpha{astr}_{congestion}_{weight}"
            f"_{n_tours}of{n_slots}.xml.gz")


def ensure_plans(camp: Campaign, preset, alpha: float, congestion: str, weight: str,
                 n_freight: int, n_tours: int, n_slots: int) -> Path:
    """Write (or knowingly reuse) one warm-plans file and report which happened.

    n_tours is the consolidated tour count for this cell and n_slots the
    BASELINE's, which is the van departure grid: a surviving van keeps the exact
    time it had at alpha=0, so baseline - scenario is the missing tours and not
    the same tours moved elsewhere in the peak (the 2026-08-21 grid fix).
    """
    path = plans_path(camp, alpha, congestion, weight, n_tours, n_slots)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        # Safe to reuse: the fleet is in the name, so this file was written for
        # exactly this tour count on exactly this grid.
        print(f"    [reuse] {path.name} ({n_tours} tours on a {n_slots}-slot grid)")
        return path
    written = create_plans_file(
        base_plans_path=longbase_seed(congestion),
        output_path=str(path),
        alpha=alpha,
        n_freight=n_freight,
        congestion=congestion,
        verbose=False,
        hub_link=preset.hub_link, terminal_link=preset.terminal_link,
        hub_x=preset.hub_xy[0], hub_y=preset.hub_xy[1],
        terminal_x=preset.terminal_xy[0], terminal_y=preset.terminal_xy[1],
        van_mode=preset.van_mode, spread_minutes=preset.van_spread_minutes,
        n_vans_override=n_tours,
        locker_stops=preset.van_locker_stops,
        n_slots=n_slots,
    )
    # create_plans_file returns what it actually inserted. Discarding it would
    # leave the one number that matters unchecked at the moment it is cheapest
    # to check.
    check(f"vans written into {path.name}", written, n_tours)
    return path


def reuse_headline_plans(alpha: float, congestion: str, weight: str) -> Path:
    """The warm-plans file the HEADLINE surface itself was run from.

    Used by the re-planning battery, which is the one campaign whose vans are
    identical to the headline's: it changes what the background is allowed to do,
    not who is on the road. Regenerating the plans would take twenty minutes and
    half a gigabyte to produce the same population, and would introduce the only
    risk worth avoiding here — that the comparison is against vans that are not
    quite the headline's. Reading the same file removes the question.

    Two directories hold them. The grid fix of 2026-08-21 rewrote only the
    alpha<1 scenarios, so alpha=0 exists only in the original directory, while
    alpha=1 exists in both with identical content (verified: same SHA-256 once
    decompressed, different gzip header timestamp). The corrected directory wins
    where both have the file.
    """
    astr = f"{int(round(alpha * 100)):03d}"
    name = f"warmplans_alpha{astr}_{congestion}_{weight}.xml.gz"
    for d in (WARM_PLANS_GRID, OUTPUT_ROOT / "ipft_rotterdam_warm_plans"):
        if (d / name).exists():
            return d / name
    raise CheckFailed(
        f"{name} found in neither {WARM_PLANS_GRID} nor "
        f"{OUTPUT_ROOT / 'ipft_rotterdam_warm_plans'} — the headline campaign's "
        f"plans are the input this battery is supposed to reuse.")


_SEED_DIRS = {"peak": "ipft_rotterdam_longbase", "offpeak": "ipft_rotterdam_longbase_offpeak"}


def longbase_seed(congestion: str) -> str:
    """The frozen equilibrium the warm runs branch from — the STRIPPED plans.

    Same resolution as make_rotterdam_warm_scenarios.rotterdam_seed, but it
    refuses the unstripped file instead of warning: every campaign here is
    compared against a headline computed on the stripped plans, and mixing the
    two would put a plan re-pick inside a difference that is supposed to hold it
    fixed.
    """
    d = OUTPUT_ROOT / _SEED_DIRS[congestion]
    # run_file resolves the optional runId prefix — see config/paths.py.
    stripped = run_file(d, "output_plans_stripped.xml.gz", required=False)
    if stripped is not None:
        return str(stripped)
    raise CheckFailed(
        f"stripped longbase plans not found in {d}. Every sensitivity here is "
        f"compared against a headline surface computed on the stripped plans; "
        f"running on the unstripped ones would let ChangeExpBeta re-pick between "
        f"baseline and scenario. Produce them with strip_selected_plans.py.")


# ── Configs ────────────────────────────────────────────────────────────────

_KEEP_STRATEGY = "ChangeExpBeta"


def freeze_replanning(tree: ET.ElementTree) -> ET.ElementTree:
    """Keep only ChangeExpBeta: the frozen-day convention of the core surface.

    Identical to make_rotterdam_warm_scenarios.freeze_replanning_rotterdam, and
    duplicated here on purpose: sens_replanning.py exists precisely to NOT call
    it, and having the switch in this module makes "frozen" and "re-planning"
    two arguments of one function instead of two code paths.
    """
    root = tree.getroot()
    for mod in root.iter("module"):
        if mod.get("name") in ("replanning", "strategy"):
            for ps in list(mod.findall("parameterset")):
                names = [p.get("value") for p in ps.iter("param")
                         if p.get("name") == "strategyName"]
                if names and _KEEP_STRATEGY not in names:
                    mod.remove(ps)
    return tree


def write_config(camp: Campaign, preset, cell: str, plans: Path, schedule: Path,
                 seed: int, iterations: int = WARM_ITERS,
                 frozen: bool = True) -> Path:
    """Write one MATSim config for this campaign and return its path.

    frozen=True is the core-surface convention (one iteration, only plan
    selection left alive). frozen=False leaves the base config's ReRoute and
    TimeAllocationMutator in place — the re-planning battery, where the point is
    that the background DOES react. The vans are unaffected either way:
    patch_config gives the 'freight' subpopulation ChangeExpBeta alone and each
    van carries a single plan, so no van ever re-routes.
    """
    out_dir = str((camp.runs_dir / cell).resolve())
    tree = patch_config(
        base_config_path=preset.base_config,
        plans_file=str(plans.resolve()),
        output_dir=out_dir,
        seed=seed,
        last_iteration=iterations,
        preset=preset,
        transit_schedule_file=str(schedule.resolve()),
    )
    if frozen:
        freeze_replanning(tree)
    ET.indent(tree, space="  ")
    camp.generated_dir.mkdir(parents=True, exist_ok=True)
    cfg = camp.generated_dir / f"config_{camp.config_prefix}_{cell}.xml"
    _write_config_with_doctype(tree, str(cfg))
    return cfg


def schedule_dwell_seconds(path: Path) -> tuple[set[int], int]:
    """Read a dwell schedule back and return (distinct dwell values [s], count).

    make_dwell_schedules prints the seconds it intends to write; this reads what
    is actually IN the file that the config will point at. The two are checked
    against each other in every --prepare, because pointing a campaign at the
    wrong schedule directory is the one mistake that produces a complete, plausible
    campaign measuring the wrong handling time — and term_c's guard cannot catch
    it, since it only fires when the measured standing falls BELOW a tenth of the
    a-priori value, never when it is too large.
    """
    import gzip

    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as f:
        root = ET.parse(f).getroot()
    vals, n = set(), 0
    for prof in root.iter("routeProfile"):
        for stop in prof.findall("stop"):
            raw = stop.get("minimumStopDuration")
            if raw is None:
                continue
            h, m, sec = raw.split(":")
            vals.add(int(h) * 3600 + int(m) * 60 + int(sec))
            n += 1
    return vals, n


def config_facts(path: Path) -> dict:
    """What a written config actually says, for the read-back check.

    Returns the iteration count, the plans and schedule it points at, and the
    replanning strategies split by subpopulation. A campaign is only as good as
    the config it wrote, and every one of these has been wrong at least once in
    this project's history.
    """
    root = ET.parse(path).getroot()
    facts: dict = {"strategies": {}}
    for mod in root.iter("module"):
        name = mod.get("name")
        if name in ("controller", "controler"):
            for prm in mod.iter("param"):
                if prm.get("name") == "lastIteration":
                    facts["last_iteration"] = int(prm.get("value"))
                elif prm.get("name") == "outputDirectory":
                    facts["output_dir"] = prm.get("value")
        elif name == "plans":
            for prm in mod.iter("param"):
                if prm.get("name") == "inputPlansFile":
                    facts["plans"] = prm.get("value")
        elif name == "transit":
            for prm in mod.iter("param"):
                if prm.get("name") == "transitScheduleFile":
                    facts["schedule"] = prm.get("value")
        elif name == "global":
            for prm in mod.iter("param"):
                if prm.get("name") == "randomSeed":
                    facts["seed"] = int(prm.get("value"))
        elif name in ("replanning", "strategy"):
            for ps in mod.findall("parameterset"):
                prms = {q.get("name"): q.get("value") for q in ps.iter("param")}
                sub = prms.get("subpopulation", "(background)")
                facts["strategies"].setdefault(sub, []).append(prms.get("strategyName"))
    for sub in facts["strategies"]:
        facts["strategies"][sub] = sorted(facts["strategies"][sub])
    return facts


def runner_command(camp: Campaign, heap: str = "7g") -> str:
    """The exact line to paste into the nohup queue.

    --filter is a plain substring match (scenario_runner.py), so the whole tag
    is given rather than a prefix of it.
    """
    return (f"python python_pipeline/scenario_runner.py --jar {JAR} "
            f"--scenario rotterdam --filter {camp.config_prefix} "
            f"--heap {heap} --skip-existing")


def announce(camp: Campaign, n_configs: int, heap: str = "7g") -> None:
    print(f"\n{n_configs} configs written to {camp.generated_dir}"
          f"  (config_{camp.config_prefix}_*)")
    print(f"runs will land in {camp.runs_dir}")
    print("\nNOW LAUNCH, from the project root:\n")
    print(f"  {runner_command(camp, heap)}\n")


# ── Reading finished runs ──────────────────────────────────────────────────

def find_events(runs_dir: Path, cell: str) -> str | None:
    hits = glob.glob(str(runs_dir / cell / "*output_events.xml.zst"))
    return hits[0] if hits else None


def run_cell(baseline: str, scenario: str, alpha: float, weight: str,
             congestion: str, extra: list[str] | None = None,
             tmp: Path | None = None) -> dict:
    """Analyse one (baseline, scenario) pair through run_pipeline.py.

    A subprocess per cell, for the reason rotterdam_surface_robust gives: a
    Rotterdam event pair is 6-8 GB of DataFrame and the memory is only released
    when the process exits. It also keeps term_b's per-(speed, mass) CO2 cache
    from ever spanning two van types in one process.
    """
    preset = get_preset("rotterdam")
    tmp = tmp or (ROOT / "output" / f"_tmp_sens_{os.getpid()}.json")
    cmd = ["python", "python_pipeline/run_pipeline.py",
           "--scenario", "rotterdam",
           "--baseline", baseline, "--scenario-events", scenario,
           "--network", preset.network_file,
           "--alpha", str(alpha), "--weight", weight,
           "--dwell-in-matsim",
           "--output", str(tmp)]
    dl = DEADLOCK_LINKS[congestion]
    if dl.exists():
        cmd += ["--deadlock-links", str(dl)]
    cmd += extra or []
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
    if not tmp.exists():
        tail = (r.stderr or r.stdout)[-300:].replace("\n", " ")
        raise CheckFailed(f"run_pipeline failed for alpha={alpha} {congestion}/"
                          f"{weight}: {tail}")
    with open(tmp) as f:
        result = json.load(f)
    tmp.unlink()
    return result


# ── The validation pact ────────────────────────────────────────────────────

def headline_row(alpha: float, congestion: str, weight: str, seed: int) -> dict:
    """The headline surface's own numbers for one cell, for the base variant to
    be checked against. alpha=1 lives in the _a1 surface, alpha<1 in _fix."""
    import pandas as pd

    src = HEADLINE_A1 if alpha == 1.0 else HEADLINE_FIX
    if not src.exists():
        raise CheckFailed(f"headline surface {src} not found — nothing to validate against")
    df = pd.read_csv(src)
    hit = df[(df.alpha == alpha) & (df.congestion == congestion)
             & (df.weight_regime == weight) & (df.seed == seed)]
    if hit.empty:
        raise CheckFailed(f"no headline cell alpha={alpha} {congestion}/{weight}/seed{seed} in {src}")
    return hit.iloc[0].to_dict()


def validate_base(result: dict, alpha: float, congestion: str, weight: str, seed: int,
                  rel_tol: float = 1e-6) -> None:
    """The base variant must reproduce the headline surface, or nothing is written.

    Borrowed verbatim from layer3_baseline_variants.py: if the unswept
    configuration reproduces the published number, then the parse, the constants
    and every step behind them match the pipeline, and a swept number differs
    from it only by the knob. If it does not, the swept numbers mean nothing and
    the script must not produce a CSV that looks as if they do.
    """
    print(f"  [validate] base variant against the headline surface "
          f"(alpha={alpha}, {congestion}/{weight}/seed{seed})")
    ref = headline_row(alpha, congestion, weight, seed)
    for key in ("term_b_kg", "term_b_excl_deadlock_kg", "term_c_kg",
                "net_robust_kg_per_day"):
        got, want = result.get(key), ref.get(key)
        if got is None or want is None:
            continue
        denom = max(abs(float(want)), 1e-9)
        rel = abs(float(got) - float(want)) / denom
        mark = "OK " if rel <= rel_tol else "FAIL"
        print(f"    [{mark}] {key}: {float(got):.6f} vs {float(want):.6f} "
              f"(rel {rel:.2e})")
        if rel > rel_tol:
            raise CheckFailed(
                f"the base variant does not reproduce the headline surface on "
                f"{key} ({got} vs {want}). Something in the shared pipeline moved: "
                f"fix that before believing any swept number.")


def net_excl_deadlock(row: dict) -> float | None:
    """S_van (deadlock links removed) minus E_PT — the net the conclusion now rests on.

    The published `net_robust_kg_per_day` is term_b - term_c with the deadlocked
    links still INSIDE term_b, so on line 44 it carries the 46 minutes a van
    spends standing on a 30 m link at Zuidplein. That artefact does not cancel in
    the difference, because Term B counts tours REMOVED and every removed tour
    takes its stall with it (HANDOFF 2026-08-22, section 2.5). Both numbers are
    carried in these CSVs; this is the corrected one.
    """
    b = row.get("term_b_excl_deadlock_kg")
    c = row.get("term_c_kg")
    if b is None or c is None or b != b or c != c:
        return None
    return float(b) - float(c)


def seed_floor(values: list[float]) -> float:
    """Seed-to-seed spread of one quantity: max - min over the seeds of a cell.

    Reported next to every difference, because the rule in Chapter 4 is that an
    effect smaller than the floor of its own set is reported as unresolved and
    never as zero.
    """
    vals = [v for v in values if v is not None]
    return (max(vals) - min(vals)) if len(vals) > 1 else float("nan")
