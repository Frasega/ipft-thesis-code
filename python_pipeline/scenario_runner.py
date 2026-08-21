"""
Batch MATSim scenario runner.

Runs all 20 configs sequentially (or a subset) by invoking the MATSim JAR
as a subprocess. Each run writes to its own output directory.

Usage:
    # Find the JAR first:
    mvn package -DskipTests  (from project root, once)

    # Run all 20 scenarios (overnight):
    python python_pipeline/scenario_runner.py --jar target/matsim-example-project-*.jar

    # Run a single config (for testing):
    python python_pipeline/scenario_runner.py --jar target/... --config scenarios/ipft_toy/generated/config_alpha050_peak_seed4711.xml

    # Run only peak scenarios:
    python python_pipeline/scenario_runner.py --jar target/... --filter peak

Estimated time (toy network, 10 iterations):
    1 run ~ 5-15 min  |  20 runs ~ 2-5 hours sequential
"""

from __future__ import annotations

import argparse
import glob
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


def _resolve_java() -> str:
    """
    Locate the java executable.
    Priority: $JAVA_HOME/bin/java(.exe) → java on PATH → fail.
    """
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        candidate = Path(java_home) / "bin" / ("java.exe" if os.name == "nt" else "java")
        if candidate.exists():
            return str(candidate)
    on_path = shutil.which("java")
    if on_path:
        return on_path
    # Last resort: typical Windows JDK install paths
    for guess in [
        Path.home() / ".jdks",
        Path("C:/Program Files/Java"),
    ]:
        if guess.is_dir():
            for jdk in sorted(guess.iterdir(), reverse=True):
                candidate = jdk / "bin" / "java.exe"
                if candidate.exists():
                    return str(candidate)
    raise FileNotFoundError(
        "Could not locate 'java'. Set JAVA_HOME or add a JDK to PATH."
    )


# ── JAR discovery ──────────────────────────────────────────────────────────

def find_jar(jar_pattern: str | None = None) -> str:
    """
    Return path to the MATSim JAR with dependencies.
    Searches target/ if no explicit pattern is given.
    """
    if jar_pattern:
        matches = glob.glob(jar_pattern)
        if matches:
            return matches[0]

    # Default: search in target/
    # The shade plugin writes to project root (not target/)
    patterns = [
        "matsim-example-project-*.jar",
        "target/*-with-dependencies.jar",
        "target/*.jar",
    ]
    for pat in patterns:
        matches = glob.glob(pat)
        if matches:
            wdep = [m for m in matches if "with-dependencies" in m]
            return (wdep or matches)[0]

    raise FileNotFoundError(
        "MATSim JAR not found. Run: mvnw.cmd package -DskipTests (Windows) "
        "or ./mvnw package -DskipTests (Linux/Mac)\n"
        "Expected at project root: matsim-example-project-*.jar"
    )


# ── Single run ─────────────────────────────────────────────────────────────

def run_one(
    config_path: str,
    jar_path: str,
    jvm_heap: str = "8g",
    verbose: bool = True,
) -> dict:
    """
    Run one MATSim scenario. Returns a result dict with timing and exit code.
    """
    # MATSim app uses the 'run' subcommand (picocli CLI)
    java_exe = _resolve_java()
    cmd = [
        java_exe,
        f"-Xmx{jvm_heap}",
        "-jar", jar_path,
        "run",
        "--config", config_path,
    ]

    run_name = Path(config_path).stem
    if verbose:
        print(f"\n[runner] Starting: {run_name}")
        print(f"  cmd: {' '.join(cmd)}")

    t0 = time.time()
    result = subprocess.run(cmd, capture_output=not verbose)
    elapsed = time.time() - t0

    ok = result.returncode == 0
    status = "OK" if ok else f"FAILED (exit {result.returncode})"
    print(f"[runner] {run_name}: {status}  ({elapsed/60:.1f} min)")

    if not ok and not verbose:
        print("[runner] stderr (last 20 lines):")
        stderr_lines = (result.stderr or b"").decode(errors="replace").splitlines()
        for line in stderr_lines[-20:]:
            print(f"  {line}")

    return {
        "run_name": run_name,
        "config_path": config_path,
        "exit_code": result.returncode,
        "elapsed_s": elapsed,
        "ok": ok,
    }


