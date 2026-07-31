"""
Physical constants and vehicle specifications for the IPFT CO2 pipeline.

Sources (updated 2026-05-20 after literature audit):
  - Diesel CO2: IPCC/GHG Protocol standard values
  - Bus tare: MAN Lion's City 12m (Wikipedia / MAN spec) — 10,882 kg confirmed
  - Bus GVW: EU Directive 96/53/EC art. 1 (two-axle bus, 18,000 kg)
  - Bus CD: 0.60–0.66 range from ResearchGate bus aerodynamics review
  - Bus frontal area: 7.5 m² (2.55 m width × ~3.0 m height − mirrors)
  - Bus ηt: 0.35–0.40 peak TTW for diesel HDV (Beckers et al. 2021 / Zhai 2008);
            0.37 placeholder is at the optimistic end of real urban cycle range
            (real average 0.32–0.35); sensitivity-analyse over 0.32–0.40
  - Bus idle rate: DOE Fact #861 / Euro-6 city bus auto-stop; 0.5–1.0 L/h range;
                   0.9 L/h is the midrange for stop-start-disabled operation
  - Bus rolling resistance: Mendoza-Sanchez (2021) MDPI Sustainability 13(2)
  - Van tare: Ford Transit Custom L2 panel-van (Wikipedia, vanreviewer.co.uk)
  - Van CD: Ford Transit Custom 2024 — officially 0.37 (Engineering News 2024)
  - Van frontal area: 1.99 m width × ~1.92 m height (L1/L2 low-roof) → ~3.8 m²
  - Van rolling resistance: ICCT light-duty driving resistance study (2016)
                            cross-checked vs Ford Transit USA coast-down tests
  - Package weights: DPD NL (max 31.5 kg), PostNL (max 30 kg),
                     MDPI Sustainability 14(23) 2022 (e-commerce parcel distribution)
  - Extra freight dwell per parcel: TCQSM (TRB) boarding-time analogue, 1.75–4.5 s
                     per passenger → ~5 s per parcel (door hand-off);
                     primary IPFT studies (Cheng 2024, Ghilas et al. 2018) treat
                     it as a free calibration parameter
  - F (bus trips/day): RET Rotterdam line 30 (Alexander–Schollevaar) timetable
                     confirms ≥60 trips/day per direction on backbone urban lines
"""

# ── Diesel energy and CO2 ──────────────────────────────────────────────────
CALORIFIC_VALUE_DIESEL_MJ_PER_L = 35.8   # IPCC/GHG Protocol standard
CO2_FACTOR_KG_PER_L = 2.65               # IPCC/GHG Protocol standard
AIR_DENSITY_KG_M3 = 1.225                # kg/m³ at sea level, 15°C
GRAVITY_M_S2 = 9.81                      # m/s²

# ── Bus — MAN Lion's City 12m diesel ──────────────────────────────────────
# Representative standard urban bus; verify with RET for actual Rotterdam fleet model.
BUS_TARE_KG = 10882
# Must equal seats + standingRoomInPersons in reduced_vehicles_5min_headway.xml
# (currently 70 seats + 10 standing = 80). Feasibility check vs MATSim simulation
# stays consistent.
BUS_PASSENGER_CAPACITY = 80

# EU Directive 96/53/EC: max permissible mass for 2-axle bus = 18,000 kg
BUS_MAX_GVW_KG = 18000

# Derived: GVW − tare − full passenger load = 18000 − 10882 − 80×75 = 1118 kg theoretical.
# Using 1000 kg as conservative operational upper bound.
# TODO: confirm with RET operator — this value is the binding feasibility constraint.
BUS_FREIGHT_CAPACITY_KG = 1000

BUS_CD = 0.66                            # drag coefficient (literature range 0.60–0.66; upper bound)
BUS_FRONTAL_AREA_M2 = 7.5                # 2.55 m width × ~3.0 m effective height (was 7.0; updated to spec)
BUS_ROLLING_RESISTANCE = 0.009           # rolling resistance coefficient f
BUS_DRIVETRAIN_EFF = 0.37               # fuel-to-wheel efficiency ηt (Zhai et al. 2008)
# Midrange of DOE transit-bus idle range 0.8–1.0 L/h. Cancels in scenario deltas;
# absolute value only matters for the additive CO2_idle_extra (extra freight dwell).
BUS_IDLE_FUEL_RATE_L_PER_S = 0.9 / 3600  # 0.9 L/h → L/s

