package org.matsim.project;

import com.google.inject.Inject;
import com.google.inject.Singleton;
import org.matsim.contrib.emissions.EmissionModule;
import org.matsim.contrib.emissions.Pollutant;
import org.matsim.contrib.emissions.events.ColdEmissionEvent;
import org.matsim.contrib.emissions.events.ColdEmissionEventHandler;
import org.matsim.contrib.emissions.events.WarmEmissionEvent;
import org.matsim.contrib.emissions.events.WarmEmissionEventHandler;
import org.matsim.core.controler.OutputDirectoryHierarchy;
import org.matsim.core.controler.events.IterationEndsEvent;
import org.matsim.core.controler.events.IterationStartsEvent;
import org.matsim.core.controler.events.StartupEvent;
import org.matsim.core.controler.listener.IterationEndsListener;
import org.matsim.core.controler.listener.IterationStartsListener;
import org.matsim.core.controler.listener.StartupListener;

import java.io.IOException;
import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;

/**
 * Aggregates warm+cold CO2_TOTAL per vehicle class (background / van / transit)
 * and writes <runId.>co2_totals.csv into the output directory at the end of
 * every iteration (overwritten — the file always reflects the LAST iteration).
 *
 * Rationale: with isWritingEmissionsEvents=false the emission events are not
 * written into events.xml (which would otherwise grow to ~8 GB/run on the
 * Rotterdam network), so Term A reads this CSV instead of parsing events.
 * The handler registers itself on EmissionModule.getEmissionEventsManager(),
 * which receives emission events regardless of the writing flag.
 *
 * Vehicle classes by id prefix (covers both toy and Rotterdam):
 *   backup_van_*                  -> van      (discarded by Term A, Term B uses formula)
 *   veh_* / EW_lower_* / EW_upper_* / NS_* -> transit  (excluded from Term A)
 *   everything else               -> background (THE Term A quantity)
 */