# ── Batch runner ───────────────────────────────────────────────────────────

def _output_events_for_config(config_path: str) -> Path | None:
    """Locate the expected output events file for a config.

    Reads the outputDirectory param from the config itself (works for both the
    toy and the Rotterdam layout instead of hard-coding output/ipft_toy_runs).
    Returns the first existing output_events.xml.{zst,gz} or, if none exists
    yet, the .zst path (so existence checks simply report 'not run yet').
    """
    import xml.etree.ElementTree as ET
    out_dir = None
    try:
        root = ET.parse(config_path).getroot()
        for m in root.iter("module"):
            if m.get("name") in ("controller", "controler"):
                for p in m.iter("param"):
                    if p.get("name") == "outputDirectory":
                        out_dir = p.get("value")
                        break
    except ET.ParseError:
        pass
    if not out_dir:
        # legacy fallback: derive from filename under the toy runs dir
        stem = Path(config_path).stem
        run_name = stem[len("config_"):] if stem.startswith("config_") else stem
        out_dir = str(Path("output/ipft_toy_runs") / run_name)
    base = Path(out_dir)
    if base.is_dir():
        # optional runId prefix (e.g. MRDH_10pct.output_events.xml.zst)
        for pattern in ("*output_events.xml.zst", "*output_events.xml.gz"):
            hits = sorted(base.glob(pattern))
            if hits:
                return hits[0]
    return base / "output_events.xml.zst"


def _prune_iters(config_path: str) -> None:
    """Delete the run's ITERS/ directory once the run has finished.

    A Rotterdam warm run writes 3 GB, of which 2 GB is ITERS: it.1 holds a
    byte-identical copy of the events file that MATSim also puts in the run
    root, and it.0 holds iteration 0, which nothing reads. The pipeline only
    ever opens the root output_events.xml.zst (ITERS is a fallback for when
    that is missing) plus co2_totals.csv, both of which are left untouched.

    Without this a 60-run batch needs 180 GB instead of 60 and fills the disk
    part-way through. Pruning per run also keeps the peak flat rather than
    letting it build up and then cleaning at the end.
    """
    import shutil
    out_dir = _output_events_for_config(config_path).parent
    iters = out_dir / "ITERS"
    if not iters.is_dir():
        return
    # Refuse to prune unless the file the pipeline actually reads is present,
    # so an interrupted or failed run never loses its only copy.
    if not any(out_dir.glob("*output_events.xml.zst")):
        print(f"[runner] NOT pruning {iters}: no output_events in the run root")
        return
    freed = sum(f.stat().st_size for f in iters.rglob("*") if f.is_file())
    shutil.rmtree(iters, ignore_errors=True)
    print(f"[runner] pruned ITERS ({freed/2**30:.1f} GB) from {out_dir.name}")


