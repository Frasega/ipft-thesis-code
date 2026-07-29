"""Verifica nel JAR shaded: esistenza di NON_HBEFA_VEHICLE, del param
isWritingEmissionsEvents in EmissionsConfigGroup e relative stringhe."""
import zipfile
from pathlib import Path

JAR = Path(r"c:\Users\frare\OneDrive\Desktop\Tesi documents\matsim-example-project-master"
           r"\matsim-example-project-0.0.1-SNAPSHOT.jar")

targets = {
    "org/matsim/contrib/emissions/HbefaVehicleCategory.class":
        [b"NON_HBEFA_VEHICLE"],
    "org/matsim/contrib/emissions/utils/EmissionsConfigGroup.class":
        [b"isWritingEmissionsEvents", b"handlesHighAverageSpeeds",
         b"nonScenarioVehicles", b"emissionsComputationMethod"],
    "org/matsim/contrib/emissions/WarmEmissionAnalysisModule.class":
        [b"NON_HBEFA_VEHICLE"],
    "org/matsim/contrib/emissions/WarmEmissionHandler.class":
        [b"NON_HBEFA_VEHICLE"],
    "org/matsim/contrib/emissions/EmissionModule.class":
        [b"WritingEmissionsEvents", b"emission"],
}

with zipfile.ZipFile(JAR) as z:
    names = set(z.namelist())
    for cls, needles in targets.items():
        if cls not in names:
            print(f"MISSING CLASS: {cls}")
            continue
        data = z.read(cls)
        found = {n.decode(): (n in data) for n in needles}
        print(f"{cls.split('/')[-1]}: {found}")

    # elenca le costanti enum di HbefaVehicleCategory
    data = z.read("org/matsim/contrib/emissions/HbefaVehicleCategory.class")
    import re
    strings = re.findall(rb"[A-Z_]{4,40}", data)
    uniq = sorted({s.decode() for s in strings if b"_" in s})
    print("\nHbefaVehicleCategory enum-like strings:", uniq[:20])
