"""Controlla se i link della rete hanno attributi OSM (type/highway) per OsmHbefaMapping."""
import gzip
import re
from pathlib import Path

HERE = Path(__file__).parent
p = HERE / "networkWithRideAndBike.xml.gz"

buf = []
n_links = 0
with gzip.open(p, "rt", encoding="utf-8") as f:
    for line in f:
        if "<link " in line:
            n_links += 1
            if n_links <= 3:
                buf.append(line)
        if n_links and n_links <= 3 and ("<attribute" in line or "</link>" in line or "attributes" in line):
            buf.append(line)
        if n_links > 3 and len(buf) > 0:
            break

print("".join(buf))

# cerca anche se nel file compaiono attributi 'type' o 'osm'
with gzip.open(p, "rt", encoding="utf-8") as f:
    chunk = f.read(3_000_000)
attrs = set(re.findall(r'<attribute name="([^"]+)"', chunk))
print("attribute names found in first 3MB:", attrs)
# il tag <link> ha un attributo XML 'type'?
m = re.findall(r"<link [^>]+>", chunk)[:3]
for x in m:
    print(x)
