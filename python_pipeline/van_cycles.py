"""
Van kinematic reconstruction from the WLTC (WLTP) driving cycle — Term B.

Replaces the speed-change method (constant speed per link), which ignored
within-link stop-and-go and under-estimated van fuel ~3-5x. See HANDOFF.md §14.

Principle (HBEFA / EMEP): vehicle category x traffic situation -> representative
kinematic profile. The backup van is a Ford Transit Custom (category N1, LCV),
so the category-appropriate cycle is the car/LCV WLTC — the very cycle this van
is type-approved on (UN/ECE GTR No. 15 / EU 2017/1151; derived from real-world
driving, Tutuianu et al. 2015). This mirrors the bus, which uses SORT (UITP).

The WLTC phase traces (real 1 Hz data extracted from the EU JRC `wltp` package)
live in cycle_data/wltc_{low,medium,high}.csv. Phase means: Low 18.88, Medium
39.54, High 56.66 km/h.

Method (regime-aware, same length-scaling as the bus):
  - in band (Low..High mean): recombine micro-trips from the two bracketing
    anchors (Lin & Niemeier 2002) until the running mean matches the link v_mean;
  - above the fastest anchor: free-flow -> constant cruise at v_mean (no
    stop-and-go to invent);
  - below the slowest anchor: amplitude-scale the slowest anchor to the target
    mean (keeps the stop-and-go shape; mean = target so the per-link distance
    scaling stays valid). Declared (these are mostly toy creep artefacts).

The returned profile is a 1 Hz v(t) [m/s]; the caller computes CO2 over it and
scales by t_actual/t_sort to the real link length (term_b, mirroring term_c).
"""

from __future__ import annotations

import csv
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# ── Settings ───────────────────────────────────────────────────────────────
TOLERANCE_KMH = 0.5
TOLERANCE_MS = TOLERANCE_KMH / 3.6
N_MAX_DRAWS = 500
IDLE_THRESH_MS = 0.5 / 3.6          # below this = stopped (idle)
CRUISE_LEN_S = 120                  # length of the synthetic free-flow cruise block
                                    # (arbitrary; cancels in the t_actual/t_sort scaling)

_CYCLE_DIR = Path(__file__).parent / "cycle_data"


# ── Anchor = one WLTC phase ────────────────────────────────────────────────

@dataclass
class VanAnchor:
    name: str
    v_ms: np.ndarray                                   # full phase trace, 1 Hz, m/s
    microtrips: list[np.ndarray] = field(default_factory=list, repr=False)
    vmean_ms: float = 0.0

    def build(self) -> None:
        self.vmean_ms = float(np.mean(self.v_ms))
        self.microtrips = _split_microtrips(self.v_ms)

    @property
    def full_cycle(self) -> np.ndarray:
        return self.v_ms


def _split_microtrips(v_ms: np.ndarray) -> list[np.ndarray]:
    """Cut a cycle into micro-trips at the START of each stop (idle run).

    Each micro-trip = idle + acceleration + cruise + deceleration, so
    concatenating them reproduces the cycle and the realistic idle fraction is
    preserved under recombination.
    """
    moving = v_ms > IDLE_THRESH_MS
    # A stop begins where a moving sample is followed by a stopped one, OR at t=0
    # if the cycle starts stopped (WLTC Low starts with ~11 s idle).
    stop_starts = [0] if not moving[0] else []
    for i in range(1, len(v_ms)):
        if moving[i - 1] and not moving[i]:
            stop_starts.append(i)
    if not stop_starts:
        return [v_ms.copy()]
    # Segment between consecutive stop-starts; tail goes to the end.
    cuts = stop_starts + [len(v_ms)]
    segs = [v_ms[cuts[k]:cuts[k + 1]].copy() for k in range(len(cuts) - 1)]
    return [s for s in segs if len(s) > 0]


# ── Load WLTC anchors at import ────────────────────────────────────────────