# ── Van — Ford Transit Custom (representative urban LCV) ──────────────────
VAN_TARE_KG = 1950                       # L2 panel-van kerb weight (range 1786–2076)
VAN_CD = 0.37                            # drag coefficient (Ford Transit Custom 2024, officially)
VAN_FRONTAL_AREA_M2 = 3.8                # 1.99 m × ~1.92 m L1/L2 low-roof (was 4.2; updated)
VAN_ROLLING_RESISTANCE = 0.010           # rolling resistance f (ICCT 2016 / Ford coast-down)
VAN_DRIVETRAIN_EFF = 0.36                # fuel-to-wheel efficiency ηt (range 0.34–0.38)
# Idle fuel rate — the van analogue of the bus idle term. A diesel engine at a
# stop (v=0 in the driving cycle: traffic lights, queues) keeps running and burns
# fuel that the traction formula (P=0 at v=0) sets to zero. WLTC urban is ~25%
# idle, so omitting this under-counts absolute van fuel. Applied ONLY at v≈0
# (true stops), NOT during braking — a modern diesel cuts injection on overrun
# (fuel cut-off), so deceleration burns ~0, not idle.
# 0.7 L/h: midrange for a ~2.0 L LCV diesel at idle (range 0.6–1.0 L/h; smaller
# engine than the 0.9 L/h city bus). Sensitivity-analyse 0.6–1.0.
VAN_IDLE_FUEL_RATE_L_PER_S = 0.7 / 3600  # 0.7 L/h → L/s

# Van DELIVERY-STOP idle (decision: meeting 2026-06-24, bracketed — HANDOFF §3.4/§7).
# Distinct from the in-traffic idle above: this is the fuel burned while the van is
# PARKED to hand parcels over at each corridor locker stop it serves (n_pickup_stops
# per tour, the locker-equivalent counterfactual). It is BRACKETED rather than fixed:
#   LOW  (conservative headline) = engine off + restart, ~10 s idle-equivalent per stop
#         (Argonne National Laboratory idle-reduction "10-second rule").
#   HIGH (upper sensitivity)      = idle through the full ~100 s service time per stop
#         (Amazon Last-Mile Routing Research Challenge service-time data).
# Charged on every tour (removed and backup vans); since removed tours outnumber backup
# tours it raises the van-removal saving, so omitting it (the old design) was conservative.
VAN_STOP_IDLE_LOW_S = 10.0    # engine off + restart equivalent (Argonne 10-second rule)
VAN_STOP_IDLE_HIGH_S = 100.0  # idle through service time (Amazon LMRRC)
VAN_STOP_IDLE_S = VAN_STOP_IDLE_LOW_S  # default = conservative headline (low bound)

# Van load capacity — the basis of the consolidation counterfactual (see below).
VAN_PAYLOAD_CAPACITY_KG = 1100           # Ford Transit Custom useful payload (range 1000–1400)
VAN_PARCELS_PER_TOUR_MAX = 150           # parcels handled per delivery tour (Boysen et al. 2021;
                                         # Spectrum last-mile survey: 150–200 parcels, 100–150
                                         # stops over an 8-h shift). Space cap on one tour.

