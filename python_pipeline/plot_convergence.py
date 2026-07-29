"""Convergence curve for the Patrick deck, from the LONGBASE 80-iteration run."""
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

RUN = Path(r"D:\TesiOutputs\ipft_rotterdam_longbase")
OUT = Path(r"c:\Users\frare\OneDrive\Desktop\Tesi documents\Tesi Regazzoni\figures\convergence_curve.png")
OUT.parent.mkdir(parents=True, exist_ok=True)

df = pd.read_csv(RUN / "MRDH_10pct.ph_modestats.csv", sep=";")
it = df["Iteration"].astype(int)
car = df["car_travel"].astype(float) / 1000.0  # thousands of car-hours

fig, ax = plt.subplots(figsize=(8, 4.6))
ax.plot(it, car, color="#1f4e79", lw=2, marker="o", ms=3)
ax.axvline(64, color="#999", ls="--", lw=1)
ax.text(64.5, car.max() * 0.85, "innovation off\n(it.64)", fontsize=8, color="#666")
eq = car[it >= 65].mean()
ax.axhline(eq, color="#c00", ls=":", lw=1)
ax.text(2, eq + 12, f"equilibrium ≈ {eq*1000:,.0f} car-hours (±0.17%)",
        fontsize=8, color="#c00")
ax.set_xlabel("MATSim iteration")
ax.set_ylabel("Total car travel [thousands of hours]")
ax.set_title("Rotterdam baseline convergence (80 iterations, no vans)")
ax.set_xlim(0, 80)
ax.set_ylim(0, car.max() * 1.05)
ax.grid(alpha=0.25)
fig.tight_layout()
fig.savefig(OUT, dpi=150)
print(f"saved {OUT}")
print(f"it.0={car.iloc[0]*1000:,.0f}  it.10={car[it==10].iloc[0]*1000:,.0f}  "
      f"equilibrium={eq*1000:,.0f}")
