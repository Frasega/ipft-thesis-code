"""
Longitudinal vehicle dynamics formula for CO2 calculation.

Implements the power-based emission model from:
  Zhai, Frey & Rouphail (2008) Environ. Sci. Technol. — PEMS-validated VSP for diesel transit buses
  Yang & Liu (2022) Scientific Reports — weight-dependent energy with passenger load

Formula (per-second timestep, 1 Hz):

    P(t) = [f·M·g·v(t) + ½·ρ·CD·A·v(t)³ + M·a(t)·v(t) + M·g·sin(θ)·v(t)] / ηt

where:
  f   = rolling resistance coefficient
  M   = total mass (kg): tare + passengers + freight
  g   = 9.81 m/s²
  v   = speed (m/s)
  ρ   = 1.225 kg/m³
  CD  = aerodynamic drag coefficient
  A   = frontal area (m²)
  a   = acceleration (m/s²)
  θ   = road gradient (0 for flat Rotterdam)
  ηt  = fuel-to-wheel drivetrain efficiency

Energy → CO2:
  E [J] = Σ P(t)·Δt  for P(t) > 0 (traction phases only; diesel has no regeneration)
  CO2_running [kg] = (E / 1e6 / calorific_value_MJ_per_L) × CO2_factor_kg_per_L

Idle at bus stops (separate term, diesel engine running at v=0):
  CO2_idle [kg] = idle_fuel_rate [L/s] × dwell_time [s] × CO2_factor_kg_per_L
"""

import numpy as np

from parameters import (
    AIR_DENSITY_KG_M3,
    BUS_DRIVETRAIN_EFF,
    BUS_FRONTAL_AREA_M2,
    BUS_CD,
    BUS_ROLLING_RESISTANCE,
    BUS_IDLE_FUEL_RATE_L_PER_S,
    CALORIFIC_VALUE_DIESEL_MJ_PER_L,
    CO2_FACTOR_KG_PER_L,
    GRAVITY_M_S2,
)


# ── Core power formula ─────────────────────────────────────────────────────

def compute_power_array(
    v_ms: np.ndarray,
    a_ms2: np.ndarray,
    mass_kg: float,
    cd: float,
    frontal_area_m2: float,
    rolling_coeff: float,
    drivetrain_eff: float,
    road_grade_rad: float = 0.0,
) -> np.ndarray:
    """
    Compute instantaneous fuel power P(t) [W] at each 1 Hz sample.

    Parameters
    ----------
    v_ms : speed array (m/s), shape (N,)
    a_ms2 : acceleration array (m/s²), shape (N,) — must match v_ms length
    mass_kg : total vehicle mass
    cd, frontal_area_m2, rolling_coeff, drivetrain_eff : vehicle parameters
    road_grade_rad : road gradient in radians (0 for flat)

    Returns
    -------
    P : fuel power array [W], shape (N,). Negative values = braking/coasting.
    """
    g = GRAVITY_M_S2
    rho = AIR_DENSITY_KG_M3

    rolling = rolling_coeff * mass_kg * g * v_ms
    aero = 0.5 * rho * cd * frontal_area_m2 * v_ms ** 3
    accel = mass_kg * a_ms2 * v_ms
    grade = mass_kg * g * np.sin(road_grade_rad) * v_ms

    return (rolling + aero + accel + grade) / drivetrain_eff


# ── CO2 from traction energy ───────────────────────────────────────────────

def energy_to_co2_kg(energy_joules: float) -> float:
    """Convert traction energy [J] to CO2 [kg] via diesel calorific value."""
    energy_mj = energy_joules / 1e6
    liters = energy_mj / CALORIFIC_VALUE_DIESEL_MJ_PER_L
    return liters * CO2_FACTOR_KG_PER_L


def compute_co2_running(
    v_ms: np.ndarray,
    a_ms2: np.ndarray,
    mass_kg: float,
    cd: float = BUS_CD,
    frontal_area_m2: float = BUS_FRONTAL_AREA_M2,
    rolling_coeff: float = BUS_ROLLING_RESISTANCE,
    drivetrain_eff: float = BUS_DRIVETRAIN_EFF,
    road_grade_rad: float = 0.0,
) -> float:
    """
    Compute CO2_running [kg] for a 1 Hz v(t), a(t) speed profile.

    Only traction phases (P > 0) contribute to fuel consumption.
    Braking and coasting are P ≤ 0 → zero fuel (diesel, no regeneration).

    Parameters default to bus values; pass van parameters for van calculation.
    """
    P = compute_power_array(
        v_ms, a_ms2, mass_kg, cd, frontal_area_m2, rolling_coeff, drivetrain_eff, road_grade_rad
    )
    # Traction energy: sum P(t)·Δt with Δt=1s, P>0 only
    E_joules = float(np.sum(P[P > 0]) * 1.0)
    return energy_to_co2_kg(E_joules)


