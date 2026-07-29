"""
Regenerate the WLTC van anchor traces in cycle_data/ from the official EU JRC
`wltp` package (JRCSTU/wltp; UN/ECE GTR No. 15 / EU 2017/1151; cycle derived from
real-world driving, Tutuianu et al. 2015). See HANDOFF.md §14.10.

Writes cycle_data/wltc_{low,medium,high}.csv (1 Hz, columns t_s,v_kmh).
Phase means: Low 18.88, Medium 39.54, High 56.66 km/h. The Extra-High phase
(~92 km/h, motorway) is intentionally omitted — too fast for an urban van.

Run from the project root:  python python_pipeline/build_wltc_cycles.py
Requires:  pip install wltp
"""
import csv
import os

import numpy as np
from wltp.cycles import class3

# Full WLTC Class 3 trace (1801 s @ 1 Hz), km/h.
v = np.asarray(class3.class_data_b()["cycle"], dtype=float)

# Standard phase boundaries (UN/ECE GTR 15).
PARTS = {"low": (0, 589), "medium": (590, 1022), "high": (1023, 1477)}
outdir = os.path.join("python_pipeline", "cycle_data")
os.makedirs(outdir, exist_ok=True)

for name, (a, b) in PARTS.items():
    seg = v[a:b + 1]
    with open(os.path.join(outdir, f"wltc_{name}.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_s", "v_kmh"])
        for t, vv in enumerate(seg):
            w.writerow([t, f"{vv:.1f}"])
    print(f"wltc_{name}.csv  {len(seg)} s  mean={seg.mean():.2f} km/h")
print("saved to", outdir)
