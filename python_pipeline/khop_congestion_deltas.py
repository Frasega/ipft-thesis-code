"""
Congestion deltas on every ring, in CO2 and vehicle-hours, next to their noise.

This is what the reply to Patrick promised: corridor, one hop, two hops, three
hops and the full network, each beside the seed-to-seed noise so it is visible
where the effect stops being resolvable (his point 12, congestion propagation,
Ji/Luo/Geroliminis 2014).

Two terms, never merged — they point in opposite directions:
  van relief   vans no longer on the road          expected +
  bus cost     traffic held behind the stopped bus expected -
Both are reported as baseline - scenario, so the bus cost comes out negative on
its own and is never sign-flipped by hand.

CO2 is recomputed from the events (recompute_link_co2), because the Java buckets
were fixed at run time and cannot answer for the rings. Both lookup rules are
carried: 'average' is what the runs used (a step at the stop&go table speed) and
'fraction' is MATSim's continuous blend. The full network is NOT recomputed —
parsing every car on every link would not fit in memory — it is read from the
Java 'background' bucket, which is exactly that quantity.

NOISE: for each (weight, congestion) the two alpha=0 baselines are physically
identical and differ only by the seed, so their difference on a set is that
set's floor. Anything below it is not a measurement.

Usage:
    python python_pipeline/khop_congestion_deltas.py            # all 48 cells
    python python_pipeline/khop_congestion_deltas.py --weights medium
"""

from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd

from corridor_metrics import corridor_background_stats, corridor_delta
from parse_events import load_link_attributes, parse_events
from recompute_link_co2 import co2_by_set, load_hbefa
from scenario_presets import get_preset

ALPHAS = [0.25, 0.50, 0.75, 1.00]


def events_of(runs: str, cell: str):
    hits = sorted(glob.glob(f"{runs}/{cell}/*output_events.xml.zst"))
    return hits[0] if hits else None


