"""Verifica se le transitRoute dello schedule hanno network route (link refIds)
e se quei link esistono nella rete car-only."""
import gzip
import re
from pathlib import Path

HERE = Path(__file__).parent

with gzip.open(HERE / "ptSchedule36Hour.xml.gz", "rt", encoding="utf-8") as f:
    sched = f.read()

n_routes = sched.count("<transitRoute ")
n_netroutes = sched.count("<route>")
links_in_routes = re.findall(r'<link refId="([^"]+)"/>', sched)
print("transitRoutes:", n_routes)
print("<route> blocks:", n_netroutes)
print("link refs in routes:", len(links_in_routes), "unique:", len(set(links_in_routes)))

# transportMode delle route
modes = re.findall(r"<transportMode>([^<]+)</transportMode>", sched)
from collections import Counter

print("route transportModes:", Counter(modes))

# linea 44: trova transitLine con '44'
lines = re.findall(r'<transitLine id="([^"]+)"', sched)
l44 = [l for l in lines if "44" in l]
print("total lines:", len(lines))
print("lines containing '44':", l44[:20])

# i link delle route esistono nella rete?
sample = set(links_in_routes)
with gzip.open(HERE / "networkWithRideAndBike.xml.gz", "rt", encoding="utf-8") as f:
    net = f.read()
net_ids = set(re.findall(r'<link id="([^"]+)"', net))
missing = sample - net_ids
print("network links:", len(net_ids))
print("route links missing from network:", len(missing), "of", len(sample))

# verifica hub/terminal link IDs
for lid in ("174131", "121963"):
    print(f"link {lid} in network: {lid in net_ids}")

# modes dei link che le route usano
link_modes = dict(
    re.findall(r'<link id="([^"]+)"[^>]*modes="([^"]+)"', net)
)
used_modes = Counter(link_modes.get(l, "?MISSING?") for l in sample)
print("modes of links used by transit routes:", dict(used_modes))
