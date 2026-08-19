"""Campiona i primi ~200MB decompressi del file eventi smoke e conta i tipi."""
import re
from collections import Counter
from pathlib import Path

import zstandard as zstd

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
PATH = (_PROJECT_ROOT / "output" / "ipft_rotterdam_smoke" / "ITERS" / "it.0"
        / "MRDH_10pct.0.events.xml.zst")

LIMIT = 200_000_000  # bytes decompressi
types = Counter()
emission_vehicles = Counter()
van_events = 0
read = 0

with open(PATH, "rb") as f:
    dctx = zstd.ZstdDecompressor()
    with dctx.stream_reader(f) as reader:
        buf = b""
        while read < LIMIT:
            chunk = reader.read(8 * 1024 * 1024)
            if not chunk:
                break
            read += len(chunk)
            buf += chunk
            lines = buf.split(b"\n")
            buf = lines.pop()
            for line in lines:
                m = re.search(rb'type="([^"]+)"', line)
                if m:
                    t = m.group(1).decode()
                    types[t] += 1
                    if t in ("warmEmissionEvent", "coldEmissionEvent"):
                        vm = re.search(rb'vehicleId="([^"]+)"', line)
                        if vm:
                            vid = vm.group(1).decode()
                            if vid.startswith("veh_"):
                                emission_vehicles["transit"] += 1
                            elif vid.startswith("backup_van_"):
                                emission_vehicles["van"] += 1
                            else:
                                emission_vehicles["background"] += 1
                    if b"backup_van_" in line:
                        van_events += 1

print(f"decompressed bytes sampled: {read:,}")
print("\nevent types (top 15):")
for t, c in types.most_common(15):
    print(f"  {t}: {c:,}")
tot = sum(types.values())
em = types.get("warmEmissionEvent", 0) + types.get("coldEmissionEvent", 0)
print(f"\nemission events share: {em:,}/{tot:,} = {em/max(1,tot):.1%}")
print("emission events by vehicle class:", dict(emission_vehicles))
print("lines mentioning backup_van_:", van_events)
