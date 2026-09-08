"""Regenerate the committed input artefacts and prove they come back identical.

Step 0 of the parameter-driven restructure, and the half of the safety net the preset
snapshot does not cover. snapshot_presets.py proves the DECLARATION survives the move
to YAML; this proves the FILES the declaration produces are still the files the runs
on disk were made with.

Two artefacts, regenerated into a scratch directory and compared against the copies
the campaigns actually used:

  bus_stop_links.txt   the link set Term A's bus-stop row is measured on
  dwell_schedules/     the transit schedules whose minimumStopDuration IS the
                       freight handling time — an INPUT to MATSim, not a
                       post-processing lever

Gzip comparison is done on the DECOMPRESSED bytes on purpose: gzip stores the source
mtime in its header, so two byte-identical schedules written a minute apart differ in
the first bytes of the file and a naive diff would report a change that is not one.

    python python_pipeline/tests/check_generators_reproducible.py
    python python_pipeline/tests/check_generators_reproducible.py --skip-dwell

Run from the project root. Writes nothing outside the scratch directory.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _digest(path: Path) -> str:
    """SHA-256 of the file's CONTENT, transparently decompressing .gz.

    See the module docstring: the gzip header carries an mtime, so hashing the raw
    file would compare when it was written, not what it says.
    """
    opener = gzip.open if path.suffix == ".gz" else open
    h = hashlib.sha256()
    with opener(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *args], cwd=_ROOT,
                          capture_output=True, text=True)


def compare(label: str, produced: Path, committed: Path, failures: list[str]) -> None:
    if not committed.exists():
        failures.append(f"{label}: nothing to compare against — {committed} is missing")
        return
    if not produced.exists():
        failures.append(f"{label}: the generator wrote no {produced.name}")
        return
    a, b = _digest(committed), _digest(produced)
    if a == b:
        print(f"  [same] {label:52s} {a[:12]}")
    else:
        failures.append(f"{label}: REGENERATED CONTENT DIFFERS\n"
                        f"      on disk    {a}  {committed}\n"
                        f"      regenerated {b}  {produced}")


def check_bus_stop_links(scratch: Path, failures: list[str]) -> None:
    print("\nbus_stop_links.txt")
    for scenario, committed in [
        ("rotterdam", _ROOT / "scenarios/ipft_rotterdam/bus_stop_links.txt"),
        ("rotterdam_L87",
         _ROOT / "scenarios/ipft_rotterdam/generated_L87/bus_stop_links.txt"),
    ]:
        out = scratch / f"bus_stop_links_{scenario}.txt"
        proc = _run(["python_pipeline/make_bus_stop_links.py",
                     "--scenario", scenario, "--out", str(out)])
        if proc.returncode != 0:
            failures.append(f"{scenario}: make_bus_stop_links exited "
                            f"{proc.returncode}\n{proc.stdout[-1500:]}{proc.stderr[-1500:]}")
            continue
        compare(f"{scenario} bus_stop_links", out, committed, failures)


def check_dwell_schedules(scratch: Path, failures: list[str]) -> None:
    """Line 44, headline variant (blocking), the five alphas of the thesis surface."""
    print("\ndwell_schedules/ (rotterdam, blocking)")
    committed_dir = _ROOT / "scenarios/ipft_rotterdam/dwell_schedules"
    out_dir = scratch / "dwell_schedules"
    proc = _run(["python_pipeline/make_dwell_schedules.py",
                 "--scenario", "rotterdam", "--blocking", "true",
                 "--out-dir", str(out_dir)])
    if proc.returncode != 0:
        failures.append(f"make_dwell_schedules exited {proc.returncode}\n"
                        f"{proc.stdout[-2000:]}{proc.stderr[-2000:]}")
        return
    produced = sorted(out_dir.glob("ptSchedule_dwell_alpha*_blocking.xml.gz"))
    if not produced:
        failures.append(f"make_dwell_schedules wrote no schedule into {out_dir}")
        return
    for path in produced:
        compare(path.name, path, committed_dir / path.name, failures)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--skip-dwell", action="store_true",
                    help="only check bus_stop_links (the dwell schedules take minutes)")
    ap.add_argument("--keep", action="store_true",
                    help="keep the scratch directory for inspection")
    args = ap.parse_args()

    scratch = Path(tempfile.mkdtemp(prefix="ipft_repro_"))
    failures: list[str] = []
    print(f"scratch: {scratch}")
    try:
        check_bus_stop_links(scratch, failures)
        if not args.skip_dwell:
            check_dwell_schedules(scratch, failures)
    finally:
        if args.keep:
            print(f"\nscratch kept at {scratch}")
        else:
            shutil.rmtree(scratch, ignore_errors=True)

    print()
    if failures:
        print(f"{len(failures)} PROBLEM(S):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("every regenerated artefact is byte-identical to the committed one")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