# ── Van consolidation (decision #1, approved by Patrick 2026-06-17) ────────
# The toy inherited a "one van per parcel" error: it dispatched N vans, each
# carrying a single package. Real urban delivery consolidates parcels onto
# tours: one van carries C_van(w) parcels per tour, so the (1−α)·N undelivered
# parcels need only ⌈(1−α)·N / C_van(w)⌉ van TOURS, not (1−α)·N single trips.
#
#   C_van(w) = min(VAN_PARCELS_PER_TOUR_MAX, ⌊VAN_PAYLOAD_CAPACITY_KG / w⌋)
#            → light(3 kg)=150, medium(10 kg)=110, heavy(25 kg)=44
#
# The consolidated van is loaded with C_van(w)·w kg (payload 450 / 1100 / 1100 kg
# for light / medium / heavy), so it is HEAVIER than the per-parcel van — Term B
# counts FEWER but HEAVIER van trips. This is the locker-equivalent counterfactual:
# the same parcels that ride the bus to the corridor lockers would otherwise be
# delivered by consolidated vans running the same corridor.
#
# CONSOLIDATE_VANS toggles between the corrected (True) and the historical
# one-van-per-parcel (False) design, so the thesis can show both (before/after).
CONSOLIDATE_VANS = True

# ── Van tour evaluation mass: MEAN of the declining load (decided 2026-07-08) ─
# A real tour sheds parcels at every locker, so evaluating the whole tour at the
# full departure payload OVERSTATES S_van (measured on the toy: light -4/-5%,
# medium/heavy -9/-10% at alpha=1) — the one bias that would favour IPFT.
# Because traction power is affine in mass, a load that declines stop by stop is
# equivalent (verified <0.02%) to a constant load at the tour's time-averaged
# mass. With n evenly spread lockers, each dropping 1/n of the payload, plus the
# final empty leg to the terminal, the time-averaged remaining fraction is
# EXACTLY 1/2 (independent of n). Hence the headline evaluation mass is
#   M_tour = VAN_TARE_KG + VAN_LOAD_FACTOR * C_van(w)·w,  VAN_LOAD_FACTOR = 0.5.
# Full-load (1.0) is kept available as a sensitivity via the load_factor
# overrides threaded through term_b / run_pipeline / sensitivity_surface.
VAN_LOAD_FACTOR = 0.5

# ── Van kinematic reconstruction method (decided 2026-06-17, HANDOFF §14) ──
# "wltc"         : micro-trip reconstruction from the WLTC cycle (van_cycles.py).
#                  Captures within-link stop-and-go -> realistic absolute fuel.
#                  Regime-aware: recombine in band, cruise above, scale below.
# "speed_change" : historical constant-speed-per-link (Kickhofer & Kern 2015).
#                  Ignores within-link stop-and-go -> under-estimates ~3-5x.
#                  Kept only for the before/after comparison.
VAN_KINEMATICS = "wltc"  # "wltc" | "speed_change"

# ── Bus on-board freight SPACE cap (constraint #3) ─────────────────────────
# The weight/GVW checks (Constraint 1/2) never test whether the parcels PHYSICALLY
# FIT in the bus. BUS_FREIGHT_NMAX_PARCELS caps the parcels carried per bus trip
# (on-board locker compartments). Commercial banks hold 40–80 (DHL ~100); declared
# as a CONSTRUCTED value (no literature m³/parcels-per-bus number). Swept 40/70 in
# the Rotterdam sensitivity (WS7); default 70 here — on the toy it never binds
# (~33 parcels/trip at α=1), so the headline toy result is unaffected.
BUS_FREIGHT_NMAX_PARCELS = 70