# ── Idle CO2 (bus stops, diesel engine running at v=0) ────────────────────

def compute_co2_idle(
    dwell_time_s: float,
    idle_rate_l_per_s: float = BUS_IDLE_FUEL_RATE_L_PER_S,
) -> float:
    """
    CO2 from diesel engine idling at bus stop.

    Only the EXTRA dwell time for freight unloading should be passed here —
    the normal passenger dwell (~3 min/stop already in MATSim v_mean) cancels
    when taking the Term C delta (CO2_with_freight − CO2_without_freight).
    """
    liters = idle_rate_l_per_s * dwell_time_s
    return liters * CO2_FACTOR_KG_PER_L


# ── Acceleration array from speed profile ─────────────────────────────────

def speed_to_accel(v_ms: np.ndarray) -> np.ndarray:
    """
    Compute 1 Hz acceleration array from speed array.

    a[i] = v[i] − v[i−1]  (finite difference at 1 Hz → Δt = 1 s)
    First element: a[0] = 0 (vehicle assumed at rest or in steady state at start).
    Same length as v_ms.
    """
    a = np.empty_like(v_ms)
    a[0] = 0.0
    a[1:] = np.diff(v_ms)
    return a


# ── Speed-change reconstruction for vans ──────────────────────────────────

def build_van_speed_profile(
    link_sequence: list[tuple[str, float, float]],
) -> tuple[np.ndarray, np.ndarray]:
    """
    Build a 1 Hz v(t), a(t) profile for a van using the speed-change method.

    Each link is modelled as constant speed. Speed changes instantaneously
    at link boundaries (Kickhöfer & Kern 2015 — speed-change method for MATSim).

    Parameters
    ----------
    link_sequence : list of (link_id, v_mean_ms, travel_time_s)
        Ordered sequence of links along the van route.

    Returns
    -------
    v_ms : speed array [m/s] at 1 Hz
    a_ms2 : acceleration array [m/s²] at 1 Hz (same length)
    """
    segments = []
    for _, v, t in link_sequence:
        n_samples = max(1, int(round(t)))  # number of 1-second samples on this link
        segments.append(np.full(n_samples, v))

    if not segments:
        return np.array([0.0]), np.array([0.0])

    v_ms = np.concatenate(segments)
    a_ms2 = speed_to_accel(v_ms)
    return v_ms, a_ms2


# ── Standalone test ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import matplotlib.pyplot as plt

    # Synthesise a simple trapezoid: accel 0→10 m/s, cruise, decel back to 0
    dt = 1.0  # 1 Hz
    v_accel = np.arange(0, 10, 1.0)        # 0..9 m/s
    v_cruise = np.full(30, 10.0)
    v_decel = np.arange(10, 0, -1.0)       # 10..1 m/s
    v_dwell = np.zeros(20)
    v = np.concatenate([v_accel, v_cruise, v_decel, v_dwell])
    a = speed_to_accel(v)

    M = 12000  # kg bus
    P = compute_power_array(v, a, M, BUS_CD, BUS_FRONTAL_AREA_M2,
                             BUS_ROLLING_RESISTANCE, BUS_DRIVETRAIN_EFF)
    co2 = compute_co2_running(v, a, M)

    print(f"Test trip CO2_running: {co2*1000:.2f} g")
    print(f"Max power: {P.max()/1000:.1f} kW  |  Min power: {P.min()/1000:.1f} kW")
    print(f"Traction phases: {(P>0).sum()}s / {len(P)}s total")

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
    ax1.plot(v, label="v(t) m/s"); ax1.set_ylabel("Speed (m/s)"); ax1.legend()
    ax2.plot(P / 1000, color="orange", label="P(t) kW")
    ax2.axhline(0, color="k", lw=0.5)
    ax2.set_ylabel("Fuel power (kW)"); ax2.set_xlabel("Time (s)"); ax2.legend()
    plt.tight_layout()
    plt.savefig("emission_formula_test.png", dpi=120)
    print("Saved emission_formula_test.png")
