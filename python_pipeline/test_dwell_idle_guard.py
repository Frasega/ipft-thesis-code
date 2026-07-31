"""
Guard tests for Term C's measured-idle path (dwell simulated inside MATSim).

No MATSim run or parse needed — these exercise the branch that decides whether
the freight idle is charged a priori or measured from the stop events. They
exist because the dangerous failure here is SILENT: pointing --dwell-in-matsim
at pre-dwell runs would make the measured extra standing collapse to ~0 and
quietly delete Term C's whole idle component (20.76 kg/day at alpha=1).

Run:  python python_pipeline/test_dwell_idle_guard.py
Exit code 0 = all guards behave; 1 = a guard failed.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd

from term_c import compute_term_c_for_bus

# Minimal 3-link "route" for one bus; only the idle branch is under test.
df = pd.DataFrame({
    "vehicle_id": ["veh_1_bus"] * 3,
    "link_id": ["L1", "L2", "L3"],
    "v_mean_ms": [8.0, 8.0, 8.0],
    "time_entered_s": [0.0, 100.0, 200.0],
    "travel_time_s": [100.0, 100.0, 100.0],
})
COMMON = dict(vmean_df=df, bus_id="veh_1_bus", pax_timeline={},
              total_freight_kg_per_day=47000.0, n_pickup_stops=8,
              n_freight_units_per_day=4700.0, bus_trips_per_day=98)


def case(name: str, expect: str, **kw) -> bool:
    try:
        r = compute_term_c_for_bus(**COMMON, **kw)
        got = ("ok", r["idle_mode"], round(r["extra_dwell_s_per_trip"], 1))
    except ValueError as e:
        got = ("raised", str(e)[:70])
    passed = got[0] == expect
    print(f"{'PASS' if passed else '**FAIL**'}  {name}\n        -> {got}")
    return passed


results = []

# 1. Default: a-priori convention, full 8 stops x 40 s = 319.8 s/trip.
results.append(case("a-priori default", "ok"))

# 2. Measured requested but no standing data -> must raise, never fall back.
results.append(case("measured, standing data missing", "raised",
                    use_measured_idle=True))

# 3. Measured, fleet present but extra ~0 -> the pre-dwell-runs trap.
base = {"veh_1_bus": {"L2": 30.0}, "veh_2_bus": {"L2": 30.0}}
scen_flat = {"veh_1_bus": {"L2": 30.5}, "veh_2_bus": {"L2": 30.0}}
results.append(case("measured, extra ~0 (pre-dwell runs)", "raised",
                    use_measured_idle=True, stop_standing_baseline=base,
                    stop_standing_scenario=scen_flat))

# 4. Measured, realistic extra (dwell in schedule, slack absorbed some of it).
scen_real = {"veh_1_bus": {"L2": 210.0}, "veh_2_bus": {"L2": 230.0}}
results.append(case("measured, realistic extra 190 s", "ok",
                    use_measured_idle=True, stop_standing_baseline=base,
                    stop_standing_scenario=scen_real))

# 5. No vehicle in common between the two runs -> must raise.
results.append(case("measured, no shared vehicle", "raised",
                    use_measured_idle=True,
                    stop_standing_baseline={"veh_a": {"L": 1.0}},
                    stop_standing_scenario={"veh_b": {"L": 9.0}}))

# 6. Zero freight (alpha=0-like): a-priori is 0, the guard must not divide by it.
r = compute_term_c_for_bus(**{**COMMON, "total_freight_kg_per_day": 0.0,
                              "n_freight_units_per_day": 0.0},
                           use_measured_idle=True,
                           stop_standing_baseline=base,
                           stop_standing_scenario=scen_flat)
print(f"PASS  zero-freight guard, no crash -> idle_mode={r['idle_mode']}, "
      f"dwell={r['extra_dwell_s_per_trip']}")
results.append(True)

print("\nALL PASS" if all(results) else "\nSOME FAILED")
sys.exit(0 if all(results) else 1)
