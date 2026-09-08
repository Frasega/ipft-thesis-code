"""
Why does a van tour get CHEAPER when there are more vans on the road?

THE OBSERVATION (parcel-count campaign, 27/8). The CO2 of one baseline van tour
falls as the fleet grows: 12.76 kg at N=235 (3 tours), 12.59 at N=470 (5), 12.07 at
N=940 (9) — deadlocked links already excluded. That is backwards on its face: more
vans should mean more congestion and MORE fuel per tour, not less.

THE TWO CANDIDATE EXPLANATIONS, and they lead to opposite conclusions.

  (a) GRID SAMPLING — an artefact of the design, not an effect. The departure grid
      always spans 07:00-09:00 and puts ceil(N/C_van) vans in it, evenly. Measured
      from the plans themselves:
          N=235 -> 07:00, 08:00, 09:00
          N=470 -> 07:00, 07:30, 08:00, 08:30, 09:00
          N=940 -> 07:00, 07:15, ... , 09:00
      Every set is symmetric about 08:00, but the SHARE of vans sitting at the two
      edges is 2/3, 2/5 and 2/9. If a tour's cost depends on the hour it leaves,
      the three means are three different samplings of one time-of-day curve, and
      the "effect of fleet size" is really the effect of where the grid puts vans.

  (b) A REAL CONGESTION EFFECT — the vans change the traffic they drive in, and
      more of them changes it more.

THE TEST THAT SEPARATES THEM. The 07:00, 08:00 and 09:00 departures exist in ALL
THREE fleets. Under (a) a van leaving at 08:00 costs the same whatever the fleet
size, and only the mean over the grid moves. Under (b) the same 08:00 van costs
more when eight others share the road with it. So: parse the three baselines, price
every van individually, and line up the vans that leave at the same minute.

This does NOT re-run MATSim. It reads the three baseline runs that the campaign
already produced, once each.

Usage, from the project root (~40 min, ~1 GB — one parse at a time):
    python python_pipeline/sensitivity/diag_per_tour_vs_n.py
"""

from __future__ import annotations

import gzip
import re
from pathlib import Path

import _common as C
from corridor_metrics import load_corridor_links
from parameters import VAN_ID_PREFIX, VAN_LOAD_FACTOR, WEIGHT_REGIMES, c_van
from parse_events import parse_events
from scenario_presets import get_preset
from term_b import co2_van_fleet

WEIGHT = "medium"
CONGESTION = "peak"
SEEDS = [4711, 9876]

# (N, cartella delle run, nome dei piani) — il baseline alpha=0 di ogni N.
CASES = [
    (235, C.OUTPUT_ROOT / "ipft_rotterdam_sens_rn235_runs",
     C.OUTPUT_ROOT / "ipft_rotterdam_sens_rn235_plans"
     / "warmplans_rn235_alpha000_peak_medium_3of3.xml.gz"),
    (470, C.BLOCKING_RUNS,
     C.OUTPUT_ROOT / "ipft_rotterdam_warm_plans"
     / "warmplans_alpha000_peak_medium.xml.gz"),
    (940, C.OUTPUT_ROOT / "ipft_rotterdam_sens_rn940_runs",
     C.OUTPUT_ROOT / "ipft_rotterdam_sens_rn940_plans"
     / "warmplans_rn940_alpha000_peak_medium_9of9.xml.gz"),
]

_PAT_VAN = re.compile(rb'<person id="(backup_van_\d+)"')
_PAT_DEP = re.compile(rb'end_time="(\d\d:\d\d:\d\d)"')


def departures(plans: Path) -> dict[str, str]:
    """van id -> depot departure time, read from the plans the run was given."""
    out, cur = {}, None
    with gzip.open(plans, "rb") as f:
        for line in f:
            m = _PAT_VAN.search(line)
            if m:
                cur = m.group(1).decode()
                continue
            if cur:
                d = _PAT_DEP.search(line)
                if d:
                    out[cur] = d.group(1).decode()
                    cur = None
    return out


