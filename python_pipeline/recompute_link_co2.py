"""
Recompute background emissions per link from the events, for ANY link set.

Why this exists. Co2TotalsHandler sums into buckets fixed at run time, and the
full emission log is switched off on Rotterdam (8 GB/run), so the runs on disk
can only ever answer for the link sets chosen before they started. Patrick asked
for corridor, one hop, two hops, three hops and the full network. Recomputing
here makes the link set a post-processing knob and the question never costs a
run again.

The same argument applies to the POLLUTANT. The HBEFA tables carry the full set
for `pass. car` (NOx, NO2, PM, PM2.5, PN, CO, HC, NH3, benzene, ...) while
Co2TotalsHandler.java picks the single key Pollutant.CO2_TOTAL and drops the
rest. Reading the table here makes the pollutant a post-processing knob too, so
NOx costs no simulation either — see COMPONENTS below.

TWO METHODS, and the difference is the point:

  average   MATSim's AverageSpeed. getTrafficSituation() picks ONE situation:
            with our two-row table that is FREEFLOW above the stop&go table
            speed and STOPANDGO at or below it. So the factor is a STEP:
            155.97 g/km or 281.23 g/km of CO2, nothing between. A car at
            13 km/h and a car at 12.4 km/h differ by 80% in emissions and by
            nothing in physics. This is why the congestion effect looks smooth
            in vehicle-hours and lumpy in CO2 — it is the emission model
            discretising, not the traffic.

  fraction  MATSim's StopAndGoFraction, blended continuously:
                f_sg = v_sg * (v_ff - v_avg) / (v_avg * (v_ff - v_sg))
                ef   = (1 - f_sg) * ef_ff + f_sg * ef_sg
            with v_ff the LINK's free speed and v_sg the table's stop&go speed,
            clamped to 0 when v_avg is within 1 km/h of free flow and to 1 when
            v_avg <= v_sg (WarmEmissionAnalysisModule:355-361). This responds to
            every km/h, so a congestion delta stops being hostage to a threshold.

WHICH ONE A RUN ACTUALLY USED is not a matter of opinion: it is written in that
run's own `*output_config.xml`, and `run_computation_method()` reads it. Census
of the 326 runs on disk (23/08/2026):

    StopAndGoFraction   234   every Rotterdam result run
    AverageSpeed         92   every toy run, plus the LONGBASE equilibrations

The split is clean along the toy/Rotterdam boundary — no campaign is mixed. An
earlier version of this docstring claimed AverageSpeed was "never overridden in
our config"; that has been false since generate_configs.py:257 started setting
StopAndGoFraction, and the validation below now picks its column from the run
instead of assuming.

Both methods are computed side by side regardless. Reporting them together is
honest: one is what the surface used, the other is what the effect looks like
without the step. VALIDATION: the total on the corridor, in the column matching
the run's own method, must reproduce the background_corridor figure the Java
handler already wrote — the script prints both and their gap.

UNITS: every emission column is in kg, for all components, so that deltas and
sums obey one rule. NOx and PM2.5 are milligrams-to-grams quantities at these
scales, so the printed summary shows them in grams; the CSV stays in kg.

Usage (from project root):
    python python_pipeline/recompute_link_co2.py --run <run_dir> [--sets a.txt b.txt ...]
"""

from __future__ import annotations

import argparse
import csv
import glob
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import numpy as np
import pandas as pd

from parse_events import load_link_attributes, parse_events
from scenario_presets import get_preset

VEH_CAT = "pass. car"

# HBEFA `Component` string -> column slug. The slug is what appears in the CSV
# as `<slug>_average_kg` / `<slug>_fraction_kg`.
#
# Only `pass. car` and `HGV` carry more than CO2(total) in these tables; LCV,
# urban bus, coach and motorcycle are CO2-only, which is exactly why
# MatsimModelImplementation forces every road vehicle to `pass. car` and why van
# and bus NOx cannot come from here at all (they need external Euro-class
# factors — see euro_factors.py).
#
# Exact-string match, so "PM2.5" does NOT pick up "PM2.5 (non-exhaust)".
COMPONENTS: dict[str, str] = {
    "CO2(total)": "co2",
    "NOx": "nox",
    "NO2": "no2",
    "PM2.5": "pm25",
}

