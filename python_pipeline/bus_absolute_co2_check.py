"""
One-off check: ABSOLUTE traction CO2 of the unladen line-44 bus, peak vs off-peak.

Term C only ever reports the laden-minus-unladen delta, in which the whole
baseline consumption of the bus cancels. This script computes the part that
cancels: CO2_running(pax-only mass) per representative H->B trip, evaluated
with exactly the same machinery as term_c._delta_co2_on_link (same SORT
recombination, same seed, same time scaling, same representative-bus rule),
on the alpha=0 baselines of both congestion regimes.

Output feeds the Ch6 paragraph explaining why E_PT is nearly identical
between peak and off-peak while total bus emissions are not.

Traction only: scheduled-stop idle fuel and auxiliaries are not included
(they cancel in the Term C delta and were never modelled) — say so in thesis.

Run from project root: python python_pipeline/bus_absolute_co2_check.py
"""

from pathlib import Path

from dynamic_mass import compute_mbus_passengers_only
from emission_formula import compute_co2_running, speed_to_accel
from parameters import (
    BUS_CD,
    BUS_DRIVETRAIN_EFF,
    BUS_FRONTAL_AREA_M2,
    BUS_ROLLING_RESISTANCE,
    BUS_TARE_KG,
)
from parse_events import load_link_attributes, parse_events
from scenario_presets import _PROJECT_ROOT, get_preset
from sort_cycles import reconstruct_bus_profile

RNG_SEED = 42  # canonical reconstruction seed (matches the headline surface)

# The canonical toy surface lives in ipft_toy_warm_runs (not the preset's
# output_base_dir, which predates the warm-start reorganisation).
TOY_RUNS_ROOT = Path("D:/TesiOutputs/ipft_toy_warm_runs")
EVENTS_FILENAME = {"rotterdam": "MRDH_10pct.output_events.xml.zst",
                   "toy": "output_events.xml.zst"}


def unladen_co2_per_trip(run_dir: Path, preset) -> dict:
    events = run_dir / EVENTS_FILENAME[preset.name]
    network = _PROJECT_ROOT / preset.network_file

    # Rotterdam: keep_link_ids=frozenset() keeps only vans (none at alpha=0)
    # and the 98 line-44 H->B buses -> lean DataFrame, full pax timeline.
    # Toy: no allowlist -> keep everything (the toy events are small anyway;
    # keep_link_ids with pax_ids=None would drop the buses).
    vmean_df, pax_timeline = parse_events(
        str(events), str(network), verbose=False,
        bus_prefixes=preset.transit_prefixes,
        pax_bus_ids=preset.term_c_bus_ids,
        keep_link_ids=frozenset() if preset.term_c_bus_ids is not None else None,
    )

    # Representative bus: link count nearest the median (term_c rule verbatim).
    if preset.term_c_bus_ids is not None:
        allow = frozenset(preset.term_c_bus_ids)
        candidates = [v for v in vmean_df["vehicle_id"].unique() if v in allow]
    else:
        from parameters import BUS_ID_PREFIXES
        candidates = [
            v for v in vmean_df["vehicle_id"].unique()
            if v.startswith(BUS_ID_PREFIXES) and v.startswith(preset.hb_route_prefixes)
        ]
    counts = (
        vmean_df[vmean_df["vehicle_id"].isin(candidates)].groupby("vehicle_id").size()
    )
    median = counts.median()
    rep = (counts - median).abs().sort_values(kind="stable").index[0]

    rows = vmean_df[vmean_df["vehicle_id"] == rep].sort_values("time_entered_s")
    link_lengths, _ = load_link_attributes(str(network))

    co2_trip = 0.0
    dist_m = 0.0
    time_s = 0.0
    pax_weighted = 0.0
    for row in rows.itertuples():
        v_t = reconstruct_bus_profile(row.v_mean_ms, rng_seed=RNG_SEED)
        a_t = speed_to_accel(v_t)
        tt = float(row.travel_time_s)
        t_leave = row.time_entered_s + tt
        m_pax = compute_mbus_passengers_only(
            rep, row.time_entered_s, t_leave, pax_timeline, BUS_TARE_KG,
            preset.sample_rate,
        )
        co2 = compute_co2_running(
            v_t, a_t, m_pax, BUS_CD, BUS_FRONTAL_AREA_M2,
            BUS_ROLLING_RESISTANCE, BUS_DRIVETRAIN_EFF,
        )
        t_sort = float(len(v_t))
        scale = (tt / t_sort) if (t_sort > 0 and tt > 0) else 1.0
        co2_trip += co2 * scale
        dist_m += link_lengths.get(row.link_id, 0.0)
        time_s += tt
        # occupants, not passengers: real-scale riders plus the driver
        pax_weighted += ((m_pax - BUS_TARE_KG) / 75.0) * tt

    return {
        "bus": rep,
        "n_links": len(rows),
        "co2_kg_per_trip": co2_trip,
        "co2_kg_per_day": co2_trip * preset.bus_trips_per_day,
        "route_km": dist_m / 1000.0,
        "mean_speed_kmh": (dist_m / time_s) * 3.6 if time_s > 0 else float("nan"),
        "mean_pax": pax_weighted / time_s if time_s > 0 else float("nan"),
    }


def main() -> None:
    import sys
    scenario = sys.argv[1] if len(sys.argv) > 1 else "rotterdam"
    preset = get_preset(scenario)
    runs_root = TOY_RUNS_ROOT if scenario == "toy" else Path(preset.output_base_dir)
    results: dict[str, list[dict]] = {"peak": [], "offpeak": []}

    for regime in ("peak", "offpeak"):
        for seed in ("4711", "9876"):
            run_dir = runs_root / f"alpha000_{regime}_medium_seed{seed}"
            r = unladen_co2_per_trip(run_dir, preset)
            results[regime].append(r)
            print(
                f"{regime:8s} seed {seed}: bus {r['bus']}  links {r['n_links']:3d}  "
                f"route {r['route_km']:.2f} km  v_mean {r['mean_speed_kmh']:.1f} km/h  "
                f"pax {r['mean_pax']:.1f}  CO2 {r['co2_kg_per_trip']:.3f} kg/trip  "
                f"{r['co2_kg_per_day']:.1f} kg/day",
                flush=True,
            )

    print(f"\n--- seed means (unladen traction CO2, {scenario}, "
          f"one-to-many route x {preset.bus_trips_per_day} trips) ---")
    means = {}
    for regime, rs in results.items():
        day = sum(r["co2_kg_per_day"] for r in rs) / len(rs)
        spd = sum(r["mean_speed_kmh"] for r in rs) / len(rs)
        means[regime] = day
        print(f"{regime:8s}: {day:.1f} kg/day   mean commercial speed {spd:.1f} km/h")
    gap = (means["peak"] - means["offpeak"]) / means["offpeak"] * 100
    print(f"peak vs offpeak: {gap:+.1f}%")


if __name__ == "__main__":
    main()
