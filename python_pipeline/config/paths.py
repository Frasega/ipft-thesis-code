"""Where things are on THIS machine, and how to find a file inside a MATSim run.

Two facts were written into the source in a couple of dozen places, and both are
properties of the installation rather than of the model:

    the output root     'D:/TesiOutputs' appeared in 17 modules
    the runId prefix    'MRDH_10pct.' appeared in 20, because MATSim prepends the
                        runId to every file it writes, and this scenario sets one

Neither is portable. The first breaks on any machine without that drive; the second
breaks on any city whose config sets a different runId, or none - and it breaks
QUIETLY, because a missing file usually surfaces as an empty result rather than an
error. This module is the single place both are resolved.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

# The value that was compiled into the source before config/machine.yaml existed.
# Kept ONLY as the last-resort fallback described in output_root().
_LEGACY_DEFAULT = "D:/TesiOutputs"


@lru_cache(maxsize=1)
def output_root() -> Path:
    """The directory MATSim run outputs live in.

    Resolution order, highest first:
        1. IPFT_OUTPUT_ROOT in the environment  - the pre-existing escape hatch
        2. config/machine.yaml                  - the documented place
        3. config/machine.example.yaml          - a fresh checkout
        4. the historical literal, with a warning

    Step 4 is deliberately a warning and not an exception. This function is called
    at import time by scenario_presets, so raising would make every script in the
    project fail to start because of a missing settings file - a worse failure than
    carrying on with the value the code used yesterday. It is loud, and it can only
    happen if the committed example file has been deleted.
    """
    env = os.environ.get("IPFT_OUTPUT_ROOT")
    if env:
        return Path(env)
    try:
        from . import loader
        return Path(loader.load_machine().value("output_root"))
    except Exception as exc:  # noqa: BLE001 - never block an import over settings
        print(f"[config] could not read the machine settings ({type(exc).__name__}: "
              f"{exc}); falling back to {_LEGACY_DEFAULT}. Copy "
              f"config/machine.example.yaml to config/machine.yaml, or set "
              f"IPFT_OUTPUT_ROOT.")
        return Path(_LEGACY_DEFAULT)


@lru_cache(maxsize=1)
def jar_path() -> Path:
    """The MATSim jar, as an absolute path."""
    from . import loader
    declared = Path(loader.load_machine().value("jar"))
    return declared if declared.is_absolute() else _ROOT / declared


@lru_cache(maxsize=1)
def java_home() -> str | None:
    """The JDK declared in machine.yaml, or None to fall back to $JAVA_HOME / PATH.

    Optional and often absent: on a workstation the JDK on PATH is the right one.
    It exists for machines where it is not - a cluster whose module system offers
    an older JDK than the one the jar was compiled against, where the run fails
    with UnsupportedClassVersionError and nothing says which java was picked.
    """
    from . import loader
    try:
        declared = loader.load_machine().value("java_home")
    except Exception:  # noqa: BLE001 - never block a run over an optional setting
        return None
    return str(declared) if declared else None


@lru_cache(maxsize=1)
def default_heap() -> str:
    from . import loader
    return str(loader.load_machine().value("heap"))


class RunFileNotFound(FileNotFoundError):
    """A file was expected inside a run directory and the directory says otherwise."""


def run_file(run_dir: str | Path, basename: str, required: bool = True) -> Path | None:
    """Find `<runId.>basename` inside a MATSim run directory.

    MATSim prepends the config's runId to everything it writes, so the same file is
    'MRDH_10pct.output_events.xml.zst' in one scenario and 'output_events.xml.zst' in
    another. Twenty modules hard-coded the Rotterdam prefix, which is why they only
    ever worked on Rotterdam.

    The unprefixed name is tried first, then any single prefixed match. More than one
    match is an error rather than a guess: two runIds in one directory means the
    directory holds two runs, and picking either silently would attribute one run's
    numbers to the other.
    """
    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        if not required:
            return None
        raise RunFileNotFound(f"{run_dir} is not a directory")

    exact = run_dir / basename
    if exact.exists():
        return exact

    matches = sorted(p for p in run_dir.glob(f"*.{basename}") if p.is_file())
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise RunFileNotFound(
            f"{run_dir} holds {len(matches)} files matching *.{basename} "
            f"({', '.join(p.name for p in matches)}). That means more than one runId "
            f"wrote into this directory; picking one would attribute its numbers to "
            f"the other run.")
    if not required:
        return None

    present = sorted(p.name for p in run_dir.iterdir() if p.is_file())[:8]
    raise RunFileNotFound(
        f"no {basename} (with or without a runId prefix) in {run_dir}.\n"
        f"  the directory holds: {', '.join(present) if present else '(no files)'}\n"
        f"  a run that was interrupted before writing its output looks exactly like "
        f"this - check the run finished before trusting anything derived from it.")


def run_path(run_dir: str | Path, basename: str) -> Path:
    """Like run_file, but never raises — for module-level constants.

    Several diagnostics build their input paths at import time, as plain strings, and
    only open them later. Making those raise would mean a script could no longer even
    be IMPORTED on a machine where the run directory is absent, which is a worse
    failure than the FileNotFoundError the later open() already gives. So: return the
    prefixed file when it can be identified, and otherwise the unprefixed name, whose
    absence will be reported at the point of use with the path in the message.
    """
    run_dir = Path(run_dir)
    try:
        found = run_file(run_dir, basename, required=False)
    except RunFileNotFound:      # ambiguous prefix — let the caller hit it on open
        found = None
    return found if found is not None else run_dir / basename


# Names MATSim writes at the end of a run, in the order the pipeline prefers them.
# .zst first because that is what the campaigns produce; the others are for runs
# made with different compression settings.
_EVENT_NAMES = ("output_events.xml.zst", "output_events.xml.gz", "output_events.xml")


def find_events(run_dir: str | Path, required: bool = True) -> Path | None:
    """The final events file of a run, whatever the runId and the compression.

    Falls back to the last ITERS/it.N/*events* when no final file was written, which
    is what a run stopped before its output phase leaves behind - the case that cost
    the replanning batch nine hours on 2026-08-28.
    """
    run_dir = Path(run_dir)
    for name in _EVENT_NAMES:
        hit = run_file(run_dir, name, required=False)
        if hit is not None:
            return hit

    iters = run_dir / "ITERS"
    if iters.is_dir():
        numbered = [(int(d.name.split(".")[1]), d) for d in iters.glob("it.*")
                    if d.is_dir() and d.name.split(".")[-1].isdigit()]
        if numbered:
            _, last = max(numbered)
            for candidate in sorted(last.glob("*events.xml*")):
                print(f"[paths] {run_dir.name}: no final events file; using "
                      f"{candidate.relative_to(run_dir)} from the last iteration. "
                      f"This run did not complete its output phase.")
                return candidate

    if not required:
        return None
    raise RunFileNotFound(
        f"no events file in {run_dir} (looked for {', '.join(_EVENT_NAMES)}, with or "
        f"without a runId prefix, and in ITERS/it.N/).")
