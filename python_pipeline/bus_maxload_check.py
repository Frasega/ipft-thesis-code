"""Max simultaneous passengers on the line-44 H->B buses (LONGBASE), scaled x10.
Settles: can the model exceed 80 passengers? does the weight constraint ever bind?

COUNTS PersonEntersPtVehicle, NOT PersonEntersVehicle. MATSim 2026 writes
passenger boardings as PersonEntersPtVehicle; the plain PersonEntersVehicle on
a transit vehicle is the DRIVER, one per trip. Counting the latter made this
script report a maximum real load of 10 when the true figure is 60 — 197 events
were simply the 197 daily trips. The conclusion never changed (the 1,000 kg
cargo allowance binds, never the gross weight), but the number did.
"""
import collections
import xml.etree.ElementTree as ET
import zstandard as zstd

BASE = "D:/TesiOutputs/ipft_rotterdam_longbase"
EVENTS = f"{BASE}/MRDH_10pct.output_events.xml.zst"
SC = "scenarios/ipft_rotterdam"
SR = 0.10

bus_ids = {l.strip() for l in open(f"{SC}/line44_hb_vehicle_ids.txt") if l.strip()}
cur = collections.defaultdict(int)
mx = collections.defaultdict(int)
n_driver = 0
with open(EVENTS, "rb") as f:
    for _, e in ET.iterparse(zstd.ZstdDecompressor().stream_reader(f), events=["end"]):
        if e.tag != "event":
            e.clear(); continue
        ty = e.get("type")
        if ty == "PersonEntersPtVehicle":
            v = e.get("vehicle")
            if v in bus_ids:
                cur[v] += 1
                if cur[v] > mx[v]:
                    mx[v] = cur[v]
        elif ty == "PersonLeavesPtVehicle":
            v = e.get("vehicle")
            if v in bus_ids:
                cur[v] = max(0, cur[v] - 1)
        elif ty == "PersonEntersVehicle":
            # the driver — counted separately so the two can never be confused again
            if e.get("vehicle") in bus_ids:
                n_driver += 1
        e.clear()
print(f"drivers seen (PersonEntersVehicle, NOT counted as passengers): {n_driver}")

loads_sim = sorted(mx.values(), reverse=True)
n_with = len(loads_sim)
print(f"line-44 H->B buses with >=1 boarding: {n_with} of {len(bus_ids)}")
if loads_sim:
    print(f"MAX simultaneous load (sim): {loads_sim[0]}  -> real x10 = {loads_sim[0]/SR:.0f}")
    print(f"top 5 sim loads: {loads_sim[:5]}  -> real: {[int(x/SR) for x in loads_sim[:5]]}")
    import statistics
    print(f"median sim load: {statistics.median(loads_sim)}  -> real {statistics.median(loads_sim)/SR:.0f}")
    # binding threshold: real load that drops GVW residual below the 1000 kg bay
    # 18000 - 10882 - (pax+1)*75 < 1000  ->  pax > 80.6
    over = sum(1 for x in loads_sim if x/SR > 80)
    print(f"\ntrips with real load > 80 (where weight would START to bind): {over}")
    print(f"max real load {loads_sim[0]/SR:.0f} vs binding threshold 81 -> "
          f"{'BINDS' if loads_sim[0]/SR>80 else 'never binds'}")
print("\nDONE")