def c_van(weight_per_unit_kg: float,
          payload_capacity_kg: float = VAN_PAYLOAD_CAPACITY_KG,
          parcels_per_tour_max: int = VAN_PARCELS_PER_TOUR_MAX) -> int:
    """Parcels a single van carries per consolidated tour for a given package weight.

    C_van(w) = min(space cap, ⌊payload capacity / w⌋). The binding cap is space
    for light parcels (150) and weight for heavy parcels (1100/w).

    payload_capacity_kg / parcels_per_tour_max default to the Ford Transit Custom
    base values; they are overridden in the van-capacity SCREENING (HANDOFF §8) to
    sweep smaller (~750 kg) and larger (~1400 kg) van types. The override must be
    applied consistently in the van-insertion generator (van count) and the Term B
    post-processing (loaded mass), or the two would disagree.
    """
    if weight_per_unit_kg <= 0:
        return parcels_per_tour_max
    return int(min(parcels_per_tour_max,
                   payload_capacity_kg // weight_per_unit_kg))

# ── Passengers ────────────────────────────────────────────────────────────
AVG_PERSON_WEIGHT_KG = 75               # EN 13035-1 / NEN-EN 12831 standard

# RESOLVED 2026-07-30. The 2026-05-26 note claimed MATSim emits no
# PersonEntersVehicle for the transit driver: that was wrong. It does — measured
# 98 of them on the 98 line-44 H→B departures, exactly one per trip. What was
# actually happening is the reverse: parse_events matched ONLY that event, so the
# driver was the sole "passenger" ever counted while the real boardings
# (PersonEntersPtVehicle, a separate xml type) were dropped.
# Now: parse_events counts PT boardings only, and the driver is added back as the
# +1 occupant in dynamic_mass.occupant_mass_kg — the same +1 that
# feasibility.check_feasibility and feasibility.compute_alpha_max already used.
DRIVER_INCLUDED_IN_PAX_COUNT = True     # driver added in dynamic_mass.occupant_mass_kg

# ── Package weight regimes (Dutch urban parcel market) ────────────────────
# Light:  3 kg — dominant e-commerce category (2.5–5 kg, MDPI 2022)
# Medium: 10 kg — clothing, electronics
# Heavy:  25 kg — large parcels (near DPD/PostNL limit of 30–31.5 kg)
PACKAGE_WEIGHT_LIGHT_KG = 3.0
PACKAGE_WEIGHT_MEDIUM_KG = 10.0
PACKAGE_WEIGHT_HEAVY_KG = 25.0

WEIGHT_REGIMES = {
    "light":  PACKAGE_WEIGHT_LIGHT_KG,
    "medium": PACKAGE_WEIGHT_MEDIUM_KG,
    "heavy":  PACKAGE_WEIGHT_HEAVY_KG,
}

# ── Scenario parameters ────────────────────────────────────────────────────
ALPHA_VALUES = [0.0, 0.25, 0.50, 0.75, 1.0]  # load success rates

# Random seeds applied to every (alpha, congestion) combination.
# Total MATSim runs = len(RANDOM_SEEDS) × |ALPHA_VALUES| × 2 (peak + off-peak).
RANDOM_SEEDS = [4711, 9876]

# Extra seeds run ONLY for the alpha=0 baseline. The baseline is the reference
# for all 30 scenarios — any seed noise in α=0 propagates into every Term A and
# Term B delta. If after the first 20-run batch the baseline Term A spread
# exceeds ~10% of the scenario delta, extend this list (e.g. [1234, 5678])
# to raise baseline statistical confidence without doubling the full matrix.
# Keep empty for the default 20-run plan.
BASELINE_EXTRA_SEEDS: list[int] = []

# Bus trips per day on the H→B route — TOY value.
# Confirmed from RET line 30 Alexander–Schollevaar timetable: ≥60 trips/day
# per direction on a backbone urban line (06:50–00:15, 15–30 min headways).
# https://www.ret.nl/en/home/travelling/timetable/bus-30.html
# ROTTERDAM: 98 (line 44 H→B departures only, 06:05–24:01 — NOT 197, which is
# both directions; the return direction carries no freight). Lives in
# scenario_presets.ROTTERDAM — do not change this constant for Rotterdam runs.
BUS_TRIPS_PER_DAY = 60

# Number of freight units in the toy scenario (one van dispatched per unit at α=0).
# Sized to ~10% of the 20,000 background agents — the literature share for urban
# freight commercial vehicle traffic (Allen et al. 2018; Holguín-Veras et al. 2016).
# Below ~5% the Term A congestion signal is dominated by run-to-run seed noise.
# ROTTERDAM (derived 2026-06-10, scenarios/ipft_rotterdam/derive_n_freight.py):
#   corridor catchment = persons with home within 400 m of a line-44 H→B stop
#   (3,759 in the 10% sample → 37,590 real) × 0.125 parcels/person/day
#   (NL e-commerce 0.10–0.15) → N_real ≈ 4,700 units/day, N_sim = 470 vans.
#   Lives in scenario_presets.ROTTERDAM (n_freight_units_sim=470, sample_rate=0.10).
#   Scale convention: insert_vans + Terms A/B work at SIMULATED scale (×10 at the
#   end); Term C + feasibility use REAL units (transit schedule is not sampled).
N_FREIGHT_UNITS_TOY = 2000

# Extra freight unloading dwell time (seconds per freight unit unloaded at a stop).
# Source: TCQSM (TRB) door-hand-off boarding-time analogue, 1.75–4.5 s/passenger.
# Cheng (2024 Sci. Rep.) and Ghilas (2018) leave it as a calibration parameter.
# 5 s = midrange door hand-off + buffer. Sensitivity-analyse over 3–15 s.
EXTRA_DWELL_PER_UNIT_S = 5.0

# Fixed overhead at each freight pickup stop (seconds), in addition to the
# per-unit time. Covers door opening, ramp deployment, signature/handoff
# ritual that happens once per stop regardless of how many parcels.
# Placeholder: TCQSM suggests 5–15 s of overhead beyond passenger boarding;
# IPFT-specific literature (Ghilas 2018; Cheng 2024) treats it as a
# calibration parameter. Default 10 s is conservative midrange.
# IMPORTANT for Term C interpretation: with 5 pickup stops × F=60 trips,
# this overhead drives the bulk of CO2_idle_extra at low units_per_stop.
# At α=1.0 medium: ~10 s × 5 stops × 60 trips × 0.9/3600 L/s × 2.65 = 1.99 kg/day
# (vs 12.6 kg/day with the old 30 s placeholder). Sensitivity-analyse 0–30 s.
EXTRA_DWELL_FIXED_OVERHEAD_S = 10.0

# DWELL TIME MODEL — SERIAL vs PARALLEL (decision deferred 2026-05-26)
# Current implementation in dynamic_mass.compute_extra_dwell_time is SERIAL:
# every freight stop adds `fixed_overhead + per_unit × n_units` to the bus
# total dwell, IN ADDITION to the ~3 min passenger dwell already in MATSim's
# v_mean. This is the CONSERVATIVE choice — it overestimates Term C.
#
# A more realistic PARALLEL model would assume freight is unloaded by a
# dedicated handler simultaneously with passenger boarding, so extra dwell
# is only the EXCESS over passenger dwell:
#     extra_dwell = max(0, freight_unload_time - 180s_passenger_baseline)
#
# Under the parallel model, with our toy scenarios (≤7 units/stop):
#     freight_unload = 10 + 5×6.67 = 43.3s  <<  180s passenger dwell
#     → extra_dwell = 0 → CO2_idle_extra = 0
#     → Term C drops to ~0.5-1.5 kg/day (only the running/mass component)
#
# Why we keep SERIAL for the toy thesis:
#   - Conservative: overestimating Term C makes the NET CO2 saving look WORSE
#     than parallel would. Robust IPFT conclusions hold under both models.
#   - Operational reality: IPFT freight handling protocols (signature, scan,
#     locker access) often DO add serial time even with dedicated handlers.
#   - Sensitivity safe: results are not load-bearing on this assumption since
#     Term B dominates by 100×.
#
# TODO for Rotterdam (Phase 5): introduce BUS_FREIGHT_DWELL_OVERLAP_FRACTION
# (0=serial, 1=parallel) and run sensitivity over {0, 0.5, 1.0}. If real
# operator data is available (RET, PostNL pilot), use measured overlap.
DWELL_MODEL = "serial"  # "serial" | "parallel" (current: serial, conservative)

# Vehicle ID prefixes used to classify vehicles from MATSim events.xml — TOY values.
# ROTTERDAM uses TWO separate filters (see scenario_presets.py, decided 2026-06-10):
#   - transit_prefixes ("veh_",): ALL transit vehicles, excluded from Term A
#     background (their HBEFA values are fake pass-car numbers);
#   - term_c_bus_ids: the exact 197 line-44 vehicle ids (transitLine 99437),
#     used for Term C and the passenger-load timeline. A prefix tuple would
#     risk matching vehicles outside the line-44 range.
BUS_ID_PREFIXES = ("EW_lower_", "EW_upper_", "NS_")
VAN_ID_PREFIX = "backup_van_"
