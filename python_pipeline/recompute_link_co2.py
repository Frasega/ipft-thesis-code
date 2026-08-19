"""
Recompute background CO2 per link from the events, for ANY link set.

Why this exists. Co2TotalsHandler sums into buckets fixed at run time, and the
full emission log is switched off (8 GB/run), so the runs on disk can only ever
answer for the link sets chosen before they started. Patrick asked for corridor,
one hop, two hops, three hops and the full network. Recomputing here makes the
link set a post-processing knob and the question never costs a run again.

TWO METHODS, and the difference is the point:

  average   what the runs actually used. MATSim's default
            (EmissionsConfigGroup: emissionsComputationMethod = AverageSpeed,
            never overridden in our config). getTrafficSituation() picks ONE
            situation: with our two-row table that is FREEFLOW above the stop&go
            table speed and STOPANDGO at or below it. So the factor is a STEP:
            155.97 g/km or 281.23 g/km, nothing between. A car at 13 km/h and a
            car at 12.4 km/h differ by 80% in emissions and by nothing in
            physics. This is why the congestion effect looks smooth in
            vehicle-hours and lumpy in CO2 — it is the emission model
            discretising, not the traffic.

  fraction  MATSim's other method (StopAndGoFraction), blended continuously:
                f_sg = v_sg * (v_ff - v_avg) / (v_avg * (v_ff - v_sg))
                ef   = (1 - f_sg) * ef_ff + f_sg * ef_sg
            with v_ff the LINK's free speed and v_sg the table's stop&go speed,
            clamped to 0 when v_avg is within 1 km/h of free flow and to 1 when
            v_avg <= v_sg (WarmEmissionAnalysisModule:355-361). This responds to
            every km/h, so a congestion delta stops being hostage to a threshold.

Both are computed side by side. Reporting them together is honest: the first is
what the surface used, the second is what the effect looks like without the
step. VALIDATION: the 'average' total on the corridor must reproduce the
background_corridor figure the Java handler already wrote — the script prints
both and their gap.

Usage (from project root):
    python python_pipeline/recompute_link_co2.py --run <run_dir> [--sets a.txt b.txt ...]
"""

from __future__ import annotations

import argparse
import csv
import glob
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd

from parse_events import load_link_attributes, parse_events
from scenario_presets import get_preset

CO2_COMPONENT = "CO2(total)"
VEH_CAT = "pass. car"


def load_hbefa(scenario_dir: Path) -> dict:
    """The two rows that matter: free-flow and stop&go for a passenger car."""
    path = scenario_dir / "sample_41_EFA_HOT_vehcat_2020average.csv"
    out = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter=";"):
            if r["VehCat"] != VEH_CAT or r["Component"] != CO2_COMPONENT:
                continue
            sit = "ff" if "Freeflow" in r["TrafficSit"] else "sg"
            out[sit] = (float(r["V_weighted"]), float(r["EFA_weighted"]))
    if {"ff", "sg"} - out.keys():
        raise RuntimeError(f"free-flow / stop&go rows not both found in {path}")
    return out


def ef_average(v_avg_kmh: float, v_sg_kmh: float, ef_ff: float, ef_sg: float) -> float:
    """MATSim AverageSpeed: one situation, a step at the stop&go table speed."""
    return ef_sg if v_avg_kmh <= v_sg_kmh else ef_ff


def ef_fraction(v_avg_kmh: float, v_ff_link_kmh: float, v_sg_kmh: float,
                ef_ff: float, ef_sg: float) -> float:
    """MATSim StopAndGoFraction: continuous blend (WarmEmissionAnalysisModule:355-361)."""
    if (v_avg_kmh - v_ff_link_kmh) >= -1.0:
        f_sg = 0.0
    elif (v_avg_kmh - v_sg_kmh) <= 0.0:
        f_sg = 1.0
    else:
        f_sg = (v_sg_kmh * (v_ff_link_kmh - v_avg_kmh)
                / (v_avg_kmh * (v_ff_link_kmh - v_sg_kmh)))
        f_sg = min(max(f_sg, 0.0), 1.0)
    return (1.0 - f_sg) * ef_ff + f_sg * ef_sg


