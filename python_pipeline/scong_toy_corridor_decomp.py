"""
Recompute + decompose the corridor-restricted congestion-relief saving S_cong
of the toy warm campaign (medium regime, Ch5 Table 5.2 / tab:term-decomposition).

PROVENANCE (closes the HANDOFF "computed by hand" TODO): this script reproduces
the Ch5 table exactly. Recipe of the table numbers:
  corridor      = links traversed by the vans in the alpha=0 baseline of the
                  same (congestion, seed) pair (49 links)
  S_cong        = background warm+cold HBEFA CO2 on those links,
                  baseline - scenario [kg/day]
  table value   = mean of the two seeds; "+-" = half the gap between them
                  (population std over n=2 - NOT a confidence interval)

DECOMPOSITION (the finding, 2026-07-12): the delta splits as
  scen - base = dVKM * I_base   (volume channel: background km changed
                                  -> plans differ -> ChangeExpBeta reshuffle)
              + VKM_scen * dI   (speed/intensity channel: g/km changed)
Because the toy HBEFA table is two-point monotone (St+Go 281 g/km at 12.5 km/h,
Freeflow floor 156 g/km at 41.7 km/h), faster traffic can never emit more per
km: at fixed background behaviour S_cong >= 0 BY CONSTRUCTION. Measured result:
  peak    : speed channel saves 1.2-4.5 kg/day, grows with alpha, seeds agree
            (alpha=1: 4.1 vs 4.5) -> real road-space effect;
            volume channel swings +-2 kg/day either sign -> reshuffle noise.
  off-peak: corridor near free flow -> speed channel ~0; the -1 to -2 kg/day
            of the table is selection noise ALONE, its sign is not physical.
The old "U-shaped speed-emission relation" explanation was WRONG for this
pipeline (no U exists in a two-point monotone table) and was removed from Ch5.

Usage:  python python_pipeline/scong_toy_corridor_decomp.py
        (runs on D:/TesiOutputs/ipft_toy_warm_runs, ~10 min, writes
         output/scong_toy_corridor_decomp.csv)
"""
import sys
from pathlib import Path

PIPE = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPE))

import pandas as pd
from term_a import parse_hbefa_events, filter_background_vehicles, _HBEFA_CACHE
from parse_events import load_link_attributes
from parameters import VAN_ID_PREFIX

RUNS = Path(r"D:\TesiOutputs\ipft_toy_warm_runs")
OUT = PIPE.parent / "output" / "scong_toy_corridor_decomp.csv"

net = PIPE.parent / "scenarios" / "ipft_toy" / "reduced_network.xml"
attrs = load_link_attributes(str(net))
lengths = attrs[0] if isinstance(attrs, tuple) else attrs
print(f"network links: {len(lengths)}")


def run_aggregates(run_name):
    """Parse one run -> per-link background aggregates + van link set."""
    p = RUNS / run_name / "output_events.xml.zst"
    df = parse_hbefa_events(str(p))
    vids = df["vehicle_id"].fillna("")
    van_links = set(df.loc[vids.str.startswith(VAN_ID_PREFIX), "link_id"].unique())
    bg = filter_background_vehicles(df)
    warm = bg[bg.event_type == "warm"].groupby("link_id")["co2_kg"].agg(["sum", "count"])
    cold = bg[bg.event_type == "cold"].groupby("link_id")["co2_kg"].sum()
    _HBEFA_CACHE.clear()  # keep memory flat across the 20 parses
    return {"warm": warm, "cold": cold, "van_links": van_links}


def corridor_stats(agg, corridor):
    w = agg["warm"][agg["warm"].index.isin(corridor)]
    cold = agg["cold"][agg["cold"].index.isin(corridor)].sum()
    warm_kg = w["sum"].sum()
    vkm = sum(w.loc[l, "count"] * lengths.get(l, 0.0) for l in w.index) / 1000.0
    return warm_kg, float(cold), vkm


rows = []
for cong in ("peak", "offpeak"):
    for seed in (4711, 9876):
        base = run_aggregates(f"alpha000_{cong}_medium_seed{seed}")
        corridor = base["van_links"]
        print(f"{cong} seed{seed}: {len(corridor)} van links in baseline")
        bw, bc, bvkm = corridor_stats(base, corridor)
        for a in ("025", "050", "075", "100"):
            scen = run_aggregates(f"alpha{a}_{cong}_medium_seed{seed}")
            sw, sc, svkm = corridor_stats(scen, corridor)
            i_base = bw / bvkm if bvkm else 0.0
            i_scen = sw / svkm if svkm else 0.0
            d_vkm = svkm - bvkm
            rows.append({
                "alpha": int(a) / 100, "congestion": cong, "seed": seed,
                "S_warm": bw - sw, "S_warmcold": (bw + bc) - (sw + sc),
                "vkm_base": bvkm, "vkm_scen": svkm, "d_vkm": d_vkm,
                "gpkm_base": i_base * 1000, "gpkm_scen": i_scen * 1000,
                "vol_effect_kg": d_vkm * i_base,
                "int_effect_kg": svkm * (i_scen - i_base),
                "n_corridor_links": len(corridor),
            })
            r = rows[-1]
            print(f"  a={a} S_w+c={r['S_warmcold']:+.2f} dvkm={d_vkm:+.1f} "
                  f"vol={r['vol_effect_kg']:+.2f} int={r['int_effect_kg']:+.2f}")

df = pd.DataFrame(rows)
OUT.parent.mkdir(exist_ok=True)
df.to_csv(OUT, index=False)
print("\n=== mean +- half-gap across the 2 seeds (compare with Ch5 Table 5.2) ===")
for (a, c), g in df.groupby(["alpha", "congestion"]):
    v = g["S_warmcold"]
    print(f"alpha={a:.2f} {c:<8s} {v.mean():+.1f} +- {abs(v.iloc[0]-v.iloc[1])/2:.1f}"
          f"  (seeds: {[round(x, 2) for x in v.tolist()]})")
print(f"\nsaved -> {OUT}")
