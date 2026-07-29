"""Crea un config smoke-test a 0 iterazioni da quello alpha050 peak."""
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
GEN = ROOT / "scenarios" / "ipft_rotterdam" / "generated"

src = (GEN / "config_alpha050_peak_seed9876.xml").read_text(encoding="utf-8")
src = src.replace('<param name="lastIteration" value="5" />',
                  '<param name="lastIteration" value="0" />')
out_dir = str(ROOT / "output" / "ipft_rotterdam_smoke")
src = re.sub(r'(<param name="outputDirectory" value=")[^"]+(" />)',
             lambda m: m.group(1) + out_dir + m.group(2), src)
(GEN / "config_SMOKE_alpha050_peak.xml").write_text(src, encoding="utf-8")
print("smoke config written")
for line in src.splitlines():
    if "lastIteration" in line or "outputDirectory" in line:
        print(line.strip())