def co2_by_set(vmean_df: pd.DataFrame, link_lengths: dict, link_freespeeds: dict,
               hbefa: dict, sets: dict[str, frozenset]) -> pd.DataFrame:
    """kg CO2 of BACKGROUND traffic on each link set, both methods."""
    v_sg, ef_sg = hbefa["sg"]
    _, ef_ff = hbefa["ff"]

    bg = vmean_df[vmean_df["vehicle_type"] == "background"]
    rows = []
    for name, links in sets.items():
        d = bg[bg["link_id"].isin(links)]
        if d.empty:
            rows.append(dict(link_set=name, n_links=len(links), n_traversals=0,
                             vkm=0.0, co2_average_kg=0.0, co2_fraction_kg=0.0,
                             share_below_threshold=None))
            continue
        length_km = d["link_id"].map(link_lengths).astype(float) / 1000.0
        v_kmh = d["v_mean_ms"].astype(float) * 3.6
        v_ff = (d["link_id"].map(link_freespeeds).astype(float) * 3.6)
        ef_a = [ef_average(v, v_sg, ef_ff, ef_sg) for v in v_kmh]
        ef_f = [ef_fraction(v, f, v_sg, ef_ff, ef_sg) for v, f in zip(v_kmh, v_ff)]
        rows.append(dict(
            link_set=name, n_links=len(links), n_traversals=int(len(d)),
            vkm=float(length_km.sum()),
            co2_average_kg=float((pd.Series(ef_a).values * length_km.values).sum() / 1000.0),
            co2_fraction_kg=float((pd.Series(ef_f).values * length_km.values).sum() / 1000.0),
            share_below_threshold=float((v_kmh <= v_sg).mean())))
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--run", required=True, help="run directory")
    ap.add_argument("--sets", nargs="*", default=None,
                    help="link-set files (default: corridor + all khop_cum*)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    preset = get_preset("rotterdam")
    root = Path(preset.base_config).parent.parent.parent
    scen = root / "scenarios" / "ipft_rotterdam"
    hbefa = load_hbefa(scen)
    print(f"HBEFA {VEH_CAT} {CO2_COMPONENT}: "
          f"free-flow {hbefa['ff'][1]:.2f} g/km @ {hbefa['ff'][0]:.2f} km/h | "
          f"stop&go {hbefa['sg'][1]:.2f} g/km @ {hbefa['sg'][0]:.2f} km/h")

    paths = ([Path(p) for p in args.sets] if args.sets
             else [scen / "corridor_links.txt"] + sorted(scen.glob("khop_cum*.txt")))
    sets = {p.stem: frozenset(x for x in p.read_text().split() if x) for p in paths}
    keep = frozenset().union(*sets.values())
    print(f"set: {', '.join(f'{k}({len(v)})' for k, v in sets.items())}")

    ev = sorted(glob.glob(f"{args.run}/*output_events.xml.zst"))
    if not ev:
        raise SystemExit(f"no events in {args.run}")
    net = str(root / preset.network_file)
    lengths, freespeeds = load_link_attributes(net)
    df, _ = parse_events(ev[0], net, verbose=False, bus_prefixes=preset.transit_prefixes,
                         pax_bus_ids=preset.term_c_bus_ids, keep_link_ids=keep)

    res = co2_by_set(df, lengths, freespeeds, hbefa, sets)
    print()
    print(res.to_string(index=False))

    # Validation: 'average' on the corridor must reproduce the Java bucket.
    # Compare against the WARM column, not the total. The Java bucket also holds
    # cold start, which this script does not recompute and does not need to:
    # cold start is the extra burned in the first minutes after ignition,
    # attributed to the link the vehicle STARTS on, and it does not depend on
    # speed at all. It therefore cannot respond to a bus blocking the lane, and
    # with the plans frozen the same agents start in the same places in both
    # runs, so it cancels in the baseline - scenario difference. Warm-only is
    # the right quantity for a congestion delta, not a shortfall.
    tot = sorted(glob.glob(f"{args.run}/*co2_totals.csv"))
    if tot and "corridor_links" in res.link_set.values:
        j = pd.read_csv(tot[0])
        row = j[j.vehicle_class == "background_corridor"]
        if not row.empty:
            warm = float(row.warm_co2_kg.iloc[0])
            cold = float(row.cold_co2_kg.iloc[0])
            mine = float(res[res.link_set == "corridor_links"].co2_average_kg.iloc[0])
            print(f"\n[validate] corridoio, WARM: Java {warm:.2f} kg | ricalcolo {mine:.2f} kg | "
                  f"scarto {100*(mine-warm)/warm:+.2f}%")
            print(f"           (cold start {cold:.2f} kg = {100*cold/(warm+cold):.1f}% del totale, "
                  f"non ricalcolato: non dipende dalla velocita e si cancella nel delta)")

    out = args.out or f"output/link_co2_{Path(args.run).name}.csv"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    res.to_csv(out, index=False)
    print(f"scritto {out}")


if __name__ == "__main__":
    main()
