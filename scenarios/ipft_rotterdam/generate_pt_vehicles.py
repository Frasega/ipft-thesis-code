"""Generate ptVehicleExtended.xml from vehicle IDs in ptSchedule36Hour.xml.gz.
Vehicle type specs are defaults — replace with Jingjun's real file when available.
"""
import gzip
import re

SCHEDULE = "ptSchedule36Hour.xml.gz"
OUTPUT = "ptVehicleExtended.xml"

# Default specs per mode: (seats, standing, length_m, max_speed_ms, pce)
TYPE_SPECS = {
    "bus":    (70,   30, 18.0, 13.889, 0.0),
    "tram":   (120,  80, 30.0, 13.889, 0.0),
    "rail":   (300, 100, 80.0, 27.778, 0.0),
    "subway": (200,  80, 60.0, 16.667, 0.0),
    "ferry":  (200, 100, 50.0,  5.556, 0.0),
}

with gzip.open(SCHEDULE, "rt", encoding="utf-8") as f:
    content = f.read()

vehicle_ids = sorted(set(re.findall(r'vehicleRefId="([^"]+)"', content)))

lines = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<vehicleDefinitions xmlns="http://www.matsim.org/files/dtd"',
    '    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"',
    '    xsi:schemaLocation="http://www.matsim.org/files/dtd http://www.matsim.org/files/dtd/vehicleDefinitions_v2.0.xsd">',
    "",
]

for mode, (seats, standing, length, speed, pce) in TYPE_SPECS.items():
    lines += [
        f'    <vehicleType id="default_{mode}">',
        f'        <capacity seats="{seats}" standingRoomInPersons="{standing}"/>',
        f'        <length meter="{length}"/>',
        f'        <width meter="2.5"/>',
        f'    </vehicleType>',
        "",
    ]

for vid in vehicle_ids:
    mode = vid.split("_")[-1]
    lines.append(f'    <vehicle id="{vid}" type="default_{mode}"/>')

lines += ["", "</vehicleDefinitions>"]

with open(OUTPUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

print(f"Written {OUTPUT} — {len(vehicle_ids)} vehicles, {len(TYPE_SPECS)} types")
