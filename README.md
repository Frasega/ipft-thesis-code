# IPFT thesis code

Code accompanying the MSc thesis on **Integrated Passenger and Freight Transport (IPFT)**:
an agent-based model, built on the open-source MATSim framework, that computes the net CO2
balance of shifting a fraction of parcel deliveries from dedicated vans onto scheduled
public-transport buses.

The net daily saving is decomposed as

```
Net CO2 saving = S_cong + S_van - E_PT   [kg CO2/day]
```

- `S_cong` congestion-relief saving: fewer vans on the road, so background traffic emits less
- `S_van` van-removal saving: the CO2 of the van tours no longer driven
- `E_PT` additional public-transport emissions: the laden bus burns more fuel

Author: Francesco Regazzoni (TU Delft).

## Authorship

All code in this repository, the Python pipeline and the Java model classes, was written by
the author. What the model relies on but does not author or include:

- MATSim: the open-source agent-based transport framework the model runs on. The Java classes
  plug into it; they are not part of MATSim itself.
- Rotterdam scenario data (road network, synthetic population and plans, GTFS transit
  schedule): provided by the XCARCITY project (Li et al., 2025).
- HBEFA emission factors: an external emission-factor dataset.

## Repository layout

```
python_pipeline/                     the emission + analysis pipeline (Python)
  parameters.py                      central parameters and vehicle capacities
  emission_formula.py                longitudinal-dynamics fuel/CO2 model
  dynamic_mass.py                    weight-dependent van mass over a tour
  sort_cycles.py / van_cycles.py     kinematic reconstruction (SORT bus, WLTC van)
  build_wltc_cycles.py               driving-cycle preparation
  parse_events.py                    MATSim event parsing (memory-lean)
  term_a.py / term_b.py / term_c.py  the three balance terms (S_cong, S_van, E_PT)
  corridor_metrics.py                corridor-restricted congestion indicators
  run_pipeline.py                    end-to-end run for one scenario cell
  sensitivity_surface.py             the sensitivity surface driver
  feasibility.py                     feasibility envelope (alpha_max)
  insert_vans.py                     inject the backup-van agents into a plans file
  make_toy_longbase.py               warm-start equilibration (toy)
  make_toy_warm_scenarios.py         warm scenario generation (toy)
  make_toy_screening_scenarios.py    fixed-variable screening (toy)
  make_rotterdam_warm_scenarios.py   warm scenario generation (Rotterdam)
  make_dwell_schedules.py            per-alpha transit schedules: freight dwell
                                     (minimumStopDuration) + isBlocking
  make_bus_stop_links.py             the bus-stop link set for the Term A split
  scenario_presets.py                toy vs Rotterdam configuration
  scenario_runner.py                 batch MATSim runner
  rotterdam_surface_robust.py        robust (paired-seed) surface post-processing
  screening_analysis.py / screening_bus_capacity.py
  validate_van_consumption.py        van fuel validation vs official WLTP
  scong_toy_corridor_decomp.py       S_cong decomposition (volume vs speed)
  test_dwell_idle_guard.py           guards on the measured-idle path
  *_check.py / *_diag.py             validation and diagnostic scripts

scenarios/ipft_rotterdam/            Rotterdam-specific setup and analysis (Python)
  make_van_corridor.py               corridor = the links the vans actually drive on
  extract_corridor_data.py           line-44 vehicles + (deprecated) bus-buffer corridor
  derive_n_freight.py                daily parcel demand from the line-44 catchment
  generate_pt_vehicles.py, make_*_config.py, check_*.py, plot_corridor_map.py, ...

src/main/java/org/matsim/project/    MATSim model classes (Java)
  MatsimModelImplementation.java     HBEFA + emission module wiring
  Co2TotalsHandler.java              per-run CO2 aggregation event handler
  RunMatsimModelImplementation.java  entry point
```

## Freight dwell inside MATSim

The parcel handover at each stop is simulated rather than only accounted for afterwards.
`make_dwell_schedules.py` writes, per load rate, a transit schedule in which the delivery
stops of the freight route carry a `minimumStopDuration` (10 s per stop + 5 s per parcel)
and the stop facilities are marked `isBlocking`, so a stopped bus holds the traffic lane.
The baseline uses the same schedule with zero freight dwell, so the passenger stop — which
happens in both worlds — cancels in the difference and only the extra freight seconds are
measured.

Two consequences for the accounting:

- `parse_events.py` subtracts the standing time from the stop link's travel time, so the
  per-link mean speed describes the driving part only. Left in, the kinematic
  reconstruction would charge traction fuel for accelerations that never happened, while
  the same seconds are already charged as idle in `E_PT`.
- `term_c.py` can take the extra idle from the simulation (`use_measured_idle`) instead of
  the a-priori convention. Where the timetable had slack the bus was standing anyway, so
  the parcels add no standing time and no extra fuel; the a-priori figure is an upper bound.

The congestion effect is reported on two disjoint link sets, never merged into one number:
the van relief on the corridor minus the stop links, and the cost of buses held at stops on
those stop links plus their upstream links (`make_bus_stop_links.py`).

## Java

The Java classes plug into the MATSim example project (MATSim 2026.0) and are not a
standalone build; they are provided here to document the emission wiring used in the runs.