# Kept for backward compatibility with anything still importing the old name.
CO2_COMPONENT = "CO2(total)"

# Ratios that hold BY CONSTRUCTION in this table, because every road vehicle is
# a `pass. car` and the table has exactly two traffic situations. Verified
# against MATSim's own warmEmissionEvent output on the toy runs (23/08/2026):
# NOx/CO2 1.9255 g/kg, NO2/NOx 0.3133, PM2.5/CO2 18.19 mg/kg.
#
# These are invariants, not results: anything outside the band is a lookup bug.
# The band is [free-flow ratio, stop&go ratio] with 1% slack on each side.
EXPECTED_RATIOS = {
    ("nox", "co2"): (1.900, 1.968, "g NOx per kg CO2"),
    ("no2", "nox"): (0.309, 0.321, "g NO2 per g NOx"),
    ("pm25", "co2"): (17.92, 18.67, "mg PM2.5 per kg CO2"),
}


def load_hbefa(scenario_dir: Path) -> dict:
    """
    The free-flow and stop&go rows for a passenger car, for every component.

    Returns {slug: {"ff": (v_kmh, ef_g_per_km), "sg": (v_kmh, ef_g_per_km)}}.
    """
    path = scenario_dir / "sample_41_EFA_HOT_vehcat_2020average.csv"
    out: dict[str, dict] = {slug: {} for slug in COMPONENTS.values()}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter=";"):
            if r["VehCat"] != VEH_CAT or r["Component"] not in COMPONENTS:
                continue
            slug = COMPONENTS[r["Component"]]
            sit = "ff" if "Freeflow" in r["TrafficSit"] else "sg"
            out[slug][sit] = (float(r["V_weighted"]), float(r["EFA_weighted"]))
    missing = {slug: sorted({"ff", "sg"} - d.keys()) for slug, d in out.items() if len(d) < 2}
    if missing:
        raise RuntimeError(
            f"free-flow / stop&go rows not both found in {path} for {VEH_CAT}: {missing}")
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


def ef_average_vec(v_kmh, v_sg_kmh: float, ef_ff: float, ef_sg: float):
    """Vectorised ef_average. Identical result, no per-traversal Python floats."""
    return np.where(v_kmh <= v_sg_kmh, ef_sg, ef_ff)


def ef_fraction_vec(v_kmh, v_ff_kmh, v_sg_kmh: float, ef_ff: float, ef_sg: float):
    """
    Vectorised ef_fraction. Identical result to the scalar version.

    This exists for memory, not elegance. The scalar version, called once per
    traversal per component per method, built eight Python lists of a few
    hundred thousand floats each on the big rings - enough to turn an already
    heavy script into a MemoryError. numpy does the same arithmetic in a handful
    of arrays. test_pollutant_columns asserts the two agree exactly.
    """
    with np.errstate(divide="ignore", invalid="ignore"):
        f_sg = (v_sg_kmh * (v_ff_kmh - v_kmh)) / (v_kmh * (v_ff_kmh - v_sg_kmh))
    f_sg = np.clip(np.nan_to_num(f_sg, nan=0.0, posinf=1.0, neginf=0.0), 0.0, 1.0)
    # The two clamps from WarmEmissionAnalysisModule:355-361, applied AFTER the
    # general formula so they win: within 1 km/h of free flow it is pure free
    # flow, at or below the table's stop&go speed it is pure stop&go.
    f_sg = np.where((v_kmh - v_ff_kmh) >= -1.0, 0.0, f_sg)
    f_sg = np.where((v_kmh - v_sg_kmh) <= 0.0, 1.0, f_sg)
    return (1.0 - f_sg) * ef_ff + f_sg * ef_sg


