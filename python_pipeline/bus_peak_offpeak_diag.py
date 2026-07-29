"""
One-off diagnostic: how differently does the line-44 bus (and the corridor
background) actually move between the peak and off-peak SCENARIOS?

Context: the off-peak scenario is the peak population thinned to 50%
(insert_vans.create_offpeak_base), yet the unladen bus traction bill differs
by only +1.1% (bus_absolute_co2_check.py). This script establishes whether
that is because (a) the corridor cars themselves barely change, or (b) the
bus is decoupled from car traffic (stop/schedule-dominated links, bus lanes).

Reports, for alpha000_medium seed 4711, peak vs off-peak:
  A. commercial speed of each of the 98 one-to-many trips, by departure hour;
  B. per-link v_mean diff for the representative (median-link-count) bus;
  C. corridor background (non-transit) speed: overall and 07-09 / 10-15 / 16-18.

Run from project root: python python_pipeline/bus_peak_offpeak_diag.py
"""

from pathlib import Path

import numpy as np
import pandas as pd

from parse_events import load_link_attributes, parse_events
from scenario_presets import _PROJECT_ROOT, get_preset

SEED = "4711"


def load_scenario(regime: str, preset):
    run_dir = Path(preset.output_base_dir) / f"alpha000_{regime}_medium_seed{SEED}"
    events = run_dir / "MRDH_10pct.output_events.xml.zst"
    network = _PROJECT_ROOT / preset.network_file
    corridor = frozenset(
        line.strip()
        for line in (_PROJECT_ROOT / preset.corridor_links_file).read_text().splitlines()
        if line.strip()
    )
    vmean_df, _pax = parse_events(
        str(events), str(network), verbose=False,
        bus_prefixes=preset.transit_prefixes,
        pax_bus_ids=preset.term_c_bus_ids,
        keep_link_ids=corridor,
    )
    return vmean_df, corridor


def trip_stats(vmean_df: pd.DataFrame, bus_ids, link_lengths) -> pd.DataFrame:
    rows = []
    for vid in bus_ids:
        sub = vmean_df[vmean_df["vehicle_id"] == vid]
        if sub.empty:
            continue
        dist = sum(link_lengths.get(l, 0.0) for l in sub["link_id"])
        tt = float(sub["travel_time_s"].sum())
        rows.append({
            "vehicle_id": vid,
            "dep_h": float(sub["time_entered_s"].min()) / 3600.0,
            "kmh": (dist / tt) * 3.6 if tt > 0 else np.nan,
        })
    return pd.DataFrame(rows).sort_values("dep_h").reset_index(drop=True)


def main() -> None:
    preset = get_preset("rotterdam")
    link_lengths, _ = load_link_attributes(str(_PROJECT_ROOT / preset.network_file))
    bus_ids = frozenset(preset.term_c_bus_ids)

    data = {}
    for regime in ("peak", "offpeak"):
        vmean_df, corridor = load_scenario(regime, preset)
        data[regime] = vmean_df
        print(f"[{regime}] parsed: {len(vmean_df):,} rows", flush=True)

    # ── A. commercial speed of all 98 trips ────────────────────────────────
    trips = {r: trip_stats(df, bus_ids, link_lengths) for r, df in data.items()}
    merged = trips["peak"].merge(
        trips["offpeak"], on="vehicle_id", suffixes=("_pk", "_op")
    )
    merged["d_kmh"] = merged["kmh_pk"] - merged["kmh_op"]
    print("\n=== A. line-44 commercial speed per trip (km/h), peak vs offpeak ===")
    print(f"{'dep':>6} {'peak':>6} {'off':>6} {'diff':>6}")
    for r in merged.itertuples():
        print(f"{r.dep_h_pk:6.2f} {r.kmh_pk:6.1f} {r.kmh_op:6.1f} {r.d_kmh:+6.1f}")
    for lo, hi, label in ((7, 9, "07-09"), (10, 15, "10-15"), (16, 18, "16-18")):
        m = merged[(merged.dep_h_pk >= lo) & (merged.dep_h_pk < hi)]
        if not m.empty:
            print(f"  {label}: peak {m.kmh_pk.mean():.2f}  off {m.kmh_op.mean():.2f}  "
                  f"diff {m.d_kmh.mean():+.2f} km/h  (n={len(m)})")
    print(f"  ALL  : peak {merged.kmh_pk.mean():.2f}  off {merged.kmh_op.mean():.2f}  "
          f"diff {merged.d_kmh.mean():+.2f} km/h")

    # ── B. per-link v_mean diff, representative bus ────────────────────────
    counts = data["peak"][data["peak"]["vehicle_id"].isin(bus_ids)].groupby("vehicle_id").size()
    rep = (counts - counts.median()).abs().sort_values(kind="stable").index[0]
    print(f"\n=== B. per-link v_mean, representative bus {rep} ===")
    pk = data["peak"][data["peak"]["vehicle_id"] == rep][["link_id", "v_mean_ms"]]
    op = data["offpeak"][data["offpeak"]["vehicle_id"] == rep][["link_id", "v_mean_ms"]]
    j = pk.merge(op, on="link_id", suffixes=("_pk", "_op"))
    j["d_kmh"] = (j.v_mean_ms_pk - j.v_mean_ms_op) * 3.6
    print(f"  links joined: {len(j)}")
    print(f"  mean |dv|: {j.d_kmh.abs().mean():.2f} km/h   median |dv|: {j.d_kmh.abs().median():.2f}")
    print(f"  links slower in peak: {(j.d_kmh < -0.5).sum()}  faster in peak: {(j.d_kmh > 0.5).sum()}  "
          f"within +-0.5: {(j.d_kmh.abs() <= 0.5).sum()}")
    top = j.reindex(j.d_kmh.abs().sort_values(ascending=False).index).head(8)
    for r in top.itertuples():
        print(f"    link {r.link_id}: peak {r.v_mean_ms_pk*3.6:5.1f}  off {r.v_mean_ms_op*3.6:5.1f}  "
              f"diff {r.d_kmh:+5.1f} km/h")

    # ── C. corridor background speed ───────────────────────────────────────
    print("\n=== C. corridor background (non-transit) speed, km/h ===")
    for regime, df in data.items():
        bg = df[~df["vehicle_id"].astype(str).str.startswith("veh_")].copy()
        bg["len_m"] = bg["link_id"].map(link_lengths).fillna(0.0)
        bg["hour"] = bg["time_entered_s"] // 3600
        def spd(sub):
            t = sub["travel_time_s"].sum()
            return (sub["len_m"].sum() / t) * 3.6 if t > 0 else np.nan
        print(f"  {regime:8s}: overall {spd(bg):5.1f}   "
              f"07-09 {spd(bg[(bg.hour >= 7) & (bg.hour < 9)]):5.1f}   "
              f"10-15 {spd(bg[(bg.hour >= 10) & (bg.hour < 15)]):5.1f}   "
              f"16-18 {spd(bg[(bg.hour >= 16) & (bg.hour < 18)]):5.1f}   "
              f"(traversals {len(bg):,})")


if __name__ == "__main__":
    main()
