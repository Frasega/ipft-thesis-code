"""
Corridor-local congestion metrics for Term A support.

The city-wide CO2 delta (Term A) dilutes a one-corridor treatment over the
whole MRDH region: the signal lives on 163 links, the seed noise on 657k.
These metrics restrict the measurement to a link set and measure congestion
DIRECTLY via speeds, independently of the HBEFA translation:

  - background vehicle-hours on those links (the congestion quantity)
  - length-weighted mean background speed on those links

Both are computed per run from the v_mean DataFrame that parse_events already
produces, and differenced baseline − scenario with paired seeds.

The functions are link-set agnostic; run_pipeline calls them on three sets:

  corridor_links.txt        the 163 links the vans actually drive
  corridor minus bus stops  ROW 1 of Term A — the van relief   (expected +)
  bus_stop_links.txt        ROW 2 of Term A — buses held at the
                            stops with isBlocking=true          (expected −)

Rows 1 and 2 are disjoint by construction (make_bus_stop_links.py) and both use
the same baseline − scenario convention, so the bus cost comes out NEGATIVE on
its own. Never flip its sign by hand: Term A = row 1 + row 2, reported apart.

Row 1 + row 2 covers 178 links (160 + 18) — MORE than the 163-link corridor,
because 15 of the upstream links where the bus queue forms lie outside it. So
the split total is not directly comparable with the historical corridor-only
figure, and their difference is not the bus cost.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def load_corridor_links(path: str | Path) -> frozenset[str]:
    p = Path(path)
    return frozenset(line.strip() for line in p.read_text(encoding="utf-8").splitlines()
                     if line.strip())


def corridor_background_stats(
    vmean_df: pd.DataFrame,
    corridor_links: frozenset[str],
    link_lengths: dict | None = None,
) -> dict:
    """
    Background-traffic congestion stats restricted to the corridor.

    Returns
    -------
    {
      'vehicle_hours': float,        total background time spent on corridor links
      'mean_speed_ms': float,        time-weighted mean background speed
      'n_traversals': int,           background link traversals on the corridor
      'vkm': float | None,           vehicle-km (needs link_lengths)
    }
    """
    df = vmean_df[(vmean_df["vehicle_type"] == "background")
                  & (vmean_df["link_id"].isin(corridor_links))]
    if df.empty:
        return {"vehicle_hours": 0.0, "mean_speed_ms": None,
                "n_traversals": 0, "vkm": None}

    total_time_s = df["travel_time_s"].sum()
    # time-weighted mean speed = total distance / total time
    if link_lengths is not None:
        dist_m = df["link_id"].map(link_lengths).fillna(0.0).sum()
    else:
        dist_m = (df["v_mean_ms"] * df["travel_time_s"]).sum()
    return {
        "vehicle_hours": total_time_s / 3600.0,
        "mean_speed_ms": (dist_m / total_time_s) if total_time_s > 0 else None,
        "n_traversals": int(len(df)),
        "vkm": dist_m / 1000.0,
    }


def corridor_delta(baseline_stats: dict, scenario_stats: dict) -> dict:
    """Baseline − scenario deltas (positive vehicle_hours delta = vans were
    causing congestion that the IPFT shift removed)."""
    out = {}
    for k in ("vehicle_hours", "vkm", "n_traversals"):
        b, s = baseline_stats.get(k), scenario_stats.get(k)
        out[f"delta_{k}"] = (b - s) if (b is not None and s is not None) else None
    b, s = baseline_stats.get("mean_speed_ms"), scenario_stats.get("mean_speed_ms")
    out["speed_change_ms"] = (s - b) if (b is not None and s is not None) else None
    return out
