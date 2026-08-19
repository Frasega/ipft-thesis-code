"""
SORT driving cycle library and Lin & Niemeier (2002) micro-trip recombination.

SORT = Standardised On-Road Test (UITP 2004 brochure, Ch. IV).
Each canonical anchor is composed of THREE trapezoidal micro-trips with
distinct v_max values, not a single repeated trapezoid:

  SORT 1 (Heavy urban,  v_mean ≈ 12.1 km/h, 520 m total):
    trapeze A: v_max = 20 km/h, dwell 20 s, accel 1.03 m/s²
    trapeze B: v_max = 30 km/h, dwell 20 s, accel 0.77 m/s²
    trapeze C: v_max = 40 km/h, dwell 20 s, accel 0.62 m/s²

  SORT 2 (Easy urban,   v_mean ≈ 18.0 km/h, 920 m total):
    trapeze A: v_max = 20 km/h, dwell 20 s, accel 1.03 m/s²
    trapeze B: v_max = 40 km/h, dwell 20 s, accel 0.62 m/s²
    trapeze C: v_max = 50 km/h, dwell 20 s, accel 0.57 m/s²

  SORT 3 (Easy suburb., v_mean ≈ 25.3 km/h, 1450 m total):
    trapeze A: v_max = 30 km/h, dwell 20 s, accel 0.77 m/s²
    trapeze B: v_max = 50 km/h, dwell 10 s, accel 0.57 m/s²
    trapeze C: v_max = 60 km/h, dwell 10 s, accel 0.46 m/s²

  Synthetic   (BRT/fast corridor, v_mean ≈ 36.9 km/h, SORT3 cruise extended)
  Synthetic45 (fast corridor,     v_mean ≈ 44.5 km/h) — added 2026-06-16
  Synthetic52 (upper free-flow,   v_mean ≈ 52.8 km/h) — added 2026-06-16
    The two Synthetic45/52 anchors were added once the Rotterdam corridor was
    measured: the bus runs at 37-49 km/h on most route links, ABOVE the old top
    anchor (36.9), so fast links were being reconstructed with a too-slow,
    too-stop-and-go profile. No empirical UITP equivalent — declared as a
    construction in Ch4 (extrapolated SORT-style trapezes).

Deceleration is fixed at 0.8 m/s² for all anchors (UITP spec).
The cruise duration of each trapeze follows analytically from its UITP target
DISTANCE (d_cruise = distance − d_accel − d_decel); the resulting anchor mean is
therefore a check, not a fit. Achieved means, which are what bracketing uses:
SORT1 11.82 · SORT2 17.62 · SORT3 24.85 · Synthetic 36.92 · Synthetic45 44.49 ·
Synthetic52 52.80 km/h.

Lin & Niemeier (2002) recombination — now meaningful:
  Given a target v_mean from MATSim, find two adjacent anchors that bracket it,
  then draw + concatenate micro-trips until the time-weighted running mean
  converges within TOLERANCE_KMH of the target. Each draw comes from the slower
  anchor's trapezes when the running mean is above target and from the faster
  anchor's when it is below — the self-correcting rule applied at trapeze level.
  Because both anchors hold genuinely different trapezes (different v_max →
  different M·a·v signatures), the sequences differ every time.

Three regimes, mirroring van_cycles (the profile's mean must equal v_mean, or
the caller's t_actual/t_sort scaling is no longer distance-correct):
  below the slowest anchor -> amplitude-scaled SORT1 (creep);
  in band                  -> micro-trip recombination;
  above the fastest anchor -> constant free-flow cruise.

References:
  UITP (2004) "SORT — Standardised On-Road Test cycles", Chapter IV
    (canonical 3-trapeze structure, v_max / accel / dwell values)
  Lajunen (2014) Transp. Res. C — bus energy with SORT
  Göhlich et al. (2018) — bus driving cycle validation
  Gallet et al. (2018) Applied Energy — SORT for electric buses
  Lin & Niemeier (2002) — micro-trip recombination algorithm
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import numpy as np

# ── Recombination settings ─────────────────────────────────────────────────
TOLERANCE_KMH = 0.5          # convergence tolerance
TOLERANCE_MS = TOLERANCE_KMH / 3.6
N_MAX_DRAWS = 500             # max micro-trip draws before accepting best result
UITP_DECEL_MS2 = 0.8          # UITP deceleration (constant across anchors)
CRUISE_LEN_S = 120            # length of the free-flow cruise block above the
                              # fastest anchor (arbitrary: cancels in t_actual/t_sort)


# ── Trapeze specification (one micro-trip) ─────────────────────────────────

@dataclass
class TrapezeSpec:
    v_max_kmh: float       # peak cruise speed
    accel_ms2: float       # acceleration ramp (UITP spec)
    dwell_s: float         # dwell at end-of-trapeze stop
    distance_m: float      # total trapeze distance (UITP spec, per trapeze)
    cruise_s: int = 0      # set analytically from distance_m


# ── Anchor: a full SORT cycle = 3 trapezes back-to-back ───────────────────

@dataclass
class SortAnchor:
    name: str
    trapezes: list[TrapezeSpec]
    target_vmean_kmh: float

    # Filled in by build()
    microtrips: list[np.ndarray] = field(default_factory=list, repr=False)
    vmean_ms: float = 0.0

    @property
    def target_vmean_ms(self) -> float:
        return self.target_vmean_kmh / 3.6

    def build(self) -> None:
        """
        Compute cruise duration of each trapeze analytically from its UITP
        target distance:

            d_accel = ½ v_max² / accel
            d_decel = ½ v_max² / decel
            d_cruise = distance_m − d_accel − d_decel
            cruise_s = d_cruise / v_max

        Then store the assembled cycle and report the achieved v_mean.
        """
        for t in self.trapezes:
            v_max_ms = t.v_max_kmh / 3.6
            d_accel = 0.5 * v_max_ms ** 2 / t.accel_ms2
            d_decel = 0.5 * v_max_ms ** 2 / UITP_DECEL_MS2
            d_cruise = max(0.0, t.distance_m - d_accel - d_decel)
            t.cruise_s = max(2, int(round(d_cruise / v_max_ms))) if v_max_ms > 0 else 2

        self.microtrips = [_generate_microtrip(t) for t in self.trapezes]
        full = np.concatenate(self.microtrips)
        self.vmean_ms = float(np.mean(full))

    @property
    def full_cycle(self) -> np.ndarray:
        return np.concatenate(self.microtrips)


# ── Trapeze → 1 Hz speed array ────────────────────────────────────────────

def _generate_microtrip(spec: TrapezeSpec) -> np.ndarray:
    """
    Build one trapezoidal micro-trip at 1 Hz from a TrapezeSpec.
    Phases: accel → cruise → decel (at 0.8 m/s²) → dwell.
    """
    v_max_ms = spec.v_max_kmh / 3.6

    n_accel = max(1, int(round(v_max_ms / spec.accel_ms2)))
    v_accel = np.linspace(0.0, v_max_ms, n_accel, endpoint=False)

    n_decel = max(1, int(round(v_max_ms / UITP_DECEL_MS2)))
    v_decel = np.linspace(v_max_ms, 0.0, n_decel, endpoint=True)

    v_cruise = np.full(max(2, spec.cruise_s), v_max_ms)
    v_dwell = np.zeros(max(1, int(round(spec.dwell_s))))

    return np.concatenate([v_accel, v_cruise, v_decel, v_dwell])


# ── Canonical UITP anchors (Chapter IV of UITP 2004 brochure) ─────────────

# Trapeze distances per UITP brochure (sum to the canonical total trip length):
#   SORT1: 100 + 200 + 220 = 520 m
#   SORT2: 100 + 300 + 520 = 920 m
#   SORT3: 200 + 600 + 650 = 1450 m
#   Synthetic: extended to 1750 m for BRT-like average ~35 km/h
_ANCHOR_SPECS = [
    SortAnchor(
        name="SORT1",
        target_vmean_kmh=12.1,
        trapezes=[
            TrapezeSpec(v_max_kmh=20, accel_ms2=1.03, dwell_s=20, distance_m=100),
            TrapezeSpec(v_max_kmh=30, accel_ms2=0.77, dwell_s=20, distance_m=200),
            TrapezeSpec(v_max_kmh=40, accel_ms2=0.62, dwell_s=20, distance_m=220),
        ],
    ),
    SortAnchor(
        name="SORT2",
        target_vmean_kmh=18.0,
        trapezes=[
            TrapezeSpec(v_max_kmh=20, accel_ms2=1.03, dwell_s=20, distance_m=100),
            TrapezeSpec(v_max_kmh=40, accel_ms2=0.62, dwell_s=20, distance_m=300),
            TrapezeSpec(v_max_kmh=50, accel_ms2=0.57, dwell_s=20, distance_m=520),
        ],
    ),
    SortAnchor(
        name="SORT3",
        target_vmean_kmh=25.3,
        trapezes=[
            TrapezeSpec(v_max_kmh=30, accel_ms2=0.77, dwell_s=20, distance_m=200),
            TrapezeSpec(v_max_kmh=50, accel_ms2=0.57, dwell_s=10, distance_m=600),
            TrapezeSpec(v_max_kmh=60, accel_ms2=0.46, dwell_s=10, distance_m=650),
        ],
    ),
    SortAnchor(
        name="Synthetic",
        target_vmean_kmh=35.0,
        # SORT3-derived: extends the cruise envelope to BRT-like speeds.
        # Longer per-trapeze distances are needed than SORT3 because high v_max
        # ramps consume large d_accel + d_decel before any cruise can occur.
        trapezes=[
            TrapezeSpec(v_max_kmh=50, accel_ms2=0.57, dwell_s=10, distance_m=600),
            TrapezeSpec(v_max_kmh=60, accel_ms2=0.46, dwell_s=10, distance_m=900),
            TrapezeSpec(v_max_kmh=70, accel_ms2=0.40, dwell_s=10, distance_m=1500),
        ],
    ),
    SortAnchor(
        name="Synthetic45",
        target_vmean_kmh=45.0,
        # Fast-corridor anchor (~45 km/h). Added 2026-06-16: the measured Rotterdam
        # corridor bus speed (median 37.6 km/h line 44 / 42.4 line 99431, 90th pct
        # ~48) sits ABOVE the old top anchor (Synthetic 36.9), so ~half the bus
        # links were being clamped to a too-slow, too-stop-and-go profile.
        trapezes=[
            TrapezeSpec(v_max_kmh=55, accel_ms2=0.50, dwell_s=7, distance_m=1050),
            TrapezeSpec(v_max_kmh=60, accel_ms2=0.46, dwell_s=7, distance_m=1600),
            TrapezeSpec(v_max_kmh=65, accel_ms2=0.42, dwell_s=7, distance_m=2350),
        ],
    ),
    SortAnchor(
        name="Synthetic52",
        target_vmean_kmh=52.0,
        # Upper free-flow anchor (~52 km/h). Added 2026-06-16. Serves as the top
        # bracket so links at 45-50 km/h interpolate up instead of clamping at 45.
        trapezes=[
            TrapezeSpec(v_max_kmh=65, accel_ms2=0.42, dwell_s=7, distance_m=1600),
            TrapezeSpec(v_max_kmh=70, accel_ms2=0.40, dwell_s=7, distance_m=2400),
            TrapezeSpec(v_max_kmh=75, accel_ms2=0.37, dwell_s=7, distance_m=3600),
        ],
    ),
]


# ── Build all anchors at import time ──────────────────────────────────────

ANCHORS: list[SortAnchor] = []
for _spec in _ANCHOR_SPECS:
    _spec.build()
    ANCHORS.append(_spec)


def print_anchor_info() -> None:
    print("SORT anchor v_mean values (km/h):")
    for a in ANCHORS:
        cruise = [t.cruise_s for t in a.trapezes]
        print(f"  {a.name:>10s}: target {a.target_vmean_kmh:>4.1f}  "
              f"actual {a.vmean_ms*3.6:>4.1f}  cruise_s={cruise}")


# ── Bracketing ────────────────────────────────────────────────────────────

def _bracket(target_ms: float) -> tuple[SortAnchor, SortAnchor]:
    """Find two adjacent anchors that bracket target_ms."""
    for i in range(len(ANCHORS) - 1):
        if ANCHORS[i].vmean_ms <= target_ms <= ANCHORS[i + 1].vmean_ms:
            return ANCHORS[i], ANCHORS[i + 1]
    return ANCHORS[-2], ANCHORS[-1]


# ── Lin & Niemeier recombination ──────────────────────────────────────────

def recombine_microtrips(target_vmean_ms: float, rng_seed: int = 42) -> np.ndarray:
    """
    Reconstruct a bus speed profile at 1 Hz by randomly drawing micro-trips
    from the two bracketing anchors until the running mean matches target.

    Because each anchor now contains 3 genuinely distinct trapezes
    (different v_max → different M·a·v signature), the pooled bag of 6
    micro-trips produces real variety across draws — the algorithm is no
    longer degenerate.
    """
    lo_anchor = ANCHORS[0]
    hi_anchor = ANCHORS[-1]

    # Out of band: recombination cannot help — concatenating micro-trips never
    # averages below the slowest ingredient nor above the fastest. Same two
    # regimes as the van (van_cycles.reconstruct_van_profile), and for the same
    # reason: the caller's t_actual/t_sort scaling is only distance-correct if
    # the profile's mean equals v_mean. Returning the anchor unchanged breaks
    # that — a link crawled at 0.4 km/h would be charged as SORT1's 11.8.
    if target_vmean_ms < lo_anchor.vmean_ms:
        # Creep: amplitude-scale the slowest anchor so its mean = target.
        # Keeps the stop-and-go shape (zeros stay zeros), fixes the distance.
        return lo_anchor.full_cycle * (target_vmean_ms / lo_anchor.vmean_ms)

    if target_vmean_ms > hi_anchor.vmean_ms:
        # Free-flow: constant cruise, a(t) = 0 — no stop-and-go to invent.
        return np.full(CRUISE_LEN_S, target_vmean_ms)

    low, high = _bracket(target_vmean_ms)
    pool = low.microtrips + high.microtrips  # 6 distinct trapezes

    rng = np.random.default_rng(rng_seed)
    sequence: list[np.ndarray] = []
    running_sum_v = 0.0
    running_n_samples = 0
    best_residual = float("inf")
    best_sequence: list[np.ndarray] = []

    for _ in range(N_MAX_DRAWS):
        current_mean = running_sum_v / running_n_samples if running_n_samples > 0 else 0.0

        # Bias the draw: if too fast, sample from the slower subset of the pool;
        # if too slow, from the faster subset. This is the Lin & Niemeier
        # self-correcting rule applied at the trapeze level (not the anchor level).
        if current_mean >= target_vmean_ms:
            candidate_pool = low.microtrips
        else:
            candidate_pool = high.microtrips

        trip = candidate_pool[rng.integers(len(candidate_pool))].copy()
        sequence.append(trip)
        running_sum_v += float(np.sum(trip))
        running_n_samples += len(trip)

        current_mean = running_sum_v / running_n_samples
        residual = abs(current_mean - target_vmean_ms)
        if residual < best_residual:
            best_residual = residual
            best_sequence = [t.copy() for t in sequence]

        if residual < TOLERANCE_MS:
            break
    else:
        warnings.warn(
            f"Lin & Niemeier did not converge in {N_MAX_DRAWS} draws. "
            f"Best residual: {best_residual*3.6:.2f} km/h. "
            f"Target: {target_vmean_ms*3.6:.1f} km/h. Using best approximation."
        )
        sequence = best_sequence

    return np.concatenate(sequence)


# ── Public entry point ─────────────────────────────────────────────────────

def reconstruct_bus_profile(v_mean_ms: float, rng_seed: int = 42) -> np.ndarray:
    """
    Main entry point: given MATSim v_mean for one bus link, return a 1 Hz
    speed profile v(t) reconstructed from the SORT cycle library.
    """
    return recombine_microtrips(v_mean_ms, rng_seed=rng_seed)


# ── Standalone test / visualisation ───────────────────────────────────────

if __name__ == "__main__":
    import matplotlib.pyplot as plt

    print_anchor_info()

    targets_kmh = [10, 15, 20, 25, 30, 35]
    fig, axes = plt.subplots(len(targets_kmh), 1, figsize=(12, 2.4 * len(targets_kmh)))

    for ax, t_kmh in zip(axes, targets_kmh):
        t_ms = t_kmh / 3.6
        v_t = reconstruct_bus_profile(t_ms, rng_seed=0)
        achieved_kmh = np.mean(v_t) * 3.6
        ax.plot(v_t, lw=0.7)
        ax.set_title(f"Target {t_kmh} km/h — achieved {achieved_kmh:.1f} km/h "
                     f"({len(v_t)} s)")
        ax.set_ylabel("v (m/s)")
        ax.set_ylim(bottom=0)

    axes[-1].set_xlabel("Time (s)")
    plt.tight_layout()
    plt.savefig("sort_cycles_test.png", dpi=120)
    print("Saved sort_cycles_test.png")
