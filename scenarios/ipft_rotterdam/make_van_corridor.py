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
import argparse
import io
import re
import sys
from pathlib import Path

import zstandard

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent.parent / "python_pipeline"))
from scenario_presets import OUTPUT_ROOT, get_preset  # noqa: E402

# The output path and the default runs used to be literals: the file was always
# written to line 44's corridor_links.txt, so pointing this script at a second
# line's events OVERWROTE line 44's corridor without a word. Both now come from
# the preset (--scenario), and --runs-dir picks the baselines to read.

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
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("events", nargs="*",
                    help="explicit alpha=0 events files; default: every alpha000_* "
                         "run found in --runs-dir")
    ap.add_argument("--scenario", default="rotterdam",
                    choices=["rotterdam", "rotterdam_L87"],
                    help="which line's corridor file is written (default: line 44)")
    ap.add_argument("--runs-dir", default=None,
                    help="run tree holding the alpha=0 baselines "
                         "(default: <output root>/ipft_rotterdam<suffix>_runs)")
    ap.add_argument("--out", default=None,
                    help="default: the preset's corridor_links_file")
    args = ap.parse_args(argv)

    preset = get_preset(args.scenario)
    root = HERE.parent.parent
    out = Path(args.out) if args.out else root / preset.corridor_links_file

    events = list(args.events)
    if not events:
        runs = Path(args.runs_dir) if args.runs_dir else (
            OUTPUT_ROOT / f"ipft_rotterdam{preset.suffix}_runs")
        events = sorted(str(f) for d in sorted(runs.glob("alpha000_*"))
                        for f in d.glob("*output_events.xml.zst"))
        if not events:
            sys.exit(f"no alpha=0 events found under {runs} — pass them explicitly "
                     f"or use --runs-dir")

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
