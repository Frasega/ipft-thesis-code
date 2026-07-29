"""Run lungo di convergenza: alpha=1 (ZERO van = pura popolazione ActivitySim),
80 iterazioni, output su D:. Doppio uso:
  (a) curva di convergenza (a quante iterazioni si appiattisce car_travel?)
  (b) piani equilibrati dello sfondo -> base warm-start per TUTTI gli scenari,
      indipendente dal design dei van (che vengono aggiunti dopo).
"""
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
GEN = ROOT / "scenarios" / "ipft_rotterdam" / "generated"

src = (GEN / "config_alpha100_peak_seed4711.xml").read_text(encoding="utf-8")
src = src.replace('<param name="lastIteration" value="5" />',
                  '<param name="lastIteration" value="80" />')
src = re.sub(r'(<param name="outputDirectory" value=")[^"]+(" />)',
             lambda m: m.group(1) + r"D:\TesiOutputs\ipft_rotterdam_longbase" + m.group(2), src)
(GEN / "config_LONGBASE_peak_seed4711.xml").write_text(src, encoding="utf-8")
for line in src.splitlines():
    if "lastIteration" in line or "outputDirectory" in line or "inputPlansFile" in line:
        print(line.strip())