def main() -> None:
    C.use_project_root()
    preset = get_preset("rotterdam")
    weight_kg = WEIGHT_REGIMES[WEIGHT]
    cvan = c_van(weight_kg)
    van_mass = 1950.0 + VAN_LOAD_FACTOR * cvan * weight_kg
    keep = frozenset(load_corridor_links(preset.corridor_links_file)
                     | load_corridor_links(preset.bus_stop_links_file))
    deadlock = load_corridor_links(str(C.DEADLOCK_LINKS[CONGESTION]))
    print(f"van evaluated at {van_mass:.0f} kg (tare + half of {cvan} x {weight_kg:.0f} kg)")
    print(f"{len(deadlock)} deadlocked links excluded, as in the campaign\n")

    rows = []
    for n, runs_dir, plans in CASES:
        if not plans.exists():
            print(f"[skip] N={n}: plans not found ({plans})")
            continue
        dep = departures(plans)
        for seed in SEEDS:
            cell = f"alpha000_{CONGESTION}_{WEIGHT}_seed{seed}"
            ev = C.find_events(runs_dir, cell)
            if not ev:
                print(f"[skip] N={n} seed {seed}: no events in {runs_dir}")
                continue
            print(f"[parse] N={n} seed {seed}", flush=True)
            vmean, _ = parse_events(ev, preset.network_file, verbose=False,
                                    bus_prefixes=preset.transit_prefixes,
                                    pax_bus_ids=preset.term_c_bus_ids,
                                    keep_link_ids=keep)
            vans = sorted(v for v in vmean["vehicle_id"].unique()
                          if str(v).startswith(VAN_ID_PREFIX))
            for vid in vans:
                co2 = co2_van_fleet(vmean, [vid], van_mass, exclude_links=deadlock)
                rows.append(dict(n=n, seed=seed, van=vid,
                                 departure=dep.get(vid, "?"), co2_kg=co2))
            del vmean

    if not rows:
        raise SystemExit("no cell could be read")

    import pandas as pd
    df = pd.DataFrame(rows)
    out = C.ROOT / "output" / "diag_per_tour_vs_n.csv"
    df.to_csv(out, index=False)

    print("\n=== every van, priced individually [kg CO2, deadlock links excluded] ===")
    piv = df.pivot_table(index="departure", columns=["n", "seed"], values="co2_kg")
    print(piv.round(3).to_string())

    print("\n=== mean over the grid, which is what Term B multiplies ===")
    for n, g in df.groupby("n"):
        print(f"  N={n:4d}  {g['van'].nunique():2d} vans  mean {g['co2_kg'].mean():7.4f} kg"
              f"   (campaign reported 12.76 / 12.59 / 12.07)")

    print("\n=== THE TEST: the same departure time, across fleet sizes ===")
    print("    flat across a row  -> grid sampling, an artefact of the design")
    print("    rising with N      -> a real congestion effect of the extra vans\n")
    shared = sorted(t for t, g in df.groupby("departure")
                    if g["n"].nunique() == df["n"].nunique())
    if not shared:
        print("    (no departure time is present in every fleet)")
    for t in shared:
        sub = df[df.departure == t].groupby("n")["co2_kg"].mean()
        vals = "   ".join(f"N={n}: {v:6.3f}" for n, v in sub.items())
        lo, hi = sub.min(), sub.max()
        print(f"  {t}   {vals}    spread {hi - lo:.3f} kg ({(hi - lo) / lo * 100:+.1f}%)")

    print("\n=== is the time of day what prices a tour? ===")
    by_t = df.groupby("departure")["co2_kg"].agg(["mean", "count"])
    for t, r in by_t.iterrows():
        print(f"  {t}  {r['mean']:7.3f} kg   ({int(r['count'])} vans across all fleets)")
    print(f"\nsaved -> {out}")


if __name__ == "__main__":
    main()