@Singleton
public class Co2TotalsHandler implements WarmEmissionEventHandler, ColdEmissionEventHandler,
		StartupListener, IterationStartsListener, IterationEndsListener {

	private static final String[] TRANSIT_PREFIXES = {"veh_", "EW_lower_", "EW_upper_", "NS_"};
	private static final String VAN_PREFIX = "backup_van_";

	private final EmissionModule emissionModule;
	private final OutputDirectoryHierarchy controlerIO;
	private final org.matsim.core.api.experimental.events.EventsManager globalEvents;
	private final org.matsim.core.config.Config config;

	private double warmBackground, warmVan, warmTransit;
	private double coldBackground, coldVan, coldTransit;

	// Corridor-local background CO2: far better signal-to-noise for Term A than
	// the region-wide sum (the van treatment lives on ~1,400 corridor links).
	// Loaded from corridor_links.txt next to the config (or its parent dir);
	// empty set = feature disabled, only the three global rows are written.
	private java.util.Set<String> corridorLinks = java.util.Set.of();
	private double warmBackgroundCorridor, coldBackgroundCorridor;

	// Dwell-in-MATSim split (2026-07-30): the van relief and the bus-stop
	// queueing are measured on two DISJOINT link sets and reported apart.
	//   background_corridor_exstop = corridor MINUS the bus-stop set (row 1, + saving)
	//   background_busstop         = bus_stop_links.txt (row 2, − cost: the 7
	//                                car-mode line-44 stop links + 1-hop upstream)
	// bus_stop_links.txt is produced by python_pipeline/make_bus_stop_links.py;
	// absent file = feature off, only the historical rows are written.
	private java.util.Set<String> busStopLinks = java.util.Set.of();
	private double warmBackgroundBusstop, coldBackgroundBusstop;
	private double warmBackgroundCorridorExstop, coldBackgroundCorridorExstop;

	@Inject
	public Co2TotalsHandler(EmissionModule emissionModule, OutputDirectoryHierarchy controlerIO,
							org.matsim.core.api.experimental.events.EventsManager globalEvents,
							org.matsim.core.config.Config config) {
		this.emissionModule = emissionModule;
		this.controlerIO = controlerIO;
		this.globalEvents = globalEvents;
		this.config = config;
	}

	private java.util.Set<String> loadLinkSet(String filename) {
		for (String candidate : new String[]{filename, "../" + filename}) {
			try {
				java.net.URL url = org.matsim.core.config.ConfigGroup
						.getInputFileURL(this.config.getContext(), candidate);
				java.util.Set<String> links;
				try (java.util.stream.Stream<String> lines =
							 Files.lines(Paths.get(url.toURI()), StandardCharsets.UTF_8)) {
					links = lines.map(String::trim)
							.filter(s -> !s.isEmpty())
							.collect(java.util.stream.Collectors.toUnmodifiableSet());
				}
				org.apache.logging.log4j.LogManager.getLogger(Co2TotalsHandler.class)
						.info("Co2TotalsHandler: link set loaded from {} ({} links)",
								candidate, links.size());
				return links;
			} catch (Exception ignored) {
				// try next candidate; absent file = feature off
			}
		}
		return java.util.Set.of();
	}

	@Override
	public void notifyStartup(StartupEvent event) {
		this.corridorLinks = loadLinkSet("corridor_links.txt");
		this.busStopLinks = loadLinkSet("bus_stop_links.txt");
		// The emission events manager differs from the global one when
		// isWritingEmissionsEvents=false — register on the right channel.
		this.emissionModule.getEmissionEventsManager().addHandler(this);

		// MATSim 2026 quirk: with isWritingEmissionsEvents=false, EmissionModule
		// constructs Warm/ColdEmissionHandler with its PRIVATE events manager and
		// the handlers self-register THERE — so they never receive the mobsim
		// link events and no emissions are computed at all (verified empirically:
		// smoke test #2 produced zero emission warnings and zero totals).
		// Fix: re-register the two contrib handlers on the GLOBAL events manager.
		// They keep emitting emission events into the private manager, where this
		// handler listens. Reflection is version-pinned to MATSim 2026 field names
		// (warmEmissionHandler / coldEmissionHandler) — revisit on MATSim upgrade.
		if (this.emissionModule.getEmissionEventsManager() != this.globalEvents) {
			for (String fieldName : new String[]{"warmEmissionHandler", "coldEmissionHandler"}) {
				try {
					java.lang.reflect.Field f = EmissionModule.class.getDeclaredField(fieldName);
					f.setAccessible(true);
					this.globalEvents.addHandler(
							(org.matsim.core.events.handler.EventHandler) f.get(this.emissionModule));
				} catch (ReflectiveOperationException e) {
					throw new RuntimeException(
							"Could not re-register EmissionModule." + fieldName
									+ " on the global EventsManager — check MATSim version", e);
				}
			}
		}
	}

	@Override
	public void notifyIterationStarts(IterationStartsEvent event) {
		warmBackground = warmVan = warmTransit = 0.0;
		coldBackground = coldVan = coldTransit = 0.0;
		warmBackgroundCorridor = coldBackgroundCorridor = 0.0;
		warmBackgroundBusstop = coldBackgroundBusstop = 0.0;
		warmBackgroundCorridorExstop = coldBackgroundCorridorExstop = 0.0;
	}

	private static int classify(String vehicleId) {
		if (vehicleId.startsWith(VAN_PREFIX)) return 1;
		for (String p : TRANSIT_PREFIXES) {
			if (vehicleId.startsWith(p)) return 2;
		}
		return 0;
	}

	@Override
	public void handleEvent(WarmEmissionEvent event) {
		Double g = event.getWarmEmissions().get(Pollutant.CO2_TOTAL);
		if (g == null) return;
		switch (classify(event.getVehicleId().toString())) {
			case 1 -> warmVan += g;
			case 2 -> warmTransit += g;
			default -> {
				warmBackground += g;
				String link = event.getLinkId().toString();
				boolean inCorridor = corridorLinks.contains(link);
				boolean inBusstop = busStopLinks.contains(link);
				if (inCorridor) warmBackgroundCorridor += g;
				if (inBusstop) warmBackgroundBusstop += g;
				if (inCorridor && !inBusstop) warmBackgroundCorridorExstop += g;
			}
		}
	}

	@Override
	public void handleEvent(ColdEmissionEvent event) {
		Double g = event.getColdEmissions().get(Pollutant.CO2_TOTAL);
		if (g == null) return;
		switch (classify(event.getVehicleId().toString())) {
			case 1 -> coldVan += g;
			case 2 -> coldTransit += g;
			default -> {
				coldBackground += g;
				String link = event.getLinkId().toString();
				boolean inCorridor = corridorLinks.contains(link);
				boolean inBusstop = busStopLinks.contains(link);
				if (inCorridor) coldBackgroundCorridor += g;
				if (inBusstop) coldBackgroundBusstop += g;
				if (inCorridor && !inBusstop) coldBackgroundCorridorExstop += g;
			}
		}
	}

	@Override
	public void notifyIterationEnds(IterationEndsEvent event) {
		// grams -> kg; overwrite each iteration so the file holds the last one
		String path = controlerIO.getOutputFilename("co2_totals.csv");
		try (PrintWriter w = new PrintWriter(Files.newBufferedWriter(
				Paths.get(path), StandardCharsets.UTF_8))) {
			w.println("iteration,vehicle_class,warm_co2_kg,cold_co2_kg,total_co2_kg");
			writeRow(w, event.getIteration(), "background", warmBackground, coldBackground);
			writeRow(w, event.getIteration(), "van", warmVan, coldVan);
			writeRow(w, event.getIteration(), "transit", warmTransit, coldTransit);
			if (!corridorLinks.isEmpty()) {
				writeRow(w, event.getIteration(), "background_corridor",
						warmBackgroundCorridor, coldBackgroundCorridor);
			}
			if (!busStopLinks.isEmpty()) {
				writeRow(w, event.getIteration(), "background_busstop",
						warmBackgroundBusstop, coldBackgroundBusstop);
				if (!corridorLinks.isEmpty()) {
					writeRow(w, event.getIteration(), "background_corridor_exstop",
							warmBackgroundCorridorExstop, coldBackgroundCorridorExstop);
				}
			}
		} catch (IOException e) {
			throw new RuntimeException("Could not write " + path, e);
		}
	}

	private static void writeRow(PrintWriter w, int iteration, String cls, double warmG, double coldG) {
		w.printf(java.util.Locale.US, "%d,%s,%.6f,%.6f,%.6f%n",
				iteration, cls, warmG / 1000.0, coldG / 1000.0, (warmG + coldG) / 1000.0);
	}
}