def run_computation_method(run_dir: str | Path) -> str | None:
    """
    Which emission lookup this run actually used, from its own output config.

    Returns "AverageSpeed", "StopAndGoFraction", or None if no config is found.
    MATSim's default when the param is absent is AverageSpeed, so an emissions
    module without the param is reported as AverageSpeed.
    """
    cfgs = sorted(glob.glob(f"{run_dir}/*output_config.xml"))
    if not cfgs:
        return None
    text = Path(cfgs[0]).read_text(encoding="utf-8", errors="replace")
    m = re.search(r'name="emissionsComputationMethod" value="([^"]+)"', text)
    if m:
        return m.group(1)
    return "AverageSpeed" if 'name="emissions"' in text else None


def method_column(method: str | None, slug: str = "co2") -> str:
    """The result column that corresponds to a run's own lookup rule."""
    return f"{slug}_fraction_kg" if method == "StopAndGoFraction" else f"{slug}_average_kg"


def emissions_by_set(vmean_df: pd.DataFrame, link_lengths: dict, link_freespeeds: dict,
                     hbefa: dict, sets: dict[str, frozenset]) -> pd.DataFrame:
    """
    kg of each pollutant from BACKGROUND traffic on each link set, both methods.

    Only `vehicle_type == "background"` is counted. Vans run as mode="car" and do
    generate HBEFA numbers in the simulation, but those are deliberately
    discarded to avoid double-counting with Term B, which computes van emissions
    from longitudinal dynamics instead (see term_a.py).
    """
    bg = vmean_df[vmean_df["vehicle_type"] == "background"]
    slugs = list(COMPONENTS.values())
    rows = []
    for name, links in sets.items():
        d = bg[bg["link_id"].isin(links)]
        if d.empty:
            row = dict(link_set=name, n_links=len(links), n_traversals=0, vkm=0.0,
                       share_below_threshold=None)
            for slug in slugs:
                row[f"{slug}_average_kg"] = 0.0
                row[f"{slug}_fraction_kg"] = 0.0
            rows.append(row)
            continue

        length_km = (d["link_id"].map(link_lengths).astype(float) / 1000.0).values
        v_kmh = (d["v_mean_ms"].astype(float) * 3.6).values
        v_ff = (d["link_id"].map(link_freespeeds).astype(float) * 3.6).values

        # The stop&go table speed is a property of the TRAFFIC SITUATION, not of
        # the pollutant: every row of a given situation carries the same
        # V_weighted. Asserting that here is what makes the step land in exactly
        # the same place for every component, and hence what makes the pollutant
        # ratios invariant. Any component may supply it; they must all agree.
        speeds = {slug: d["sg"][0] for slug, d in hbefa.items()}
        v_sg = next(iter(speeds.values()))
        if any(abs(s - v_sg) > 1e-6 for s in speeds.values()):
            raise RuntimeError(
                f"the stop&go table speed differs between components ({speeds}); "
                f"the step would land in a different place for each pollutant and "
                f"the ratios would stop being meaningful")

        row = dict(link_set=name, n_links=len(links), n_traversals=int(len(d)),
                   vkm=float(length_km.sum()),
                   share_below_threshold=float((v_kmh <= v_sg).mean()))
        for slug in slugs:
            ef_ff, ef_sg = hbefa[slug]["ff"][1], hbefa[slug]["sg"][1]
            ef_a = ef_average_vec(v_kmh, v_sg, ef_ff, ef_sg)
            ef_f = ef_fraction_vec(v_kmh, v_ff, v_sg, ef_ff, ef_sg)
            row[f"{slug}_average_kg"] = float((ef_a * length_km).sum() / 1000.0)
            row[f"{slug}_fraction_kg"] = float((ef_f * length_km).sum() / 1000.0)
        rows.append(row)
    return pd.DataFrame(rows)