def run_all(
    config_dir: str = "scenarios/ipft_toy/generated",
    jar_path: str | None = None,
    config_filter: str | None = None,
    jvm_heap: str = "8g",
    verbose: bool = False,
    skip_existing: bool = False,
    prune_iters: bool = True,
) -> list[dict]:
    """
    Run all configs in config_dir (optionally filtered by substring).

    config_filter: substring to match in config filename, e.g. 'peak', 'alpha050'
    skip_existing: if True, skip runs whose output_events.xml.zst already exists
    """
    jar = jar_path or find_jar()
    print(f"[runner] Using JAR: {jar}")

    configs = sorted(glob.glob(f"{config_dir}/config_*.xml"))
    if config_filter:
        # A plain substring cannot select one variant AND one weight: "medium"
        # also matches the RWARM and RDWELLBAY configs, i.e. it would launch
        # three times the intended batch. A filter containing * or ? is treated
        # as a glob instead, so "*RDWELLBLOCKING*medium*" selects exactly the
        # 20 medium cells of the blocking variant.
        if any(ch in config_filter for ch in "*?["):
            import fnmatch
            configs = [c for c in configs
                       if fnmatch.fnmatch(Path(c).name, config_filter)]
        else:
            configs = [c for c in configs if config_filter in Path(c).name]

    if not configs:
        print(f"[runner] No configs found in {config_dir} (filter={config_filter!r})")
        return []

    if skip_existing:
        before = len(configs)
        configs = [c for c in configs if not _output_events_for_config(c).exists()]
        skipped = before - len(configs)
        if skipped:
            print(f"[runner] Skipping {skipped} runs with existing output_events.xml.zst")

    if not configs:
        print("[runner] Nothing to run (all outputs already present).")
        return []

    print(f"[runner] {len(configs)} runs to execute")
    total_t0 = time.time()
    results = []

    for config in configs:
        r = run_one(config, jar, jvm_heap=jvm_heap, verbose=verbose)
        if r["ok"] and prune_iters:
            _prune_iters(config)
        results.append(r)

    total_elapsed = time.time() - total_t0
    n_ok = sum(1 for r in results if r["ok"])
    print(f"\n[runner] Completed {n_ok}/{len(results)} runs in {total_elapsed/60:.1f} min")

    failed = [r for r in results if not r["ok"]]
    if failed:
        print("[runner] FAILED runs:")
        for r in failed:
            print(f"  {r['run_name']}")

    return results


# ── CLI ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Run MATSim scenarios")
    p.add_argument("--jar", default=None,
                   help="Path to MATSim JAR (auto-detected from target/ if omitted)")
    p.add_argument("--config", default=None,
                   help="Run a single config file instead of the full batch")
    p.add_argument("--scenario", default=None,
                   choices=["toy", "rotterdam", "rotterdam_L87"],
                   help="Scenario preset — sets the default --config-dir")
    p.add_argument("--config-dir", default=None,
                   help="Directory containing generated config files "
                        "(default: scenarios/ipft_toy/generated, or the "
                        "preset's generated dir when --scenario is given)")
    p.add_argument("--filter", default=None,
                   help="Only run configs whose filename contains this substring")
    p.add_argument("--heap", default="8g",
                   help="JVM heap size (default: 8g)")
    p.add_argument("--verbose", action="store_true",
                   help="Stream MATSim stdout to console (noisy but useful for debugging)")
    p.add_argument("--skip-existing", action="store_true",
                   help="Skip runs whose output_events.xml.zst already exists")
    p.add_argument("--keep-iters", action="store_true",
                   help="Keep each run's ITERS/ directory. By default it is deleted "
                        "once the run finishes: on Rotterdam it is 2 of the 3 GB a run "
                        "writes, holding a byte-identical copy of the root events file "
                        "plus iteration 0, neither of which the pipeline reads. Keeping "
                        "it turns a 60-run batch from 60 GB into 180 GB.")
    args = p.parse_args()

    try:
        jar = find_jar(args.jar)
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    config_dir = args.config_dir
    if config_dir is None:
        if args.scenario:
            sys.path.insert(0, str(Path(__file__).parent))
            from scenario_presets import get_preset
            config_dir = get_preset(args.scenario).generated_dir
        else:
            config_dir = "scenarios/ipft_toy/generated"

    if args.config:
        r = run_one(args.config, jar, jvm_heap=args.heap, verbose=args.verbose)
        sys.exit(0 if r["ok"] else 1)
    else:
        results = run_all(
            config_dir=config_dir,
            jar_path=jar,
            config_filter=args.filter,
            jvm_heap=args.heap,
            verbose=args.verbose,
            skip_existing=args.skip_existing,
            prune_iters=not args.keep_iters,
        )
        sys.exit(0 if all(r["ok"] for r in results) else 1)
