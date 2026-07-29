"""Generate corridor_links.txt = the links the VANS actually drive on.

WHY (2026-07-28): the previous corridor_links.txt (from extract_corridor_data.py)
was "line-44 bus route + 250 m car buffer". That is WRONG for S_cong: the vans are
car-routed and take the fastest car path, which diverges from the bus corridor.
Verified from events: the vans traverse 163 distinct links, of which only 76 fell
inside the old 1360-link buffer (87 were outside). S_cong and the corridor traffic
indicators must be summed over the links the vans really use.

WHAT THIS DOES: reads one or more alpha=0 baseline events files (where the vans are
on the road), collects every link crossed by a `backup_van_*` vehicle, and writes
their union to corridor_links.txt. The van route is deterministic on the frozen
network, so the set is stable across runs (checked and reported here).

USAGE:
    python make_van_corridor.py                      # default alpha=0 baselines on D:
    python make_van_corridor.py <events.zst> [...]   # explicit event files
"""
import io
import re
import sys
from pathlib import Path

import zstandard

HERE = Path(__file__).parent
OUT = HERE / "corridor_links.txt"

# Default: alpha=0 baselines (vans present). Heavy = most tours. Peak + off-peak +
# both seeds to confirm the van route does not depend on congestion or seed.
DEFAULT_RUNS = [
    "D:/TesiOutputs/ipft_rotterdam_runs/alpha000_peak_heavy_seed4711",
    "D:/TesiOutputs/ipft_rotterdam_runs/alpha000_offpeak_heavy_seed4711",
    "D:/TesiOutputs/ipft_rotterdam_runs/alpha000_peak_heavy_seed9876",
]

_RX_LV = re.compile(r'type="(?:entered link|left link)" .*?link="([^"]+)" vehicle="(backup_van_[^"]+)"')
_RX_VL = re.compile(r'type="(?:entered link|left link)" .*?vehicle="(backup_van_[^"]+)" link="([^"]+)"')


def van_links_in(events_path: str) -> set[str]:
    links: set[str] = set()
    dctx = zstandard.ZstdDecompressor()
    with io.TextIOWrapper(dctx.stream_reader(open(events_path, "rb")), encoding="utf-8") as r:
        for line in r:
            if "backup_van_" not in line:
                continue
            m = _RX_LV.search(line)
            if m:
                links.add(m.group(1))
            else:
                m = _RX_VL.search(line)
                if m:
                    links.add(m.group(2))
    return links


def main(argv: list[str]) -> None:
    if argv:
        events = argv
    else:
        events = [f"{d}/MRDH_10pct.output_events.xml.zst" for d in DEFAULT_RUNS]

    per_run = {}
    for ev in events:
        s = van_links_in(ev)
        per_run[ev] = s
        print(f"{Path(ev).parent.name}: {len(s)} van links")

    union = set().union(*per_run.values())
    inter = set.intersection(*per_run.values()) if per_run else set()
    print(f"\nunion: {len(union)} links | intersection: {len(inter)} "
          f"({'STABLE' if len(union) == len(inter) else 'VARIES across runs — inspect'})")

    OUT.write_text("\n".join(sorted(union, key=lambda x: (len(x), x))) + "\n", encoding="utf-8")
    print(f"wrote {len(union)} links -> {OUT.name}")


if __name__ == "__main__":
    main(sys.argv[1:])