def java_background(runs: str, cell: str):
    """Whole-network background CO2 (warm+cold) as the Java handler summed it."""
    hits = sorted(glob.glob(f"{runs}/{cell}/*co2_totals.csv"))
    if not hits:
        return None
    d = pd.read_csv(hits[0])
    r = d[d.vehicle_class == "background"]
    return float(r.total_co2_kg.iloc[0]) if len(r) else None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--runs-dir", default="D:/TesiOutputs/ipft_rotterdam_dwell_blocking_runs")
    ap.add_argument("--weights", nargs="*", default=["light", "medium", "heavy"])
    ap.add_argument("--congestion", nargs="*", default=["peak", "offpeak"])
    ap.add_argument("--seeds", nargs="*", type=int, default=[4711, 9876])
    ap.add_argument("--alphas", nargs="*", type=float, default=None,
                    help="subset of ALPHAS to evaluate (default: all). Each alpha costs 2 event "
                         "files per weight/congestion/seed block; the full-load table needs 1.00 only")
    ap.add_argument("--out", default="output/khop_congestion_deltas.csv")
    ap.add_argument("--exclude-deadlocks", action="store_true",
                    help="also report vehicle-hours with the deadlock links removed "
                         "(deadlock_links.txt, written by make_deadlock_links.py from the "
                         "LONGBASE). Adds the column d_vehicle_hours_excl_deadlock; the "
                         "unfiltered d_vehicle_hours column is kept so both can be shown "
                         "side by side. The kg columns are never filtered — a stuck link "
                         "is charged the same step-function factor in both worlds and "
                         "cancels in the difference.")
    args = ap.parse_args()

    alphas = args.alphas if args.alphas else ALPHAS
    preset = get_preset("rotterdam")
    root = Path(preset.base_config).parent.parent.parent
    scen = root / "scenarios" / "ipft_rotterdam"
    hbefa = load_hbefa(scen)
    net = str(root / preset.network_file)
    lengths, freespeeds = load_link_attributes(net)

    def rd(p):
        return frozenset(x for x in (scen / p).read_text().split() if x)

    corridor, busstop = rd("corridor_links.txt"), rd("bus_stop_links.txt")
    rings = {"corridor": corridor}
    for k in (1, 2, 3):
        f = scen / f"khop_cum{k}.txt"
        if f.exists():
            rings[f"hop{k}"] = rd(f.name)
    # van row = ring minus the bus-stop links, so the two terms stay disjoint
    van_sets = {f"van_{n}": frozenset(s - busstop) for n, s in rings.items()}
    bus_sets = {"bus_stops": busstop}
    sets = {**van_sets, **bus_sets}
    keep = frozenset().union(*sets.values())
    print(f"set: {', '.join(f'{k}({len(v)})' for k, v in sets.items())}")

    deadlocks = frozenset()
    if args.exclude_deadlocks:
        f = scen / "deadlock_links.txt"
        if not f.exists():
            raise FileNotFoundError(
                f"{f} not found — run 'python python_pipeline/make_deadlock_links.py' first")
        # line by line, NOT .split(): the file carries a comment header, and
        # splitting on whitespace would turn each header word into a fake link id
        deadlocks = frozenset(l.strip() for l in f.read_text(encoding="utf-8").splitlines()
                              if l.strip() and not l.lstrip().startswith("#"))
        print(f"deadlock esclusi: {len(deadlocks)} link | per set: "
              + ", ".join(f"{k}(-{len(v & deadlocks)})" for k, v in sets.items()))

    def parse(p):
        df, _ = parse_events(p, net, verbose=False, bus_prefixes=preset.transit_prefixes,
                             pax_bus_ids=preset.term_c_bus_ids, keep_link_ids=keep)
        return df

    rows = []
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    for w in args.weights:
        for c in args.congestion:
            base = {}
            for s in args.seeds:
                p = events_of(args.runs_dir, f"alpha000_{c}_{w}_seed{s}")
                if p:
                    base[s] = (parse(p), java_background(args.runs_dir, f"alpha000_{c}_{w}_seed{s}"))
            if not base:
                print(f"[skip] {w}/{c}: nessuna baseline"); continue

            def emit(tag, seed, adf, ajava, bdf, bjava):
                """a - b on every set, both currencies."""
                ca = co2_by_set(adf, lengths, freespeeds, hbefa, sets).set_index("link_set")
                cb = co2_by_set(bdf, lengths, freespeeds, hbefa, sets).set_index("link_set")
                for name in sets:
                    st_a = corridor_background_stats(adf, sets[name], lengths)
                    st_b = corridor_background_stats(bdf, sets[name], lengths)
                    d = corridor_delta(st_a, st_b)
                    row = dict(
                        weight=w, congestion=c, alpha=tag, seed=seed, link_set=name,
                        n_links=len(sets[name]),
                        d_co2_step=ca.loc[name, "co2_average_kg"] - cb.loc[name, "co2_average_kg"],
                        d_co2_cont=ca.loc[name, "co2_fraction_kg"] - cb.loc[name, "co2_fraction_kg"],
                        d_vehicle_hours=d["delta_vehicle_hours"])
                    if deadlocks:
                        ea = corridor_background_stats(adf, sets[name], lengths, deadlocks)
                        eb = corridor_background_stats(bdf, sets[name], lengths, deadlocks)
                        de = corridor_delta(ea, eb)
                        row["n_links_deadlock"] = ea["n_links_excluded"]
                        row["d_vehicle_hours_excl_deadlock"] = de["delta_vehicle_hours"]
                    rows.append(row)
                if ajava is not None and bjava is not None:
                    rows.append(dict(weight=w, congestion=c, alpha=tag, seed=seed,
                                     link_set="full_network", n_links=657005,
                                     d_co2_step=ajava - bjava, d_co2_cont=None,
                                     d_vehicle_hours=None))
                pd.DataFrame(rows).to_csv(out, index=False)

            # noise floor: the two baselines are the same world, seed apart
            if len(base) == 2:
                s1, s2 = args.seeds
                emit("NOISE", f"{s1}v{s2}", base[s1][0], base[s1][1], base[s2][0], base[s2][1])
                print(f"  {w}/{c} NOISE scritto", flush=True)

            for s, (bdf, bjava) in base.items():
                for a in alphas:
                    p = events_of(args.runs_dir, f"alpha{int(a*100):03d}_{c}_{w}_seed{s}")
                    if not p:
                        continue
                    sdf = parse(p)
                    sjava = java_background(args.runs_dir, f"alpha{int(a*100):03d}_{c}_{w}_seed{s}")
                    emit(f"{a:.2f}", s, bdf, bjava, sdf, sjava)
                    print(f"  {w}/{c}/{s}/a={a:.2f} scritto", flush=True)

    print(f"\n{len(rows)} righe -> {out}")


if __name__ == "__main__":
    main()
