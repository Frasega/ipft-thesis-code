"""Verifica nel JAR shaded: esistenza di NON_HBEFA_VEHICLE, del param
isWritingEmissionsEvents in EmissionsConfigGroup e relative stringhe."""
import zipfile
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
# The shaded JAR sits in the project root, or under target/ after `mvn package`.
_JARS = (sorted(_PROJECT_ROOT.glob("matsim-example-project-*.jar"))
         + sorted((_PROJECT_ROOT / "target").glob("matsim-example-project-*.jar")))
if not _JARS:
    raise SystemExit("no matsim-example-project-*.jar found; run `mvn package` first")
JAR = _JARS[0]

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
