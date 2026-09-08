"""
Tests for the van/bus NOx calculation.

The factors are no longer a placeholder: euro_factors reads the EMEP/EEA
Guidebook Appendix 4 workbook and evaluates equation (25). So these tests check
three different kinds of thing, and the first is the one that matters most:

  THE FORMULA    that equation (25), as implemented here, reproduces the EF the
                 workbook itself publishes. This is not a unit test of our
                 arithmetic - it is the check that we read the source right, on
                 every row the source pre-computes.

  THE STRUCTURE  that Term C moves in the right direction on each axis (more
                 mass, and a lower speed from the extra dwell), that the two
                 axes are reported separately with their interaction, and that
                 delta-bus-km stays exactly zero.

  THE REFUSALS   that a Euro class the workbook does not contain, or a load
                 outside the published range, RAISES instead of quietly
                 producing a number. A silent zero would be the worst failure
                 here: Term C has a legitimate zero (delta-bus-km), so a zero
                 NOx would look like a result rather than a missing input.

Usage:
    python python_pipeline/test_nox_plumbing.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd

import euro_factors as EF
import term_b
import term_c

PASS, FAIL = [], []


def check(name: str, got, want, tol: float = 0.0) -> None:
    ok = (abs(got - want) <= tol) if isinstance(got, (int, float)) and \
        isinstance(want, (int, float)) else (got == want)
    (PASS if ok else FAIL).append(name)
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}: {got!r}"
          + ("" if ok else f"  (expected {want!r})"))


def check_true(name: str, cond, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}{(' — ' + detail) if detail else ''}")


def check_raises(name: str, fn, exc=EF.FactorNotSourced) -> None:
    try:
        fn()
    except exc:
        PASS.append(name)
        print(f"  ok    {name} (raised {exc.__name__})")
        return
    except Exception as e:                                      # noqa: BLE001
        FAIL.append(name)
        print(f"  FAIL  {name}: raised {type(e).__name__} instead of {exc.__name__}")
        return
    FAIL.append(name)
    print(f"  FAIL  {name}: returned a value instead of raising")


# ── fixtures ───────────────────────────────────────────────────────────────

def _van_df(v_ms: float = 5.0, n_links: int = 4, link_prefix: str = "L") -> pd.DataFrame:
    """One van over n_links links, each taking 10 s at v_ms."""
    return pd.DataFrame({
        "vehicle_id": [f"{term_b.VAN_ID_PREFIX}1"] * n_links,
        "link_id": [f"{link_prefix}{i}" for i in range(n_links)],
        "v_mean_ms": [v_ms] * n_links,
        "travel_time_s": [10.0] * n_links,
        "time_entered_s": [i * 10.0 for i in range(n_links)],
    })


def _term_c_result(extra_dwell_s: float = 133.0) -> dict:
    """The subset of a Term C result the NOx side reads, with a trip geometry
    close to line 44: about 10 km at roughly 17.5 km/h before the freight dwell."""
    return {
        "term_c_kg_per_day": 14.4,
        "bus_km_per_trip": 10.0,
        "bus_running_time_s_per_trip": 1900.0,
        "bus_standing_s_per_trip_baseline": 160.0,
        "extra_dwell_s_per_trip": extra_dwell_s,
        "freight_per_trip_kg": 480.0,
    }


# ── 1. the formula against the source ──────────────────────────────────────

print("\n1. equation (25) against the workbook's own EF column")
v = EF.validate_equation_25()
check_true("rows checked", v["rows_checked"] > 50_000, f"{v['rows_checked']:,} rows")
check_true("worst relative error < 1e-9",
           v["worst_relative_error"] < 1e-9,
           f"{v['worst_relative_error']:.2e} at {v['worst_at']}")

# ── 2. the van curve ───────────────────────────────────────────────────────

print("\n2. van NOx")
euro5, euro6 = EF.FLEET_SCENARIOS["euro5"]["van"], EF.FLEET_SCENARIOS["euro6"]["van"]
check_true("Euro 5 is dirtier than Euro 6 d/e at corridor speed",
           EF.van_nox(euro5).at(17.5) > 5 * EF.van_nox(euro6).at(17.5),
           f"{EF.van_nox(euro5).at(17.5):.3f} vs {EF.van_nox(euro6).at(17.5):.3f} g/km")
check_true("NOx per km rises as the link slows down",
           EF.van_nox(euro5).at(10.0) > EF.van_nox(euro5).at(40.0),
           f"{EF.van_nox(euro5).at(10.0):.3f} @10 vs {EF.van_nox(euro5).at(40.0):.3f} @40")
check_raises("an unknown Euro class raises", lambda: EF.van_nox("Euro 42"))

# the van tour: 4 links x 10 s at 5 m/s = 200 m, so 0.2 km
df = _van_df()
ids = [f"{term_b.VAN_ID_PREFIX}1"]
check("van km on route", term_b.van_km_on_route(df, ids), 0.2, 1e-12)
g, clamped = term_b.mean_nox_g_per_van(df, ids, euro5)
check("van NOx = curve(18 km/h) x 0.2 km",
      g, EF.van_nox(euro5).at(5.0 * 3.6) * 0.2, 1e-9)
check("no link outside the published speed range at 18 km/h", clamped, 0)

# ── 3. the same link exclusion on both currencies ──────────────────────────

print("\n3. exclude_links reaches the NOx kilometres too")
excl = {"L0"}
check("km drop by exactly one link of four",
      term_b.van_km_on_route(df, ids, excl), 0.15, 1e-12)
g_ex, _ = term_b.mean_nox_g_per_van(df, ids, euro5, excl)
check("NOx drops in the same proportion", g_ex, g * 0.75, 1e-9)

# ── 4. Term C: both axes, and the zero that must stay zero ─────────────────

print("\n4. Term C in NOx")
bus6 = EF.FLEET_SCENARIOS["euro6"]["bus"]
res = term_c.compute_term_c_nox(_term_c_result(), bus6, bus_trips_per_day=98)
check("delta bus-km is exactly zero", res["delta_bus_km_per_day"], 0.0)
check_true("the freight dwell lowers the journey speed",
           res["v_dwell_raw_kmh"] < res["v_baseline_kmh"],
           f"{res['v_baseline_kmh']:.2f} -> {res['v_dwell_raw_kmh']:.2f} km/h")
check("load basis is the thesis's own bus (80 pax x 75 kg)",
      res["bus_payload_capacity_kg"], 6000.0)
check("freight load share", round(res["d_load"], 4), 0.08)

check("the dwell is the measured idle rate times the standing hours",
      res["nox_dwell_g_per_day"],
      res["nox_idle_rate_g_per_h"] * res["nox_extra_dwell_h_per_day"], 1e-9)
check("Euro VI idle rate is the sourced one",
      res["nox_idle_rate_g_per_h"], 20.0)
check("the total is exactly mass + dwell, nothing else",
      res["term_c_nox_g_per_day"],
      res["nox_mass_component_g_per_day"] + res["nox_dwell_g_per_day"], 1e-9)
check_true("Euro VI: the mass term is positive",
           res["nox_mass_component_g_per_day"] > 0,
           f"{res['nox_mass_component_g_per_day']:.1f} g/day")
check_true("the measurement exceeds the speed-curve bracket it replaced",
           res["term_c_nox_g_per_day"] > res["legacy_term_c_nox_g_per_day_high"],
           f"{res['term_c_nox_g_per_day']:.1f} > "
           f"{res['legacy_term_c_nox_g_per_day_high']:.1f} g/day")

no_dwell = term_c.compute_term_c_nox(_term_c_result(extra_dwell_s=0.0), bus6,
                                     bus_trips_per_day=98)
check_true("with no extra dwell the total collapses onto the mass term",
           abs(no_dwell["nox_dwell_g_per_day"]) < 1e-9)
check_true("more dwell costs more NOx",
           res["term_c_nox_g_per_day"] > no_dwell["term_c_nox_g_per_day"])

# ── 5. refusals ────────────────────────────────────────────────────────────

print("\n5. refusals")
check_raises("a load above the published range raises",
             lambda: EF.bus_nox(bus6).at(17.5, 1.4))
check_raises("an unknown bus Euro class raises", lambda: EF.bus_nox("Euro 0"))
check_raises("a Term C result without the trip geometry raises",
             lambda: term_c.compute_term_c_nox({"term_c_kg_per_day": 1.0}, bus6),
             ValueError)

# A bus missing from the baseline standing data must stop the calculation, not
# be treated as standing for zero seconds: that would raise the baseline journey
# speed and overstate the whole cost.
_no_standing = _term_c_result()
_no_standing["bus_standing_s_per_trip_baseline"] = None
check_raises("a bus with no baseline standing time raises rather than assuming 0",
             lambda: term_c.compute_term_c_nox(_no_standing, bus6), ValueError)

# The clamp counter must be per cell, not a running total over the process.
_a = term_c.compute_term_c_nox(_term_c_result(), bus6, bus_trips_per_day=98)
_b = term_c.compute_term_c_nox(_term_c_result(), bus6, bus_trips_per_day=98)
check("the clamp count is per cell, not cumulative",
      _a["n_speed_clamped"], _b["n_speed_clamped"])

# ── 6. the cross-check against the physics model ───────────────────────────

print("\n6. energy-consumption cross-check (the mixed-method validation)")
check_true("the empirical route costs more than the physics model, as expected",
           res["crosscheck_ratio"] > 1,
           f"{res['crosscheck_ec_co2_kg_per_day']:.2f} kg/day empirical vs "
           f"{res['crosscheck_model_co2_kg_per_day']:.2f} from the physics model "
           f"(ratio {res['crosscheck_ratio']:.1f}x) — this gap is WHY the dwell "
           f"is priced by a measured idle rate, not read off the speed curve")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print(f"  FAILED: {f}")
    sys.exit(1)
