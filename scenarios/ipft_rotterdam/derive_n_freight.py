"""Deriva N_FREIGHT_UNITS dal bacino d'utenza della linea 44.

Bacino = persone (nel campione 10%) la cui prima attivita' home e' entro
WALK_BUFFER_M da una fermata H->B della linea 99437.
N_real = (persone_bacino / SAMPLE_RATE) * PARCELS_PER_PERSON_DAY
N_sim  = N_real * SAMPLE_RATE   (van simulati, coerenti con flowCapacityFactor 0.1)
"""
import gzip
import math
import re
from pathlib import Path

HERE = Path(__file__).parent
WALK_BUFFER_M = 400.0
SAMPLE_RATE = 0.10
import sys as _sys
_sys.path.insert(0, str(Path(__file__).parent.parent.parent / "python_pipeline"))
from parameters import PARCELS_PER_PERSON_DAY  # noqa: E402  (was copied here)

# fermate H->B linea 99437 (da check_line44_stops.py)
HB_STOP_IDS = [
    "2522467.link:174131", "2685255.link:448306", "2685230.link:668963",
    "2669755.link:448173", "2659572.link:438699", "2669753.link:178358",
    "2669750.link:390546", "2522886.link:49628", "2610827.link:121963",
]

with gzip.open(HERE / "ptSchedule36Hour.xml.gz", "rt", encoding="utf-8") as f:
    sched = f.read()

stops = []
for sid in HB_STOP_IDS:
    m = re.search(r'<stopFacility id="' + re.escape(sid) + r'" x="([^"]+)" y="([^"]+)"', sched)
    stops.append((float(m.group(1)), float(m.group(2))))
print("stop coords ok:", len(stops))

n_total = 0
n_catchment = 0
with gzip.open(HERE / "planExternalProcessed_lowerCase.xml.gz", "rt", encoding="utf-8") as f:
    in_person = False
    found_home = False
    for line in f:
        if "<person " in line:
            in_person = True
            found_home = False
            n_total += 1
        elif in_person and not found_home and "<activity" in line and 'type="home' in line:
            m = re.search(r'x="([^"]+)" y="([^"]+)"', line)
            if m:
                found_home = True
                x, y = float(m.group(1)), float(m.group(2))
                for sx, sy in stops:
                    if math.hypot(x - sx, y - sy) <= WALK_BUFFER_M:
                        n_catchment += 1
                        break
        elif "</person>" in line:
            in_person = False

pop_real = n_catchment / SAMPLE_RATE
n_real = pop_real * PARCELS_PER_PERSON_DAY
n_sim = n_real * SAMPLE_RATE
print(f"persons total (sample): {n_total}")
print(f"persons with home within {WALK_BUFFER_M:.0f} m of a line-44 H->B stop (sample): {n_catchment}")
print(f"catchment population (real, /{SAMPLE_RATE}): {pop_real:.0f}")
print(f"N_FREIGHT_UNITS_REAL = pop_real * {PARCELS_PER_PERSON_DAY}: {n_real:.0f}")
print(f"N_VANS_SIM = N_real * {SAMPLE_RATE}: {n_sim:.0f}")
print(f"feasibility check @alpha=1 medium 10kg: {n_real*10/98:.0f} kg/trip (cap 1000 kg)")
