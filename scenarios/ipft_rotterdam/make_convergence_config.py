"""Config diagnostico: 10 iterazioni del caso alpha050 peak per verificare che
il gridlock dell'iterazione 0 si normalizzi con l'equilibrio (re-routing)."""
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
GEN = ROOT / "scenarios" / "ipft_rotterdam" / "generated"

src = (GEN / "config_alpha050_peak_seed9876.xml").read_text(encoding="utf-8")
src = src.replace('<param name="lastIteration" value="5" />',
                  '<param name="lastIteration" value="10" />')
out_dir = str(ROOT / "output" / "ipft_rotterdam_convergence_check")
src = re.sub(r'(<param name="outputDirectory" value=")[^"]+(" />)',
             lambda m: m.group(1) + out_dir + m.group(2), src)
(GEN / "config_CONV_alpha050_peak.xml").write_text(src, encoding="utf-8")
print("convergence config written (10 iterations)")
