"""Valida un config generato e i plans con i van per lo scenario Rotterdam."""
import gzip
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent  # matsim-example-project-master
GEN = ROOT / "scenarios" / "ipft_rotterdam" / "generated"

cfg = (GEN / "config_alpha050_peak_seed9876.xml").read_text(encoding="utf-8")
patterns = [
    "randomSeed", "lastIteration", "outputDirectory", "inputPlansFile",
    "inputNetworkFile", "transitScheduleFile", 'name="vehiclesFile"',
    "flowCapacityFactor", "storageCapacityFactor", "vehiclesSource",
    "averageFleet", "freight_hub", "freight_delivery", "mainMode", "networkModes",
]
for pat in patterns:
    for h in re.findall(r"<param[^>]*" + re.escape(pat) + r"[^>]*>", cfg)[:3]:
        print(h.strip())
print("---")
print("strategysettings count:", cfg.count("strategysettings"))
m = re.search(
    r'<parameterset type="strategysettings">\s*'
    r'<param name="subpopulation" value="freight"[^>]*>\s*'
    r'<param name="strategyName" value="ChangeExpBeta"[^>]*>', cfg)
print("freight strategy block present:", bool(m))
print("activityParams count:", cfg.count('"activityParams"'))
print("freight_hub activityParams:", 'value="freight_hub"' in cfg)

print("--- plans ---")
with gzip.open(GEN / "plans_alpha050_peak.xml.gz", "rt", encoding="utf-8") as f:
    content = f.read()
vans = re.findall(r'<person id="backup_van_\d+">', content)
print("vans inserted:", len(vans))
deps = re.findall(
    r'type="freight_hub" link="(\d+)" x="([\d.]+)" y="([\d.]+)" end_time="([\d:]+)"',
    content)
print("first van:", deps[0] if deps else None)
print("last van :", deps[-1] if deps else None)
tail = content[content.find("backup_van_0000"):]
print("van leg modes:", set(re.findall(r'<leg mode="(\w+)">', tail)))
print("terminal links ok:", content.count('type="freight_delivery" link="121963"'))
print("subpop freight count:", tail.count(">freight</attribute>"))
