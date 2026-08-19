# Figure for thesis section 4.5.2: SORT and WLTC anchor cycles + one
# reconstructed link profile. Uses the REAL traces from the pipeline modules.
import sys

PIPELINE = r"c:\Users\frare\OneDrive\Desktop\Tesi documents\matsim-example-project-master\python_pipeline"
FIG_DIR = r"c:\Users\frare\OneDrive\Desktop\Tesi documents\Tesi Regazzoni\figures"
SCRATCH = r"C:\Users\frare\AppData\Local\Temp\claude\c--Users-frare-OneDrive-Desktop-Tesi-documents\3d294299-6900-4d67-be54-ccd76ad34a5f\scratchpad"
sys.path.insert(0, PIPELINE)

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import sort_cycles as sc
import van_cycles as vc

# ── palette (validated: dataviz skill, CVD dE 47.2) ────────────────────────
C_SLOW, C_MED, C_FAST = "#2a78d6", "#1baf7a", "#eda100"
INK, INK2, MUTED, GRID, BASE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"

plt.rcParams.update({
    "font.size": 8, "font.family": "sans-serif",
    "axes.edgecolor": BASE, "axes.linewidth": 0.6,
    "axes.labelcolor": INK2, "axes.titlecolor": INK,
    "xtick.color": MUTED, "ytick.color": MUTED,
    "xtick.labelsize": 7, "ytick.labelsize": 7,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.5,
    "legend.frameon": False, "legend.fontsize": 7,
})

def style(ax):
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_axisbelow(True)

# ── data ────────────────────────────────────────────────────────────────────
sort_official = [a for a in sc.ANCHORS if a.name in ("SORT1", "SORT2", "SORT3")]
wltc = {a.name.replace("WLTC_", ""): a for a in vc.ANCHORS}   # low/medium/high

# reconstruction at 30 km/h with provenance, replicating vc._recombine (seed 42)
TARGET_KMH = 30.0
target_ms = TARGET_KMH / 3.6
low, high = vc._bracket(target_ms)
rng = np.random.default_rng(42)
frags, sources = [], []
running_sum, running_n = 0.0, 0
best_res, best_len = float("inf"), 0
for _ in range(vc.N_MAX_DRAWS):
    cur = running_sum / running_n if running_n else 0.0
    src_is_low = cur >= target_ms
    pool = low.microtrips if src_is_low else high.microtrips
    trip = pool[rng.integers(len(pool))]
    frags.append(trip); sources.append(src_is_low)
    running_sum += float(np.sum(trip)); running_n += len(trip)
    res = abs(running_sum / running_n - target_ms)
    if res < best_res:
        best_res, best_len = res, len(frags)
    if res < vc.TOLERANCE_MS:
        break
else:
    frags, sources = frags[:best_len], sources[:best_len]

profile = np.concatenate(frags) * 3.6
running_mean = np.cumsum(profile) / np.arange(1, len(profile) + 1)

# ── figure (manual layout: predictable spacing) ─────────────────────────────
fig = plt.figure(figsize=(6.1, 6.9))
gs = fig.add_gridspec(3, 3, height_ratios=[1.0, 1.0, 1.2],
                      hspace=0.62, wspace=0.13,
                      left=0.085, right=0.985, top=0.94, bottom=0.065)

# (a) bus anchors: three official SORT cycles on a shared time scale
t_max = max(len(a.full_cycle) for a in sort_official)
for i, (a, c) in enumerate(zip(sort_official, [C_SLOW, C_MED, C_FAST])):
    ax = fig.add_subplot(gs[0, i])
    v = a.full_cycle * 3.6
    ax.plot(np.arange(len(v)), v, color=c, lw=1.3)
    ax.set_title(f"SORT {a.name[4]} · {a.target_vmean_kmh:.1f} km/h",
                 fontsize=7.5, loc="left", pad=3)
    ax.set_ylim(0, 70); ax.set_xlim(0, t_max)
    style(ax)
    if i == 0:
        ax.set_ylabel("speed [km/h]")
    else:
        ax.set_yticklabels([])
    if i == 1:
        ax.set_xlabel("time [s]", labelpad=1)

# (b) van anchors: the three WLTC urban phases, plotted end to end
axB = fig.add_subplot(gs[1, :])
t0 = 0
for name, c, lab in (("low", C_SLOW, "Low"), ("medium", C_MED, "Medium"),
                     ("high", C_FAST, "High")):
    a = wltc[name]
    v = a.full_cycle * 3.6
    axB.plot(np.arange(t0, t0 + len(v)), v, color=c, lw=1.0)
    axB.text(t0 + len(v) / 2, 108, f"{lab} · {a.vmean_ms*3.6:.1f} km/h",
             ha="center", va="top", fontsize=7.5, color=INK2)
    t0 += len(v)
    if name != "high":
        axB.axvline(t0, color=BASE, lw=0.6)
axB.set_ylim(0, 112); axB.set_xlim(0, t0)
axB.set_ylabel("speed [km/h]"); axB.set_xlabel("time [s]", labelpad=1)
style(axB)
axB.text(0, 1.16, "(b)  Van anchors — WLTC phases (recorded from real driving)",
         transform=axB.transAxes, fontsize=8.5, fontweight="bold", color=INK)

# (c) reconstruction example: fragments coloured by source anchor
axC = fig.add_subplot(gs[2, :])
t0 = 0
handles = {}
for trip, src_is_low in zip(frags, sources):
    v = trip * 3.6
    c = C_SLOW if src_is_low else C_MED
    key = "low" if src_is_low else "med"
    ln, = axC.plot(np.arange(t0, t0 + len(v)), v, color=c, lw=1.2)
    handles.setdefault(key, ln)
    t0 += len(v)
rm, = axC.plot(np.arange(len(running_mean)), running_mean, color=INK2, lw=0.9,
               ls=(0, (4, 2)))
tg = axC.axhline(TARGET_KMH, color=INK, lw=0.8, ls=":")
axC.set_ylim(0, 92); axC.set_xlim(0, len(profile))
axC.set_ylabel("speed [km/h]"); axC.set_xlabel("time [s]", labelpad=1)
style(axC)
axC.legend([handles["low"], handles["med"], rm, tg],
           [f"micro-trip from Low ({low.vmean_ms*3.6:.1f} km/h)",
            f"micro-trip from Medium ({high.vmean_ms*3.6:.1f} km/h)",
            "running mean of the assembled profile",
            "link mean speed (target, 30 km/h)"],
           loc="upper right", ncols=2, columnspacing=1.2, handlelength=1.6)
axC.text(0, 1.13, "(c)  Reconstructed van link — target mean 30 km/h",
         transform=axC.transAxes, fontsize=8.5, fontweight="bold", color=INK)

fig.text(0.085, 0.968, "(a)  Bus anchors — UITP SORT reference cycles",
         fontsize=8.5, fontweight="bold", color=INK)

fig.savefig(FIG_DIR + r"\cycles_reconstruction.pdf")
fig.savefig(SCRATCH + r"\cycles_reconstruction.png", dpi=200)
print(f"done: {len(frags)} fragments, achieved mean {profile.mean():.2f} km/h "
      f"(target {TARGET_KMH}), profile {len(profile)} s")
