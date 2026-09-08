"""
Regression guard for the multi-pollutant recompute.

The change that added NOx/NO2/PM2.5 to recompute_link_co2 turned a single
hardcoded component into a loop. The risk that introduces is not that NOx is
wrong - validate_pollutant_recompute.py checks that against MATSim itself - but
that the CO2 columns MOVE, which would silently invalidate every background
figure already in the thesis.

These tests pin that down without needing a run:

  1. the CO2 columns are computed by exactly the same arithmetic as before,
     i.e. adding components to COMPONENTS does not perturb co2_*;
  2. every component is charged its OWN factor, so the loop does not leak one
     pollutant's factor into another's column;
  3. the step and the blend behave as documented at the threshold;
  4. the by-construction ratios hold, which is the invariant the real script
     asserts on live data.

Usage:
    python python_pipeline/test_pollutant_columns.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd

import recompute_link_co2 as R

# The real table's two rows, so the numbers below are the ones that actually run.
HBEFA = {
    "co2": {"ff": (41.66275787, 155.973587), "sg": (12.48863316, 281.2261963)},
    "nox": {"ff": (41.66275787, 0.299246997), "sg": (12.48863316, 0.548087835)},
    "no2": {"ff": (41.66275787, 0.093356177), "sg": (12.48863316, 0.17424117)},
    "pm25": {"ff": (41.66275787, 0.002823106), "sg": (12.48863316, 0.005200097)},
}

LENGTHS = {"L1": 1000.0, "L2": 500.0, "L3": 2000.0}
FREESPEEDS = {"L1": 50 / 3.6, "L2": 50 / 3.6, "L3": 50 / 3.6}


def _df():
    """Three background traversals, one of them below the stop&go threshold."""
    return pd.DataFrame([
        # fast, well above the step -> free-flow factor under 'average'
        dict(vehicle_id="p1", link_id="L1", v_mean_ms=40 / 3.6,
             time_entered_s=0.0, travel_time_s=90.0, vehicle_type="background"),
        # crawling, at/below the step -> stop&go factor under 'average'
        dict(vehicle_id="p2", link_id="L2", v_mean_ms=8 / 3.6,
             time_entered_s=0.0, travel_time_s=225.0, vehicle_type="background"),
        # mid-range, where the two rules disagree most
        dict(vehicle_id="p3", link_id="L3", v_mean_ms=25 / 3.6,
             time_entered_s=0.0, travel_time_s=288.0, vehicle_type="background"),
        # a van: must be ignored entirely (Term B recomputes it from dynamics)
        dict(vehicle_id="backup_van_1", link_id="L1", v_mean_ms=30 / 3.6,
             time_entered_s=0.0, travel_time_s=120.0, vehicle_type="van"),
        # a bus: likewise ignored
        dict(vehicle_id="veh_9", link_id="L1", v_mean_ms=20 / 3.6,
             time_entered_s=0.0, travel_time_s=180.0, vehicle_type="bus"),
    ])


def _run(components: dict[str, str]):
    """emissions_by_set with COMPONENTS temporarily restricted."""
    saved = R.COMPONENTS
    try:
        R.COMPONENTS = components
        hb = {slug: HBEFA[slug] for slug in components.values()}
        return R.emissions_by_set(_df(), LENGTHS, FREESPEEDS, hb,
                                  {"all": frozenset(LENGTHS)}).iloc[0]
    finally:
        R.COMPONENTS = saved


def test_co2_unchanged_by_adding_components():
    """Adding NOx/NO2/PM2.5 must not move a single CO2 digit."""
    only_co2 = _run({"CO2(total)": "co2"})
    all_four = _run(R.COMPONENTS)
    for col in ("co2_average_kg", "co2_fraction_kg", "vkm", "n_traversals"):
        a, b = only_co2[col], all_four[col]
        assert a == b, f"{col} moved when components were added: {a} vs {b}"
    print(f"  [ok] CO2 identical with 1 vs 4 components "
          f"(average {all_four['co2_average_kg']:.6f} kg, "
          f"fraction {all_four['co2_fraction_kg']:.6f} kg)")


def test_each_component_uses_its_own_factor():
    """A pollutant computed alone must equal the same pollutant computed together."""
    all_four = _run(R.COMPONENTS)
    for comp, slug in R.COMPONENTS.items():
        alone = _run({comp: slug})
        for suffix in ("average_kg", "fraction_kg"):
            a, b = alone[f"{slug}_{suffix}"], all_four[f"{slug}_{suffix}"]
            assert a == b, f"{slug}_{suffix} leaked: alone {a} vs together {b}"
    print("  [ok] no factor leaks between components")


def test_van_and_bus_excluded():
    """Only vehicle_type == 'background' is counted."""
    r = _run(R.COMPONENTS)
    assert r["n_traversals"] == 3, f"expected 3 background traversals, got {r['n_traversals']}"
    assert abs(r["vkm"] - 3.5) < 1e-9, f"expected 3.5 vkm, got {r['vkm']}"
    print("  [ok] van and bus traversals excluded from the background total")


def test_step_and_blend_behaviour():
    """The step picks one of two factors; the blend sits strictly between them."""
    v_sg = HBEFA["co2"]["sg"][0]
    ef_ff, ef_sg = HBEFA["co2"]["ff"][1], HBEFA["co2"]["sg"][1]

    assert R.ef_average(v_sg - 0.01, v_sg, ef_ff, ef_sg) == ef_sg
    assert R.ef_average(v_sg + 0.01, v_sg, ef_ff, ef_sg) == ef_ff

    # at free flow the blend collapses to the free-flow factor
    assert R.ef_fraction(50.0, 50.0, v_sg, ef_ff, ef_sg) == ef_ff
    # at or below the table's stop&go speed it collapses to stop&go
    assert R.ef_fraction(v_sg - 1, 50.0, v_sg, ef_ff, ef_sg) == ef_sg
    # in between it is strictly between, which is the whole point of the method
    mid = R.ef_fraction(25.0, 50.0, v_sg, ef_ff, ef_sg)
    assert ef_ff < mid < ef_sg, f"blend {mid} not between {ef_ff} and {ef_sg}"
    print(f"  [ok] step is a step, blend is continuous (25 km/h -> {mid:.2f} g/km, "
          f"between {ef_ff:.2f} and {ef_sg:.2f})")


def test_vectorised_matches_scalar():
    """
    The numpy path must reproduce the scalar reference EXACTLY.

    The scalar functions are the readable statement of what MATSim does; the
    vectorised ones exist only because the scalar ones, called once per
    traversal per component per method, exhausted memory on the big rings. If
    these two ever disagree, the scalar version is right and the fast one is
    wrong.
    """
    import numpy as np

    v_sg, ef_ff, ef_sg = 12.48863316, 155.973587, 281.2261963
    # deliberately includes the awkward cases: exactly at the stop&go speed,
    # exactly at free flow, inside the 1 km/h free-flow clamp, above free flow,
    # and a near-zero speed where the formula divides by v
    speeds = np.array([0.001, 0.5, 5.0, 12.0, v_sg, 12.6, 20.0, 25.0,
                       48.9, 49.0, 49.5, 50.0, 55.0])
    v_ff = np.full_like(speeds, 50.0)

    got_a = R.ef_average_vec(speeds, v_sg, ef_ff, ef_sg)
    want_a = np.array([R.ef_average(v, v_sg, ef_ff, ef_sg) for v in speeds])
    assert np.array_equal(got_a, want_a), f"average differs:\n{got_a}\n{want_a}"

    got_f = R.ef_fraction_vec(speeds, v_ff, v_sg, ef_ff, ef_sg)
    want_f = np.array([R.ef_fraction(v, 50.0, v_sg, ef_ff, ef_sg) for v in speeds])
    assert np.allclose(got_f, want_f, rtol=0, atol=1e-12), (
        f"fraction differs:\n{got_f}\n{want_f}\ndelta {got_f - want_f}")
    print(f"  [ok] vectorised == scalar on {len(speeds)} speeds including both "
          f"clamp boundaries and v->0")


def test_ratios_are_invariant():
    """
    Every road vehicle is a `pass. car` from a two-row table, so these ratios
    cannot leave the band the table spans, whatever the traffic does.
    """
    r = _run(R.COMPONENTS)
    problems = R.check_ratios(pd.DataFrame([r]), "StopAndGoFraction")
    problems += R.check_ratios(pd.DataFrame([r]), "AverageSpeed")
    assert not problems, "ratio invariant violated:\n  " + "\n  ".join(problems)
    for method in ("AverageSpeed", "StopAndGoFraction"):
        col = "fraction" if method == "StopAndGoFraction" else "average"
        print(f"  [ok] {method:18s} NOx/CO2 = "
              f"{r[f'nox_{col}_kg'] / r[f'co2_{col}_kg'] * 1000:.4f} g/kg, "
              f"NO2/NOx = {r[f'no2_{col}_kg'] / r[f'nox_{col}_kg']:.4f}")


def main() -> None:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print(f"\nall {len(tests)} checks passed")


if __name__ == "__main__":
    main()
