"""
The van type must not disturb any number that was computed without one.

WHY THIS FILE EXISTS. When the van size became a variable (2026-08-26), term_b
stopped reading VAN_CD / VAN_FRONTAL_AREA_M2 / VAN_ROLLING_RESISTANCE /
VAN_DRIVETRAIN_EFF as module constants and started taking a whole VanType. The
first version of that change had a real defect: compute_term_b resolved the vehicle
and then handed the RESOLVED object to its two components, which made them ignore
the loose van_tare_kg / payload_capacity_kg / parcels_per_tour_max arguments. Every
cell of the van-capacity screening (screening_analysis.py passes
van_payload_capacity_kg with no van type) was then priced as the base van, with the
right tour count and the wrong mass — silently, and plausibly, because the number
still moved with the tour count.

The golden values below were computed with the term_b of commit 26e775e, i.e.
BEFORE the van type existed at all. They are not a description of what the code
does; they are what it did, and they must keep coming out.

Run:  python python_pipeline/test_van_type_plumbing.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from parameters import VanType, van_type, VAN_ROLLING_RESISTANCE, VAN_DRIVETRAIN_EFF
from term_b import _van_co2_per_second, compute_term_b


def _frame(n_vans: int, seed: int) -> pd.DataFrame:
    """A stand-in for a parsed events file: n_vans tours over 96 links, speeds
    spread across the range the corridor really shows, a few of them near
    standstill so the idle term is exercised too."""
    rng = np.random.default_rng(seed)
    rows = []
    for v in range(n_vans):
        for link in range(96):
            rows.append(dict(vehicle_id=f"backup_van_{v:04d}", link_id=str(1000 + link),
                             v_mean_ms=float(rng.uniform(0.05, 14.0)),
                             travel_time_s=float(rng.uniform(3.0, 60.0)),
                             time_entered_s=link * 60.0, vehicle_type="van"))
    return pd.DataFrame(rows)


BASE = _frame(5, 1)
SCEN = _frame(3, 2)
EXCL = frozenset({"1000", "1001"})

# (label, extra kwargs, expected term_b_kg) — measured on term_b at commit 26e775e.
GOLDEN = [
    ("light, alpha=0.5",   dict(van_payload_kg=3.0,  alpha=0.5),   7.036023),
    ("medium, alpha=0.5",  dict(van_payload_kg=10.0, alpha=0.5),   7.876748),
    ("heavy, alpha=0.5",   dict(van_payload_kg=25.0, alpha=0.5),  19.540667),
    ("medium, alpha=1",    dict(van_payload_kg=10.0, alpha=1.0),  18.935858),
    ("medium, alpha=0",    dict(van_payload_kg=10.0, alpha=0.0),   0.504008),
    # The four that the first version of the van-type change broke.
    ("payload 750",   dict(van_payload_kg=10.0, alpha=0.5, payload_capacity_kg=750),   11.160120),
    ("payload 1400",  dict(van_payload_kg=10.0, alpha=0.5, payload_capacity_kg=1400),   8.118320),
    ("tare 1600",     dict(van_payload_kg=10.0, alpha=0.5, van_tare_kg=1600.0),         7.067088),
    ("parcel cap 100", dict(van_payload_kg=10.0, alpha=0.5, parcels_per_tour_max=100),  7.760826),
    # The other levers that were already there and must stay put.
    ("full load",     dict(van_payload_kg=10.0, alpha=0.5, load_factor=1.0),            9.153915),
    ("idle high",     dict(van_payload_kg=10.0, alpha=0.5, van_stop_idle_s=100.0),      8.618748),
    ("recon seed 7",  dict(van_payload_kg=10.0, alpha=0.5, rng_seed=7),                 7.965234),
]

TOL = 1e-6
failures = 0


def _check(label: str, got: float, want: float) -> None:
    global failures
    ok = abs(got - want) <= TOL * max(abs(want), 1.0)
    print(f"  [{'ok' if ok else 'FAIL'}] {label:<18} term_b = {got:12.6f}"
          + ("" if ok else f"   expected {want:.6f}"))
    if not ok:
        failures += 1


print("golden values, from term_b before the van type existed:")
for label, kw, want in GOLDEN:
    res = compute_term_b(baseline_vmean_df=BASE, scenario_vmean_df=SCEN,
                         n_total_vans=470, n_pickup_stops=8, exclude_links=EXCL, **kw)
    _check(label, res["term_b_kg"], want)

print("\nthe van type is inert when it names the headline vehicle:")
explicit = compute_term_b(baseline_vmean_df=BASE, scenario_vmean_df=SCEN, alpha=0.5,
                          n_total_vans=470, van_payload_kg=10.0, n_pickup_stops=8,
                          exclude_links=EXCL, van=van_type("base"))
_check("van=base", explicit["term_b_kg"], 7.876748)

print("\nand live when it names another one:")
small = compute_term_b(baseline_vmean_df=BASE, scenario_vmean_df=SCEN, alpha=0.5,
                       n_total_vans=470, van_payload_kg=10.0, n_pickup_stops=8,
                       exclude_links=EXCL,
                       van=van_type("small", allow_unsourced=True))
moved = abs(small["term_b_kg"] - 7.876748) > 1e-3
print(f"  [{'ok' if moved else 'FAIL'}] van=small          term_b = "
      f"{small['term_b_kg']:12.6f}   (tours {explicit['tours_baseline']} -> "
      f"{small['tours_baseline']})")
if not moved:
    failures += 1

print("\nthe per-second cache keys on the vehicle, not only on the mass:")
# Same evaluation mass, different bodywork. With the pre-2026-08-26 cache key
# (speed, mass, seed) the second van would have been handed the first one's number.
a = VanType("A", 1950, 1100, 150, 3.8, 0.37, VAN_ROLLING_RESISTANCE, VAN_DRIVETRAIN_EFF, "test")
b = VanType("B", 1950, 1100, 150, 5.0, 0.36, VAN_ROLLING_RESISTANCE, VAN_DRIVETRAIN_EFF, "test")
ra = _van_co2_per_second(8.0, 2500.0, 42, a)
rb = _van_co2_per_second(8.0, 2500.0, 42, b)
ra_again = _van_co2_per_second(8.0, 2500.0, 42, a)
distinct = ra != rb and ra == ra_again
print(f"  [{'ok' if distinct else 'FAIL'}] area 3.8 -> 5.0    {ra:.10f} vs {rb:.10f}"
      f"   (A stable after B: {ra == ra_again})")
if not distinct:
    failures += 1

print("\nevery van type in the table carries a citation:")
from parameters import VAN_TYPES  # noqa: E402
for name, spec in sorted(VAN_TYPES.items()):
    ok = bool(spec.source)
    print(f"  [{'ok' if ok else 'FAIL'}] {name:<6} {spec.source[:58] if spec.source else 'NO SOURCE'}")
    if not ok:
        failures += 1

print("\nand one without a citation would still be refused:")
# The guard has to keep working after the table is filled in, so it is tested on a
# type injected for the purpose rather than on whichever entry happens to be
# unsourced today — which, now that all three are sourced, would be none of them.
VAN_TYPES["_probe"] = VanType("_probe", 1000, 500, 50, 3.0, 0.35,
                              VAN_ROLLING_RESISTANCE, VAN_DRIVETRAIN_EFF, None)
try:
    van_type("_probe")
except Exception as exc:
    refused = type(exc).__name__ == "VanTypeNotSourced"
    print(f"  [{'ok' if refused else 'FAIL'}] raised {type(exc).__name__}")
    if not refused:
        failures += 1
else:
    print("  [FAIL] van_type('_probe') returned a specification with source=None")
    failures += 1
finally:
    del VAN_TYPES["_probe"]

print()
print("all checks passed" if failures == 0 else f"{failures} FAILURES")
sys.exit(1 if failures else 0)