# Old name, same function: khop_congestion_deltas and any ad-hoc script that
# imported `co2_by_set` keep working, and the co2_* columns are unchanged.
co2_by_set = emissions_by_set


def check_ratios(res: pd.DataFrame, method: str | None) -> list[str]:
    """
    Verify the by-construction pollutant ratios. Returns a list of complaints.

    Every road vehicle in this scenario is a `pass. car` drawn from a two-row
    table, so NOx/CO2, NO2/NOx and PM2.5/CO2 cannot leave the band spanned by
    the free-flow and stop&go rows. A violation means the lookup is wrong, not
    that the traffic did something interesting.
    """
    col = "fraction" if method == "StopAndGoFraction" else "average"
    problems = []
    for (num, den), (lo, hi, unit) in EXPECTED_RATIOS.items():
        scale = 1e6 if unit.startswith("mg") else 1e3 if unit.startswith("g NOx") else 1.0
        for _, r in res.iterrows():
            d = r[f"{den}_{col}_kg"]
            if d <= 0:
                continue
            ratio = r[f"{num}_{col}_kg"] / d * scale
            if not (lo <= ratio <= hi):
                problems.append(
                    f"{r['link_set']}: {num}/{den} = {ratio:.4f} {unit}, "
                    f"outside [{lo}, {hi}]")
    return problems


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--run", required=True, help="run directory")
    ap.add_argument("--sets", nargs="*", default=None,
                    help="link-set files (default: corridor + all khop_cum*)")
    ap.add_argument("--scenario", default="rotterdam",
                    help="scenario preset the run belongs to (default: rotterdam)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    preset = get_preset(args.scenario)
    root = Path(preset.base_config).parent.parent.parent
    scen = Path(preset.base_config).parent
    hbefa = load_hbefa(scen)

    method = run_computation_method(args.run)
    print(f"run lookup rule: {method or 'UNKNOWN (no output_config.xml)'} "
          f"-> reporting column '{method_column(method)}'")
    for comp, slug in COMPONENTS.items():
        print(f"HBEFA {VEH_CAT} {comp:11s}: "
              f"free-flow {hbefa[slug]['ff'][1]:10.4f} g/km @ {hbefa[slug]['ff'][0]:.2f} km/h | "
              f"stop&go {hbefa[slug]['sg'][1]:10.4f} g/km @ {hbefa[slug]['sg'][0]:.2f} km/h")

    # Link-set files: prefer the preset's own generated_dir, because line 87 keeps
    # its corridor there (generated_L87/corridor_links.txt) and the copy in the
    # scenario root is line 44's. Falling through to `scen` keeps the historical
    # behaviour for the line-44 and toy presets.
    link_dir = (Path(preset.generated_dir)
                if (Path(preset.generated_dir) / "corridor_links.txt").exists() else scen)
    # khop_cum[0-9].txt only: the scenario dir also holds dated backups such as
    # khop_cum1_PRE_LOCKER_20260812.txt, and a bare khop_cum*.txt glob silently
    # mixes a superseded link set into the report next to the live one.
    paths = ([Path(p) for p in args.sets] if args.sets
             else [link_dir / "corridor_links.txt"]
             + sorted(p for p in link_dir.glob("khop_cum?.txt") if p.stem[-1].isdigit()))
    print(f"link sets read from: {link_dir}")
    # line by line, skipping comments: these files carry a header, and splitting
    # the whole text on whitespace would turn each header word into a fake link id
    sets = {}
    for p in paths:
        ids = frozenset(
            tok for line in p.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
            for tok in line.split())
        sets[p.stem] = ids
    keep = frozenset().union(*sets.values())
    print(f"set: {', '.join(f'{k}({len(v)})' for k, v in sets.items())}")

    ev = sorted(glob.glob(f"{args.run}/*output_events.xml.zst"))
    if not ev:
        raise SystemExit(f"no events in {args.run}")
    net = str(root / preset.network_file)
    lengths, freespeeds = load_link_attributes(net)
    df, _ = parse_events(ev[0], net, verbose=False, bus_prefixes=preset.transit_prefixes,
                         pax_bus_ids=preset.term_c_bus_ids, keep_link_ids=keep)

    res = emissions_by_set(df, lengths, freespeeds, hbefa, sets)
    print()
    print(res.to_string(index=False))

    # The pollutants are grams-scale here; show them that way without changing
    # the CSV, which stays in kg so that every column obeys one rule.
    col = "fraction" if method == "StopAndGoFraction" else "average"
    print(f"\n[{col}] in reporting units:")
    for _, r in res.iterrows():
        print(f"  {r['link_set']:22s} CO2 {r[f'co2_{col}_kg']:9.3f} kg | "
              f"NOx {r[f'nox_{col}_kg'] * 1000:8.2f} g | "
              f"NO2 {r[f'no2_{col}_kg'] * 1000:7.2f} g | "
              f"PM2.5 {r[f'pm25_{col}_kg'] * 1e6:7.1f} mg")

    problems = check_ratios(res, method)
    if problems:
        print("\n[RATIO CHECK FAILED] the pollutant ratios are fixed by the table; "
              "a violation is a lookup bug, not a result:")
        for p in problems:
            print(f"  {p}")
    else:
        print("\n[ratio check] NOx/CO2, NO2/NOx and PM2.5/CO2 all inside the "
              "band the two-row table allows")

    # Validation: the corridor total, in the column matching the run's OWN
    # lookup rule, must reproduce the Java bucket.
    # Compare against the WARM column, not the total. The Java bucket also holds
    # cold start, which this script does not recompute and does not need to:
    # cold start is the extra burned in the first minutes after ignition,
    # attributed to the link the vehicle STARTS on, and it does not depend on
    # speed at all. It therefore cannot respond to a bus blocking the lane, and
    # with the plans frozen the same agents start in the same places in both
    # runs, so it cancels in the baseline - scenario difference. Warm-only is
    # the right quantity for a congestion delta, not a shortfall.
    #
    # Note for NOx: cold start is a LARGER share of NOx than of CO2, so the
    # cancellation argument still holds for a delta but an ABSOLUTE background
    # NOx level taken from here is understated. Report deltas.
    tot = sorted(glob.glob(f"{args.run}/*co2_totals.csv"))
    if tot and "corridor_links" in res.link_set.values:
        j = pd.read_csv(tot[0])
        row = j[j.vehicle_class == "background_corridor"]
        if not row.empty:
            warm = float(row.warm_co2_kg.iloc[0])
            cold = float(row.cold_co2_kg.iloc[0])
            mine = float(res[res.link_set == "corridor_links"][method_column(method)].iloc[0])
            gap = 100 * (mine - warm) / warm if warm else float("nan")
            print(f"\n[validate] corridoio, WARM, colonna {method_column(method)}: "
                  f"Java {warm:.2f} kg | ricalcolo {mine:.2f} kg | scarto {gap:+.2f}%")
            print(f"           (cold start {cold:.2f} kg = {100*cold/(warm+cold):.1f}% del totale, "
                  f"non ricalcolato: non dipende dalla velocita e si cancella nel delta)")
            other = method_column("AverageSpeed" if method == "StopAndGoFraction"
                                  else "StopAndGoFraction")
            print(f"           (l'altra regola, {other}: "
                  f"{float(res[res.link_set == 'corridor_links'][other].iloc[0]):.2f} kg, "
                  f"NON e' quella che ha girato)")

    out = args.out or f"output/link_emissions_{Path(args.run).name}.csv"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    res.to_csv(out, index=False)
    print(f"scritto {out}")


if __name__ == "__main__":
    main()