def _load_phase(name: str) -> np.ndarray:
    path = _CYCLE_DIR / f"wltc_{name}.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — run the WLTC extraction "
            f"(wltp package -> cycle_data/wltc_*.csv). See HANDOFF §14.10."
        )
    v_kmh = []
    with open(path, newline="") as f:
        r = csv.reader(f)
        next(r)  # header t_s,v_kmh
        for row in r:
            v_kmh.append(float(row[1]))
    return np.asarray(v_kmh, dtype=float) / 3.6  # -> m/s


ANCHORS: list[VanAnchor] = []
for _name in ("low", "medium", "high"):
    _a = VanAnchor(name=f"WLTC_{_name}", v_ms=_load_phase(_name))
    _a.build()
    ANCHORS.append(_a)
ANCHORS.sort(key=lambda a: a.vmean_ms)


def print_anchor_info() -> None:
    print("WLTC van anchors:")
    for a in ANCHORS:
        print(f"  {a.name:>11s}: mean {a.vmean_ms*3.6:5.2f} km/h  "
              f"{len(a.v_ms):4d} s  {len(a.microtrips):2d} micro-trips")


# ── Lin & Niemeier recombination (in-band) ─────────────────────────────────

def _bracket(target_ms: float) -> tuple[VanAnchor, VanAnchor]:
    for i in range(len(ANCHORS) - 1):
        if ANCHORS[i].vmean_ms <= target_ms <= ANCHORS[i + 1].vmean_ms:
            return ANCHORS[i], ANCHORS[i + 1]
    return ANCHORS[-2], ANCHORS[-1]


def _recombine(target_ms: float, rng_seed: int) -> np.ndarray:
    low, high = _bracket(target_ms)
    rng = np.random.default_rng(rng_seed)
    sequence: list[np.ndarray] = []
    running_sum, running_n = 0.0, 0
    best_res, best_seq = float("inf"), []
    for _ in range(N_MAX_DRAWS):
        cur = running_sum / running_n if running_n else 0.0
        pool = low.microtrips if cur >= target_ms else high.microtrips
        trip = pool[rng.integers(len(pool))]
        sequence.append(trip)
        running_sum += float(np.sum(trip))
        running_n += len(trip)
        res = abs(running_sum / running_n - target_ms)
        if res < best_res:
            best_res, best_seq = res, list(sequence)
        if res < TOLERANCE_MS:
            break
    else:
        sequence = best_seq
    return np.concatenate(sequence)


# ── Public entry point ─────────────────────────────────────────────────────

def reconstruct_van_profile(v_mean_ms: float, rng_seed: int = 42) -> np.ndarray:
    """Return a 1 Hz v(t) [m/s] for a van link with the given mean speed.

    Regime-aware (see module docstring / HANDOFF §14.11):
      below slowest anchor -> amplitude-scaled slowest anchor (creep);
      in band              -> micro-trip recombination (stop-and-go);
      above fastest anchor -> constant free-flow cruise.
    The mean of the returned profile equals v_mean_ms (within tolerance), so the
    caller's t_actual/t_sort length scaling stays distance-correct.
    """
    if v_mean_ms <= 0:
        return np.zeros(1)

    slowest, fastest = ANCHORS[0], ANCHORS[-1]

    if v_mean_ms < slowest.vmean_ms:
        # Creep: scale the slowest anchor's speed so its mean = target.
        return slowest.v_ms * (v_mean_ms / slowest.vmean_ms)

    if v_mean_ms > fastest.vmean_ms:
        # Free-flow: constant cruise (no stop-and-go to invent). a(t)=0.
        return np.full(CRUISE_LEN_S, v_mean_ms)

    return _recombine(v_mean_ms, rng_seed)


if __name__ == "__main__":
    print_anchor_info()
    print("\nReconstruction check (target -> achieved mean):")
    for t_kmh in (3, 8, 12, 18, 25, 35, 45, 56, 72):
        v = reconstruct_van_profile(t_kmh / 3.6, rng_seed=1)
        regime = ("creep" if t_kmh < ANCHORS[0].vmean_ms * 3.6
                  else "cruise" if t_kmh > ANCHORS[-1].vmean_ms * 3.6
                  else "recombine")
        print(f"  {t_kmh:3d} km/h -> {np.mean(v)*3.6:5.2f} km/h  "
              f"[{regime:9s}]  len={len(v):4d}s")
