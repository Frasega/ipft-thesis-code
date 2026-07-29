"""
Toy warm-start, step 1 — LONGBASE equilibration configs (decision #5, HANDOFF §14b/§9).

Builds two equilibration configs from the existing alpha=100 toy configs (alpha=1 =
ZERO backup vans = the pure background population + transit). Bumps the iteration count
so the background reaches mode/route equilibrium, and redirects output to D:. The
resulting output_plans become the FROZEN baseline that every warm-started scenario
starts from, so Term A noise (from-scratch seed variation) cancels.

Two longbases because peak and off-peak are different populations (off-peak = 50%
background subsample).

Run from project root:  python python_pipeline/make_toy_longbase.py
Then run the two configs with scenario_runner / the MATSim JAR.
"""
import re
from pathlib import Path

GEN = Path(__file__).parent.parent / "scenarios" / "ipft_toy" / "generated"
LONGBASE_ITERS = 80

for congestion in ("peak", "offpeak"):
    src_path = GEN / f"config_alpha100_{congestion}_seed4711.xml"
    src = src_path.read_text(encoding="utf-8")

    # Bump iterations (toy default 10) -> 80 for equilibration.
    src = re.sub(r'(<param name="lastIteration" value=")\d+(" />)',
                 rf'\g<1>{LONGBASE_ITERS}\g<2>', src)

    # Redirect output to a dedicated longbase dir on D:.
    out = f"D:\\TesiOutputs\\ipft_toy_longbase_{congestion}"
    src = re.sub(r'(<param name="outputDirectory" value=")[^"]+(" />)',
                 lambda m: m.group(1) + out + m.group(2), src)

    dst = GEN / f"config_TOY_LONGBASE_{congestion}.xml"
    dst.write_text(src, encoding="utf-8")
    print(f"{dst.name}")
    for line in src.splitlines():
        if any(k in line for k in ("lastIteration", "outputDirectory", "inputPlansFile")):
            print("   ", line.strip())
