/* *********************************************************************** *
 * project: org.matsim.*												   *
 *                                                                         *
 * *********************************************************************** *
 *                                                                         *
 * copyright       : (C) 2008 by the members listed in the COPYING,        *
 *                   LICENSE and WARRANTY file.                            *
 * email           : info at matsim dot org                                *
 *                                                                         *
 * *********************************************************************** *
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *   See also COPYING, LICENSE and WARRANTY file                           *
 *                                                                         *
 * *********************************************************************** */
package org.matsim.project;

import org.apache.logging.log4j.core.tools.picocli.CommandLine;
import org.matsim.api.core.v01.Scenario;
import org.matsim.application.MATSimApplication;
import org.matsim.api.core.v01.network.Link;
import org.matsim.contrib.emissions.EmissionModule;
import org.matsim.contrib.emissions.EmissionUtils;
import org.matsim.contrib.emissions.utils.EmissionsConfigGroup;
import org.matsim.core.config.Config;
import org.matsim.core.config.ConfigUtils;
import org.matsim.core.controler.AbstractModule;
import org.matsim.core.controler.Controler;
import org.matsim.core.controler.OutputDirectoryHierarchy.OverwriteFileSetting;
import org.matsim.core.network.algorithms.MultimodalNetworkCleaner;
import java.util.Set;
import org.matsim.vehicles.EngineInformation;
import org.matsim.vehicles.VehicleType;

/**
 * @author nagel
 *
 */
@CommandLine.Command( header = ":: MyScenario ::", version = "1.0")
public class MatsimModelImplementation extends MATSimApplication {

	public MatsimModelImplementation() {
		super();
	}

	public static void main(String[] args) {
		MATSimApplication.runWithDefaults(MatsimModelImplementation.class, args);
	}

	@Override
	protected Config prepareConfig(Config config) {

		config.controller().setOverwriteFileSetting( OverwriteFileSetting.deleteDirectoryIfExists );

		// Allow storageCapacityFactor != flowCapacityFactor (e.g. the flow^0.75
		// rule for the 10% Rotterdam sample). MATSim 2026's consistency check
		// rejects the difference unless this tolerance is set, and the parameter
		// is NOT settable from XML (not a reflective config param). Harmless when
		// storage == flow (toy).
		config.global().setRelativeToleranceForSampleSizeFactors(1.0);

		// ---

		return config;
	}

	@Override
	protected void prepareScenario(Scenario scenario) {
		new MultimodalNetworkCleaner(scenario.getNetwork()).run(Set.of("car", "ride", "bike", "ebike"));

		EmissionsConfigGroup emCfg = ConfigUtils.addOrGetModule(
				scenario.getConfig(), EmissionsConfigGroup.class);
		if (emCfg.getAverageWarmEmissionFactorsFile() == null) return;

		// Set HBEFA road type on all network links.
		// DELIBERATE for BOTH toy and Rotterdam (decided 2026-06-10): the sample
		// factor tables (sample_41_EFA_*_2020average.csv) contain ONLY the
		// URB/Local/50 traffic situations (Freeflow + St+Go). OsmHbefaMapping
		// would emit other road types (URB/MW-City/80, URB/Trunk-City/70, ...)
		// whose factors are missing from the table and crash the lookup.
		// Congestion still differentiates emissions: the average-table lookup
		// interpolates between Freeflow and St+Go based on the simulated speed.
		// Thesis limitation: emission factors are road-type-blind.
		for (Link link : scenario.getNetwork().getLinks().values()) {
			EmissionUtils.setHbefaRoadType(link, "URB/Local/50");
		}

		// Ensure all vehicle types have HBEFA attributes.
		// Road vehicles fall back to "pass. car": the sample factor table only has
		// the full pollutant set for "pass. car" and "HGV" (urban bus / LCV rows
		// are CO2-only and break the lookup). Van HBEFA values are discarded in
		// post-processing anyway (Term B uses the longitudinal dynamics formula).
		for (VehicleType vt : scenario.getVehicles().getVehicleTypes().values()) {
			setHbefaPassengerCarIfMissing(vt);
		}
		// Transit vehicles -> NON_HBEFA_VEHICLE: no emission events generated.
		// Their HBEFA values were fake pass-car numbers, always discarded (Term C
		// uses the longitudinal dynamics formula). On Rotterdam, 20,868 transit
		// vehicles x all links would also bloat the emission-event volume.
		for (VehicleType vt : scenario.getTransitVehicles().getVehicleTypes().values()) {
			vt.getEngineInformation().getAttributes()
					.putAttribute("HbefaVehicleCategory", "NON_HBEFA_VEHICLE");
		}
	}

	private static void setHbefaPassengerCarIfMissing(VehicleType vt) {
		EngineInformation ei = vt.getEngineInformation();
		if (ei.getAttributes().getAttribute("HbefaVehicleCategory") == null) {
			ei.getAttributes().putAttribute("HbefaVehicleCategory", "pass. car");
			ei.getAttributes().putAttribute("HbefaTechnology",       "average");
			ei.getAttributes().putAttribute("HbefaSizeClass",        "average");
			ei.getAttributes().putAttribute("HbefaEmissionsConcept", "average");
		}
	}

	@Override
	protected void prepareControler(Controler controler) {
		// Add EmissionModule only when HBEFA warm factors are configured.
		// This lets the same JAR handle both plain and HBEFA-enabled runs.
		EmissionsConfigGroup emCfg = ConfigUtils.addOrGetModule(
				controler.getConfig(), EmissionsConfigGroup.class);
		if (emCfg.getAverageWarmEmissionFactorsFile() != null) {
			controler.addOverridingModule(new AbstractModule() {
				@Override
				public void install() {
					bind(EmissionModule.class).asEagerSingleton();
					// Aggregates CO2 per vehicle class into co2_totals.csv.
					// Works whether or not emission events are written to file
					// (isWritingEmissionsEvents=false on Rotterdam keeps the
					// events file small; Term A reads the CSV instead).
					bind(Co2TotalsHandler.class).asEagerSingleton();
					addControlerListenerBinding().to(Co2TotalsHandler.class);
				}
			});
		}
	}
}
