"""
Master orchestrator for the IPFT CO2 pipeline.

Runs Steps 0–6 from thesis_overview.md Section 9:

  Step 0 — Feasibility check (alpha × weight regimes)
  Step 1 — (MATSim runs already done externally via scenario_runner.py)
  Step 2 — Term A: background congestion relief (requires HBEFA; stub if not enabled)
  Step 3 — Kinematic reconstruction (inside term_b.py / term_c.py)
  Step 4 — Emission calculation (inside term_b.py / term_c.py)
  Step 5 — Apply weight regimes in Python (no new MATSim runs)
  Step 6 — Net CO2 = Term A + Term B − Term C

For the toy Phase 1 run (single baseline events.xml.zst, no vans inserted):
  - Term A: skipped (HBEFA not enabled)
  - Term B: proxy using background car v_mean (flagged)
  - Term C: computed for representative bus vehicle(s)

Usage:
    python run_pipeline.py --baseline output/ipft_toy/ITERS/it.10/10.events.xml.zst
                           --network  scenarios/ipft_toy/reduced_network.xml
                           --alpha 0.5 --weight medium --n-freight 50

For full 20-run batch, use sensitivity_surface.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Add parent dir to path so imports work when run from project root
sys.path.insert(0, str(Path(__file__).parent))

# Windows: a REDIRECTED stdout defaults to cp1252, which cannot encode the
# arrows and ± used in the progress lines -> UnicodeEncodeError and the whole
# scenario dies after Term C is already computed. A terminal never shows this
# because it is UTF-8, but rotterdam_surface_robust runs every cell with
# subprocess capture_output=True, i.e. redirected. errors="replace" keeps the
# text readable and can never raise. Done at import so it also covers
# sensitivity_surface, which imports run_scenario and prints through it.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):  # not a TextIOWrapper (e.g. captured)
        pass

from feasibility import check_feasibility, run_feasibility_matrix, feasibility_summary
from parse_events import parse_events
from term_a import compute_term_a, TERM_A_STUB
from term_b import compute_term_b
from term_c import compute_term_c
from parameters import (
    BUS_TRIPS_PER_DAY,
    N_FREIGHT_UNITS_TOY,
    WEIGHT_REGIMES,
    VAN_PAYLOAD_CAPACITY_KG,
    VAN_PARCELS_PER_TOUR_MAX,
    VAN_STOP_IDLE_S,
    VAN_STOP_IDLE_LOW_S,
    VAN_STOP_IDLE_HIGH_S,
    VAN_LOAD_FACTOR,
    VAN_TYPES,
    VanType,
    van_type,
)


# ── Single scenario runner ─────────────────────────────────────────────────

def run_scenario(
    baseline_events_path: str,
    network_path: str,
    scenario_events_path: str | None,
    alpha: float,
    weight_regime: str,
    n_freight_units: int = N_FREIGHT_UNITS_TOY,
    n_pickup_stops: int = 5,
    bus_id_override: str | None = None,
    verbose: bool = True,
    with_nox: bool = False,
    sample_rate: float = 1.0,
    bus_trips_per_day: int | None = None,
    transit_prefixes: tuple[str, ...] | None = None,
    bus_id_allowlist: frozenset[str] | set[str] | None = None,
    hb_route_prefixes: tuple[str, ...] = ("EW_",),
    corridor_links_file: str | None = None,
    bus_stop_links_file: str | None = None,
    van_payload_capacity_kg: float | None = None,
    van_parcels_per_tour_max: int | None = None,
    van: VanType | None = None,
    van_stop_idle_s: float = VAN_STOP_IDLE_S,
    van_load_factor: float | None = None,
    recon_seed: int = 42,
    extra_dwell_per_unit_s: float | None = None,
    dwell_in_matsim: bool = False,
    deadlock_links_file: str | None = None,
) -> dict:
    """
    Run the full pipeline for one (alpha, weight_regime) scenario.

    If scenario_events_path is None (Phase 1 — no separate scenario run yet),
    the baseline is used for both components (proxy mode).

    Scale conventions (sample_rate = population sampling rate, 1.0 for the toy,
    0.10 for Rotterdam):
      - n_freight_units is the SIMULATED van count at alpha=0 (what insert_vans
        injected). Terms A and B are computed at simulation scale from events
        and multiplied by 1/sample_rate to obtain real-world kg/day.
      - Term C and the feasibility check use the REAL daily freight
        n_freight_units / sample_rate, because the transit schedule is NOT
        sampled (all real bus departures are simulated 1:1).

    Returns a results dict with Term A, B, C and the net saving (real kg/day).
    """
    weight_per_unit_kg = WEIGHT_REGIMES[weight_regime]
    if bus_trips_per_day is None:
        bus_trips_per_day = BUS_TRIPS_PER_DAY
    if sample_rate <= 0 or sample_rate > 1:
        raise ValueError(f"sample_rate must be in (0, 1], got {sample_rate}")
    # Once the dwell is stamped in the schedule, the handling time stops being a
    # free re-pricing lever: those seconds are an INPUT to MATSim, so changing
    # them needs make_dwell_schedules.py + new runs. Under use_measured_idle the
    # a-priori value survives only inside the sanity threshold, so a sweep would
    # return the same numbers with no warning. Checked here, before the 8-minute
    # event parse, so the sweep fails immediately instead of at the end.
    if dwell_in_matsim and extra_dwell_per_unit_s is not None:
        raise ValueError(
            f"extra_dwell_per_unit_s={extra_dwell_per_unit_s} given together with "
            f"dwell_in_matsim=True. With the dwell simulated in the schedule, Term C "
            f"charges the MEASURED standing: this value would change nothing except "
            f"the guard threshold, and the sweep would silently reproduce the "
            f"unswept numbers. To vary the handling time on dwell runs, regenerate "
            f"the schedules with a different EXTRA_DWELL_PER_UNIT_S and re-run "
            f"MATSim; to keep it a free lever, drop --dwell-in-matsim.")
    scale = 1.0 / sample_rate
    n_freight_units_real = n_freight_units / sample_rate

    # ── Step 0: feasibility (real-world units on the real timetable) ──────
    feas = check_feasibility(
        alpha, n_freight_units_real, weight_per_unit_kg, weight_regime,
        bus_trips_per_day=bus_trips_per_day,
    )
    if verbose:
        print(f"\n[Step 0] {feas}")

    # Corridor link set (Rotterdam): loaded up front so the event parse can keep
    # ONLY the records the terms need (vans + freight buses + corridor background),
    # which cuts the per-parse memory from ~6-8 GB to well under 1 GB. None on the
    # toy, where the parse keeps everything (region-wide Term A).
    # With the dwell-in-MATSim split the bus-stop set is kept as well, so the
    # bus-stop congestion row can be measured from the same parse.
    corr_links = None
    busstop_links = None
    if corridor_links_file is not None:
        from corridor_metrics import load_corridor_links
        corr_links = load_corridor_links(corridor_links_file)
    if bus_stop_links_file is not None and Path(bus_stop_links_file).exists():
        from corridor_metrics import load_corridor_links
        busstop_links = load_corridor_links(bus_stop_links_file)
    keep_links = None
    if corr_links is not None or busstop_links is not None:
        keep_links = frozenset((corr_links or frozenset())
                               | (busstop_links or frozenset()))

    # Deadlocked links: a handful of links in the Zuidplein area hold vehicles
    # for tens of minutes over a few tens of metres. Term A cancels them (they
    # are on both sides of the delta) but Term B does NOT, because it counts
    # tours removed and each removed tour takes its idle fuel with it.
    deadlock_links = None
    if deadlock_links_file and Path(deadlock_links_file).exists():
        from corridor_metrics import load_corridor_links
        deadlock_links = load_corridor_links(deadlock_links_file)
        if verbose:
            print(f"[Step 4] deadlock links excluded from Term B: "
                  f"{len(deadlock_links)} ({deadlock_links_file})")

    # ── Parse baseline events ──────────────────────────────────────────────
    if verbose:
        print(f"\n[Step 3] Parsing baseline events: {baseline_events_path}")
    baseline_vmean, baseline_pax = parse_events(
        baseline_events_path, network_path, verbose=verbose,
        bus_prefixes=transit_prefixes, pax_bus_ids=bus_id_allowlist,
        keep_link_ids=keep_links,
    )

    # ── Parse scenario events (or reuse baseline as proxy) ────────────────
    if scenario_events_path and scenario_events_path != baseline_events_path:
        if verbose:
            print(f"[Step 3] Parsing scenario events: {scenario_events_path}")
        scenario_vmean, scenario_pax = parse_events(
            scenario_events_path, network_path, verbose=verbose,
            bus_prefixes=transit_prefixes, pax_bus_ids=bus_id_allowlist,
            keep_link_ids=keep_links,
        )
    else:
        if verbose:
            print("[Step 3] No separate scenario run — using baseline as proxy for Term B")
        scenario_vmean = baseline_vmean
        scenario_pax = baseline_pax

    # ── Step 2: Term A (simulation scale → ×1/sample_rate) ────────────────
    if scenario_events_path and scenario_events_path != baseline_events_path:
        term_a_result = compute_term_a(baseline_events_path, scenario_events_path,
                                       transit_prefixes=transit_prefixes)
    else:
        term_a_result = TERM_A_STUB
    if verbose:
        status = (f"{term_a_result['term_a_kg'] * scale:.3f} kg (real scale)"
                  if term_a_result["hbefa_enabled"] else "N/A (HBEFA not enabled)")
        print(f"\n[Step 2] Term A: {status}")

    # ── Step 4: Term B (simulation scale → ×1/sample_rate) ────────────────
    term_b_result = compute_term_b(
        baseline_vmean_df=baseline_vmean,
        scenario_vmean_df=scenario_vmean,
        alpha=alpha,
        n_total_vans=n_freight_units,
        van_payload_kg=weight_per_unit_kg,
        payload_capacity_kg=(van_payload_capacity_kg if van_payload_capacity_kg is not None
                             else VAN_PAYLOAD_CAPACITY_KG),
        parcels_per_tour_max=(van_parcels_per_tour_max if van_parcels_per_tour_max is not None
                              else VAN_PARCELS_PER_TOUR_MAX),
        n_pickup_stops=n_pickup_stops,
        van_stop_idle_s=van_stop_idle_s,
        load_factor=(van_load_factor if van_load_factor is not None
                     else VAN_LOAD_FACTOR),
        rng_seed=recon_seed,
        exclude_links=deadlock_links,
        # A whole vehicle, when the van-size sensitivity gives one: it overrides
        # the tare, the payload capacity and the parcel cap above and brings the
        # frontal area and drag with them, so a size cannot be varied by halves.
        # None keeps the headline van and every number it produces.
        van=van,
    )
    if verbose:
        proxy_note = " [PROXY — no vans in baseline run]" if term_b_result["used_proxy"] else ""
        cons_note = (f" [consolidated: {term_b_result['parcels_per_tour']} parcels/tour -> "
                     f"{term_b_result['tours_baseline']}->{term_b_result['tours_scenario']} tours]"
                     if term_b_result["consolidated"]
                     else " [one van per parcel — historical]")
        print(f"[Step 4] Term B (sim scale): {term_b_result['term_b_kg']:.3f} kg{proxy_note}{cons_note}")
        print(f"         Component1 (removed vans): {term_b_result['component1_kg']:.3f} kg")
        print(f"         Component2 (backup vans):  {term_b_result['component2_kg']:.3f} kg")

    # ── Step 4: Term C (real freight on the real timetable) ───────────────
    # Representative-bus selection is delegated to compute_term_c (median link
    # count across H→B candidates) unless an explicit --bus-id was given.
    term_c_kwargs = {}
    if extra_dwell_per_unit_s is not None:
        term_c_kwargs["extra_dwell_per_unit_s"] = extra_dwell_per_unit_s
    if dwell_in_matsim:
        # Freight dwell is simulated in the schedule: replace the a-priori
        # idle with the MEASURED extra standing (scenario − baseline, fleet
        # mean). term_c raises if the standing data is missing rather than
        # silently falling back.
        term_c_kwargs["use_measured_idle"] = True
        term_c_kwargs["stop_standing_baseline"] = baseline_vmean.attrs.get("stop_standing")
        term_c_kwargs["stop_standing_scenario"] = scenario_vmean.attrs.get("stop_standing")
    term_c_result = compute_term_c(
        vmean_df=scenario_vmean,
        pax_timeline=scenario_pax,
        alpha=alpha,
        n_freight_units=n_freight_units_real,
        weight_per_unit_kg=weight_per_unit_kg,
        n_pickup_stops=n_pickup_stops,
        bus_trips_per_day=bus_trips_per_day,
        bus_id_override=bus_id_override,
        bus_id_allowlist=bus_id_allowlist,
        hb_route_prefixes=hb_route_prefixes,
        rng_seed=recon_seed,
        pax_sample_rate=sample_rate,
        **term_c_kwargs,
    )
    if term_c_result["representative_bus_id"] is None and alpha > 0:
        print("[Step 4] WARNING: no H→B bus found in events — Term C = 0. "
              "Check bus_id_allowlist / hb_route_prefixes against the events file.")
    if verbose:
        total_kg = term_c_result.get("total_freight_kg_per_day",
                                     term_c_result.get("total_freight_kg", 0.0))
        print(f"[Step 4] Term C: {term_c_result['term_c_kg_per_day']:.3f} kg/day "
              f"(real daily freight: {total_kg:.1f} kg; F={bus_trips_per_day} trips/day; "
              f"bus={term_c_result['representative_bus_id']})")

    # ── alpha_max: endogenous capacity envelope from simulated pax loads ──
    # pax_timeline is tracked for the H→B vehicles only (bus_id_allowlist), so
    # each entry is one freight-carrying departure. Only meaningful when an
    # allowlist is set (Rotterdam); the toy pax_timeline mixes all transit.
    alpha_max_result = None
    if bus_id_allowlist is not None and scenario_pax:
        from feasibility import compute_alpha_max
        alpha_max_result = compute_alpha_max(
            pax_timeline=scenario_pax,
            weight_per_unit_kg=weight_per_unit_kg,
            n_freight_units_real=n_freight_units_real,
            sample_rate=sample_rate,
            expected_vehicle_ids=bus_id_allowlist,
        )
        if verbose:
            print(f"[Step 0b] alpha_max (endogenous, simulated pax): "
                  f"{alpha_max_result['alpha_max']:.2f} "
                  f"(mean residual {alpha_max_result['mean_trip_residual_kg']:.0f} kg/trip "
                  f"over {alpha_max_result['n_trips_observed']} H→B trips)")
        if alpha_max_result["alpha_max"] < alpha and verbose:
            print(f"[Step 0b] WARNING: alpha={alpha:.2f} exceeds alpha_max="
                  f"{alpha_max_result['alpha_max']:.2f} — passenger loads make this "
                  f"cell infeasible in practice")

    # ── Corridor-local congestion metrics (speed-based, HBEFA-independent) ─
    # Three link sets, measured from the same parse:
    #   corridor  full 163-link van corridor (historical, kept for comparability)
    #   vanrow    corridor MINUS the bus-stop set (row 1: van relief, expected +)
    #   busrow    bus_stop_links.txt (row 2: bus-stop queueing, expected −)
    # vanrow/busrow are DISJOINT by construction (make_bus_stop_links.py) and
    # both use the same baseline − scenario convention: the bus cost comes out
    # negative on its own, no sign is flipped by hand.
    corridor = None
    vanrow = None
    busrow = None
    if corridor_links_file is not None or busstop_links is not None:
        from corridor_metrics import corridor_background_stats, corridor_delta
        from parse_events import load_link_attributes
        link_lengths, _ = load_link_attributes(network_path)

        # The dead-link exclusion is measured BOTH ways and never swapped in
        # silence: *_excl_deadlock is the value that belongs in the thesis
        # (vehicle-hours are a time integral, so a link where time does not
        # advance dominates the total), while the raw one stays so the CSVs
        # written before this fix remain comparable. It reaches only the three
        # congestion rows here; Term B has its own exclusion (see above).
        def _delta_on(links: frozenset[str]) -> dict:
            def _pair(excl):
                b = corridor_background_stats(baseline_vmean, links, link_lengths,
                                              exclude_links=excl)
                s = corridor_background_stats(scenario_vmean, links, link_lengths,
                                              exclude_links=excl)
                return b, s, corridor_delta(b, s)

            base_stats, scen_stats, raw = _pair(None)
            out = {**raw, "baseline": base_stats, "scenario": scen_stats,
                   "n_deadlock_links": 0}
            if deadlock_links:
                base_ex, scen_ex, excl = _pair(deadlock_links)
                out.update({f"{k}_excl_deadlock": v for k, v in excl.items()})
                out["n_deadlock_links"] = base_ex["n_links_excluded"]
                out["baseline_excl_deadlock"] = base_ex
                out["scenario_excl_deadlock"] = scen_ex
            return out

        if corr_links is not None:
            corridor = _delta_on(corr_links)
            if verbose and corridor["baseline"]["mean_speed_ms"]:
                print(f"[Step 2b] Corridor: background speed "
                      f"{corridor['baseline']['mean_speed_ms']:.2f} → "
                      f"{corridor['scenario']['mean_speed_ms']:.2f} m/s, "
                      f"vehicle-hours delta {corridor['delta_vehicle_hours']:+.1f} h (sim scale)")
        if busstop_links is not None:
            busrow = _delta_on(busstop_links)
            if corr_links is not None:
                vanrow = _delta_on(frozenset(corr_links - busstop_links))
            if verbose:
                print(f"[Step 2b] Bus-stop row ({len(busstop_links)} links): "
                      f"vehicle-hours delta {busrow['delta_vehicle_hours']:+.2f} h (sim scale)")

    # ── Passenger travel time on the tracked buses ─────────────────────────
    # Same parse as everything above: the legs ride on vmean_df.attrs, so this
    # costs no extra pass over the events.
    from parse_events import pax_leg_deltas, stop_departure_delay_delta
    pax_metrics = pax_leg_deltas(baseline_vmean.attrs.get("pax_legs"),
                                 scenario_vmean.attrs.get("pax_legs"))
    pax_metrics.update(stop_departure_delay_delta(
        baseline_vmean.attrs.get("stop_delays"),
        scenario_vmean.attrs.get("stop_delays")))
    if verbose and pax_metrics["pax_n_paired"]:
        print(f"[Step 2c] Passengers: {pax_metrics['pax_n_paired']} paired, "
              f"{pax_metrics['pax_d_total_s']:+.1f} s each "
              f"({pax_metrics['pax_d_wait_s']:+.1f} waiting, "
              f"{pax_metrics['pax_d_invehicle_s']:+.1f} aboard)")

    # ── Step 6: Net CO2 saving (all terms at real-world scale) ────────────
    term_a_kg = (term_a_result["term_a_kg"] or 0.0) * scale
    term_b_kg = term_b_result["term_b_kg"] * scale
    term_c_kg = term_c_result["term_c_kg_per_day"]
    net_saving = term_a_kg + term_b_kg - term_c_kg
    # Robust net = Term B − Term C only. On the toy, Term A is pure seed noise
    # (independent from-scratch runs, no frozen baseline), so the full net is
    # noise-dominated after consolidation. net_robust isolates the two terms the
    # toy can actually resolve; the clean Term A arrives on Rotterdam via the
    # warm-started frozen baseline (decision #5 / WS9).
    net_robust = term_b_kg - term_c_kg

    if verbose:
        print(f"\n{'='*60}")
        print(f"  alpha={alpha:.0%}  weight={weight_regime} ({weight_per_unit_kg} kg/unit)")
        print(f"  Term A (congestion relief): {term_a_kg:+.3f} kg CO2")
        print(f"  Term B (van removal):       {term_b_kg:+.3f} kg CO2")
        print(f"  Term C (bus weight cost):   {-term_c_kg:+.3f} kg CO2")
        print(f"  -------------------------------------")
        print(f"  NET CO2 saving:             {net_saving:+.3f} kg CO2/day")
        if not feas.feasible:
            print(f"  [INFEASIBLE: {feas.binding_constraint}] result retained but excluded from policy conclusions")
        print("=" * 60)

    # ── NOx, both fleet worlds ────────────────────────────────────────────
    #
    # Computed HERE and not in a driver of its own, because everything it needs
    # is already wired correctly above: the tour counts, the deadlock exclusion,
    # the measured dwell, the representative bus. A separate script would have to
    # reproduce that wiring, and the day one of them drifted the CO2 and the NOx
    # would silently disagree about what the operation removed.
    #
    # Off by default: it costs one workbook read (~11 MB, once per process) and
    # every existing caller expects the CO2 keys only.
    nox_result = None
    if with_nox:
        import euro_factors as EF
        from term_b import compute_term_b_nox
        from term_c import compute_term_c_nox

        per_bus = (term_c_result["per_bus"][0] if term_c_result.get("per_bus")
                   else None)
        nox_result = {}
        for world, classes in EF.FLEET_SCENARIOS.items():
            bn = compute_term_b_nox(baseline_vmean, scenario_vmean, term_b_result,
                                    classes["van"], exclude_links=deadlock_links)
            # Term B is a sample-scale fleet quantity, exactly like its CO2
            # counterpart, so it takes the same scale factor. Term C is already
            # per-day real: the timetable is not sampled.
            s_van = bn["term_b_nox_g"] * scale
            cn = (compute_term_c_nox(per_bus, classes["bus"],
                                     bus_trips_per_day=bus_trips_per_day)
                  if per_bus and alpha > 0 else None)
            e_pt = cn["term_c_nox_g_per_day"] if cn else 0.0
            nox_result[world] = {
                "van_class": classes["van"], "bus_class": classes["bus"],
                "s_van_nox_g_per_day": s_van,
                "e_pt_nox_g_per_day": e_pt,
                "e_pt_nox_mass_g_per_day": cn["nox_mass_component_g_per_day"] if cn else 0.0,
                "e_pt_nox_dwell_g_per_day": cn["nox_dwell_g_per_day"] if cn else 0.0,
                "idle_rate_g_per_h": cn["nox_idle_rate_g_per_h"] if cn else None,
                "extra_dwell_h_per_day": cn["nox_extra_dwell_h_per_day"] if cn else 0.0,
                # The question the thesis asks is this ratio, not the level.
                # Above 1 the van saving wins; below 1 the operation costs NOx.
                "ratio": (s_van / e_pt) if e_pt else None,
                # superseded speed-curve bracket, kept for the audit trail
                "legacy_e_pt_nox_low_g_per_day": (cn["legacy_term_c_nox_g_per_day_low"]
                                                  if cn else 0.0),
                "legacy_e_pt_nox_high_g_per_day": (cn["legacy_term_c_nox_g_per_day_high"]
                                                   if cn else 0.0),
                "van_km_removed": bn["term_b_km"] * scale,
                "links_outside_ef_speed_range": bn["n_links_outside_ef_speed_range"],
                "v_baseline_kmh": cn["v_baseline_kmh"] if cn else None,
                "v_dwell_raw_kmh": cn["v_dwell_raw_kmh"] if cn else None,
                "v_dwell_calibrated_kmh": cn["v_dwell_fuel_calibrated_kmh"] if cn else None,
                "crosscheck_ec_over_model": cn.get("crosscheck_ratio") if cn else None,
            }
        if verbose:
            print()
            print("[NOx] fleet worlds - the ratio is the result, not the level")
            for world, r in nox_result.items():
                if not r["ratio"]:
                    print(f"  {world:>6}: no bus cost in this cell (alpha=0)")
                    continue
                verdict = "van saving wins" if r["ratio"] > 1 else "bus cost wins"
                print(f"  {world:>6}: S_van {r['s_van_nox_g_per_day']:8.1f} g/day"
                      f"   E_PT {r['e_pt_nox_g_per_day']:7.1f} g/day"
                      f"  (mass {r['e_pt_nox_mass_g_per_day']:+7.1f}"
                      f" + dwell {r['e_pt_nox_dwell_g_per_day']:6.1f}"
                      f" @ {r['idle_rate_g_per_h']:.0f} g/h)"
                      f"   ratio {r['ratio']:5.2f}  {verdict}")

    return {
        "alpha": alpha,
        "weight_regime": weight_regime,
        "weight_per_unit_kg": weight_per_unit_kg,
        "feasible": feas.feasible,
        "binding_constraint": feas.binding_constraint,
        "term_a_kg": term_a_kg,
        "term_b_kg": term_b_kg,
        "term_b_excl_deadlock_kg": (term_b_result["term_b_excl_deadlock_kg"] * scale
                                    if term_b_result.get("term_b_excl_deadlock_kg") is not None
                                    else None),
        "n_deadlock_links": term_b_result.get("n_excluded_links", 0),
        "nox": nox_result,
        "term_c_kg": term_c_kg,
        "net_saving_kg_per_day": net_saving,
        "net_robust_kg_per_day": net_robust,
        "hbefa_enabled": term_a_result["hbefa_enabled"],
        "term_b_proxy": term_b_result["used_proxy"],
        "consolidated": term_b_result["consolidated"],
        "parcels_per_tour": term_b_result["parcels_per_tour"],
        # Taken from the Term B result rather than re-derived here, so the row
        # reports the vehicle that was actually emitted, not the one requested.
        "van_type": term_b_result.get("van_type"),
        "van_tare_kg": term_b_result.get("van_tare_kg"),
        "van_frontal_area_m2": term_b_result.get("van_frontal_area_m2"),
        "van_payload_capacity_kg": term_b_result.get(
            "van_payload_capacity_kg",
            van_payload_capacity_kg if van_payload_capacity_kg is not None
            else VAN_PAYLOAD_CAPACITY_KG),
        "van_stop_idle_s": van_stop_idle_s,
        "tours_baseline": term_b_result["tours_baseline"],
        "tours_scenario": term_b_result["tours_scenario"],
        # The van counts the two event files actually held, against the two tour
        # counts above, which come from the consolidation formula. A campaign that
        # knows how many vans it inserted can compare them and catch a stale
        # warm-plans file, which otherwise produces a plausible wrong number.
        "n_vans_observed_baseline": term_b_result.get("n_vans_observed_baseline"),
        "n_vans_observed_scenario": term_b_result.get("n_vans_observed_scenario"),
        "bus_id": term_c_result["representative_bus_id"],
        "n_bus_links": (term_c_result["per_bus"][0]["n_links_processed"]
                        if term_c_result["per_bus"] else 0),
        "sample_rate": sample_rate,
        "n_freight_units_sim": n_freight_units,
        "n_freight_units_real": n_freight_units_real,
        "bus_trips_per_day": bus_trips_per_day,
        "alpha_max": alpha_max_result["alpha_max"] if alpha_max_result else None,
        "term_a_corridor_kg": ((term_a_result.get("term_a_corridor_kg") or 0.0) * scale
                               if term_a_result.get("term_a_corridor_kg") is not None
                               else None),
        "corridor_delta_vehicle_hours": (corridor["delta_vehicle_hours"]
                                         if corridor else None),
        "corridor_speed_change_ms": (corridor["speed_change_ms"]
                                     if corridor else None),
        "corridor_delta_vehicle_hours_excl_deadlock":
            (corridor.get("delta_vehicle_hours_excl_deadlock") if corridor else None),
        "corridor_speed_change_ms_excl_deadlock":
            (corridor.get("speed_change_ms_excl_deadlock") if corridor else None),
        "corridor_n_deadlock_links": (corridor.get("n_deadlock_links") if corridor else None),
        # ── Dwell-in-MATSim split: the two Term-A rows, reported apart ────
        "term_a_vans_kg": ((term_a_result.get("term_a_vans_kg") or 0.0) * scale
                           if term_a_result.get("term_a_vans_kg") is not None
                           else None),
        "term_a_busstop_kg": ((term_a_result.get("term_a_busstop_kg") or 0.0) * scale
                              if term_a_result.get("term_a_busstop_kg") is not None
                              else None),
        "vanrow_delta_vehicle_hours": (vanrow["delta_vehicle_hours"]
                                       if vanrow else None),
        "vanrow_speed_change_ms": (vanrow["speed_change_ms"] if vanrow else None),
        "busstop_delta_vehicle_hours": (busrow["delta_vehicle_hours"]
                                        if busrow else None),
        "busstop_speed_change_ms": (busrow["speed_change_ms"] if busrow else None),
        "vanrow_delta_vehicle_hours_excl_deadlock":
            (vanrow.get("delta_vehicle_hours_excl_deadlock") if vanrow else None),
        "vanrow_speed_change_ms_excl_deadlock":
            (vanrow.get("speed_change_ms_excl_deadlock") if vanrow else None),
        "vanrow_n_deadlock_links": (vanrow.get("n_deadlock_links") if vanrow else None),
        # Zero by construction (make_bus_stop_links.py picks stop links, none of
        # which deadlock): the column is written so the claim stays checkable.
        "busstop_delta_vehicle_hours_excl_deadlock":
            (busrow.get("delta_vehicle_hours_excl_deadlock") if busrow else None),
        "busstop_speed_change_ms_excl_deadlock":
            (busrow.get("speed_change_ms_excl_deadlock") if busrow else None),
        "busstop_n_deadlock_links": (busrow.get("n_deadlock_links") if busrow else None),
        # Passenger travel time, scenario − baseline: positive = worse off.
        **pax_metrics,
        "dwell_in_matsim": dwell_in_matsim,
        "idle_mode": term_c_result.get("idle_mode", "a-priori"),
        "extra_dwell_s_per_trip": term_c_result.get("extra_dwell_s_per_trip"),
    }


# ── CLI ────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="IPFT CO2 pipeline — single scenario")
    p.add_argument("--baseline", required=True,
                   help="Path to baseline events.xml.zst (alpha=0 run)")
    p.add_argument("--scenario-events", "--scenario-run", dest="scenario_events", default=None,
                   help="Path to scenario events.xml.zst (alpha=x run). "
                        "If omitted, baseline is used as proxy.")
    p.add_argument("--network", required=True,
                   help="Path to the network XML (plain or .gz)")
    p.add_argument("--scenario", default="toy",
                   help="Scenario preset: sets transit filters, F, sample rate, "
                        "pickup stops and the n-freight default (default: toy). "
                        "rotterdam = line 44, rotterdam_L87 = the second corridor. "
                        "Getting this wrong is silent: line 44 runs in EVERY "
                        "Rotterdam simulation, so an L87 run analysed as "
                        "'rotterdam' returns line-44 numbers.")
    p.add_argument("--alpha", type=float, default=0.5,
                   help="Load success rate alpha [0, 1] (default: 0.5)")
    p.add_argument("--weight", default="medium", choices=list(WEIGHT_REGIMES.keys()),
                   help="Weight regime: light / medium / heavy (default: medium)")
    p.add_argument("--n-freight", type=int, default=None,
                   help="SIMULATED freight units / vans at alpha=0 "
                        "(default: preset value — toy 2000, rotterdam 470)")
    p.add_argument("--n-pickup-stops", type=int, default=None,
                   help="Freight delivery stops (default: preset — toy 5, rotterdam 8)")
    p.add_argument("--van-type", default=None, choices=sorted(VAN_TYPES),
                   help="Delivery vehicle for the van-removal saving (default: the "
                        "headline Ford Transit Custom). A type carries its tare, "
                        "payload capacity, parcel cap, frontal area and drag together, "
                        "so a size cannot be varied by halves. A type whose "
                        "specification has no citation in parameters.py is refused.")
    p.add_argument("--bus-id", default=None,
                   help="Bus vehicle ID to use for Term C (default: median-link-count "
                        "bus among the H→B candidates)")
    # ── Layer-3 sensitivity knobs — LINE 44 ONLY ─────────────────────────
    # They live here because rotterdam_surface_robust.py runs every cell as a
    # SUBPROCESS and can reach run_scenario only through this CLI; without them
    # it passed four options argparse rejected and every cell was skipped.
    # Default None = not asked for, so the headline path is untouched, and the
    # gate in scenario_presets.check_sensitivity_allowed refuses them on any
    # scenario other than line 44 unless --force-sensitivity is given.
    p.add_argument("--van-stop-idle", choices=["low", "high", "zero"], default=None,
                   help="Van delivery-stop idle bracket: low = engine off + restart "
                        "~10 s/stop (headline), high = idle through the ~100 s service "
                        "time, zero = omitted (old design). Default: parameters.VAN_STOP_IDLE_S.")
    p.add_argument("--van-load", choices=["mean", "full"], default=None,
                   help="Van tour evaluation mass: mean = tare + payload/2 (headline, "
                        "exact for a stepwise-declining load), full = tare + payload "
                        "(historical departure mass, overstates S_van 4-10%%). "
                        "Default: parameters.VAN_LOAD_FACTOR.")
    p.add_argument("--recon-seed", type=int, default=None,
                   help="RNG seed of the micro-trip kinematic reconstruction — rerun "
                        "with another seed to show the terms and the cell ranking do "
                        "not depend on the draw. Default: 42.")
    p.add_argument("--extra-dwell-s", type=float, default=None,
                   help="Per-parcel freight-handling dwell seconds (TCQSM range 3-15 s). "
                        "Rejected together with --dwell-in-matsim, where the seconds are "
                        "an input to the schedule and this would change nothing.")
    p.add_argument("--nox", action="store_true",
                   help="Also compute NOx for the two fleet worlds (all Euro 5/V "
                        "and all Euro 6/VI), from the EMEP/EEA Appendix 4 curves. "
                        "The reported quantity is the RATIO between the van saving "
                        "and the bus cost, and the bus side is a bracket — see "
                        "PIANO.md 4.2quater. Off by default: it reads an 11 MB "
                        "workbook and every existing caller wants the CO2 keys only.")
    p.add_argument("--deadlock-links", default=None,
                   help="File of link ids to drop from Term B (peak and off-peak "
                        "have their own list: deadlock_links.txt / "
                        "deadlock_links_offpeak.txt). Both values are reported — "
                        "term_b_kg with them, term_b_excl_deadlock_kg without.")
    p.add_argument("--force-sensitivity", action="store_true",
                   help="Apply the four knobs above on a scenario other than line 44. "
                        "Their brackets were built on the line-44 corridor and validated "
                        "nowhere else, so results obtained this way must be reported as "
                        "unvalidated.")
    p.add_argument("--dwell-in-matsim", action="store_true",
                   help="The runs simulate the freight dwell in the schedule "
                        "(make_dwell_schedules.py): Term C idle uses the MEASURED "
                        "extra standing (scenario − baseline) instead of the "
                        "a-priori 10 s + 5 s/parcel convention.")
    p.add_argument("--feasibility-only", action="store_true",
                   help="Only run the feasibility matrix and exit")
    p.add_argument("--output", default=None,
                   help="Save results to JSON file")
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    from scenario_presets import check_sensitivity_allowed, get_preset
    preset = get_preset(args.scenario)

    # Only the knobs actually given count as "requested": the headline run
    # passes none of them and never touches the gate.
    requested = {name: value for name, value in
                 (("van_stop_idle", args.van_stop_idle),
                  ("van_load", args.van_load),
                  ("recon_seed", args.recon_seed),
                  ("extra_dwell_s", args.extra_dwell_s))
                 if value is not None}
    try:
        check_sensitivity_allowed(preset.name, requested, args.force_sensitivity)
    except ValueError as exc:
        sys.exit(f"run_pipeline.py: error: {exc}")

    van_stop_idle_s = ({"low": VAN_STOP_IDLE_LOW_S, "high": VAN_STOP_IDLE_HIGH_S,
                        "zero": 0.0}[args.van_stop_idle]
                       if args.van_stop_idle is not None else VAN_STOP_IDLE_S)
    van_load_factor = ({"mean": 0.5, "full": 1.0}[args.van_load]
                       if args.van_load is not None else None)

    # Resolved before the eight-minute event parse, so an unsourced van type fails
    # in the first second instead of after the work.
    try:
        van = van_type(args.van_type) if args.van_type else None
    except Exception as exc:
        sys.exit(f"run_pipeline.py: error: {exc}")

    n_freight = args.n_freight if args.n_freight is not None else preset.n_freight_units_sim
    n_pickup = args.n_pickup_stops if args.n_pickup_stops is not None else preset.n_pickup_stops

    if args.feasibility_only:
        results = run_feasibility_matrix(
            int(round(n_freight / preset.sample_rate)),
            bus_trips_per_day=preset.bus_trips_per_day,
        )
        feasibility_summary(results)
        return

    result = run_scenario(
        baseline_events_path=args.baseline,
        network_path=args.network,
        scenario_events_path=args.scenario_events,
        alpha=args.alpha,
        weight_regime=args.weight,
        n_freight_units=n_freight,
        n_pickup_stops=n_pickup,
        bus_id_override=args.bus_id,
        sample_rate=preset.sample_rate,
        bus_trips_per_day=preset.bus_trips_per_day,
        transit_prefixes=preset.transit_prefixes,
        bus_id_allowlist=preset.term_c_bus_ids,
        hb_route_prefixes=preset.hb_route_prefixes,
        corridor_links_file=preset.corridor_links_file,
        bus_stop_links_file=preset.bus_stop_links_file,
        dwell_in_matsim=args.dwell_in_matsim,
        deadlock_links_file=args.deadlock_links,
        van=van,
        van_stop_idle_s=van_stop_idle_s,
        van_load_factor=van_load_factor,
        recon_seed=args.recon_seed if args.recon_seed is not None else 42,
        extra_dwell_per_unit_s=args.extra_dwell_s,
        with_nox=args.nox,
    )

    if args.output:
        with open(args.output, "w") as f:
            json.dump(result, f, indent=2)
        print(f"\nResults saved to {args.output}")


if __name__ == "__main__":
    main()
