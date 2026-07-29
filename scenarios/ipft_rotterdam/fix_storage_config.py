"""Rimuove il param relativeTolerance (non valido via XML) dal config storage-test."""
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
p = ROOT / "scenarios" / "ipft_rotterdam" / "generated" / "config_STORAGE_alpha050_peak.xml"
src = p.read_text(encoding="utf-8")
src = src.replace('    <param name="relativeTolerance" value="1.0" />\n', "")
p.write_text(src, encoding="utf-8")
print("relativeTolerance removed:", "relativeTolerance" not in src)
