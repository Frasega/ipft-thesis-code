"""Test A/B: identico al run di convergenza (10 iter, alpha050 peak seed9876)
ma con storageCapacityFactor = 0.178 (regola flow^0.75 per campioni al 10%,
Nagel et al.) invece di 0.100. Se il gridlock si scioglie, il colpevole e' lo
storage troppo basso, non (solo) la mancata convergenza."""
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
GEN = ROOT / "scenarios" / "ipft_rotterdam" / "generated"

src = (GEN / "config_CONV_alpha050_peak.xml").read_text(encoding="utf-8")
src = src.replace('<param name="storageCapacityFactor" value="0.100" />',
                  '<param name="storageCapacityFactor" value="0.178" />')
# MATSim 2026 consistency check rifiuta storage != flow senza una tolleranza
# esplicita nel global config group
src = src.replace('<param name="randomSeed" value="9876" />',
                  '<param name="randomSeed" value="9876" />\n'
                  '    <param name="relativeTolerance" value="1.0" />')
out_dir = str(ROOT / "output" / "ipft_rotterdam_storage_test")
src = re.sub(r'(<param name="outputDirectory" value=")[^"]+(" />)',
             lambda m: m.group(1) + out_dir + m.group(2), src)
(GEN / "config_STORAGE_alpha050_peak.xml").write_text(src, encoding="utf-8")
for line in src.splitlines():
    if "storageCapacityFactor" in line or "lastIteration" in line:
        print(line.strip())
