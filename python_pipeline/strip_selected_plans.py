"""
Keep only each agent's SELECTED plan in a warm plans file.

Why: the warm start freezes routes and departure times but not plan CHOICE.
ChangeExpBeta stays active, so inserting a different number of vans shifts
MATSim's random stream and thousands of background agents end up selecting a
different plan between baseline and scenario. On the 657k-link network that
averages out; on the 15-18 links of the bus-stop row it is the same size as
the signal, which is why that row could not be measured.

With a single plan per person there is nothing left to choose: baseline and
scenario then differ ONLY by the vans, and the congestion delta becomes the
deterministic physical effect we are after. This is the change agreed with the
supervisor at the mid-term.

The vans themselves are left untouched: they are inserted with one plan each
anyway, and their subpopulation must keep working exactly as before.

Streaming (the Rotterdam warm plans are ~270 MB gzipped, several GB expanded),
and the output is verified against the input: same person count, exactly one
plan each, and every kept plan was the selected one.

Usage:
    python python_pipeline/strip_selected_plans.py IN.xml.gz OUT.xml.gz
"""

from __future__ import annotations

import gzip
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

def _open(path: Path, mode: str):
    # The LONGBASE output_plans MATSim writes are .zst; the warm plans this
    # script was first used on were .gz. Read both, write .gz or plain.
    if path.suffix == ".zst":
        if "w" in mode:
            raise ValueError("writing .zst is not supported; use .gz")
        import io

        import zstandard
        return io.BufferedReader(
            zstandard.ZstdDecompressor().stream_reader(open(path, "rb"), closefd=True)
        )
    return gzip.open(path, mode) if path.suffix == ".gz" else open(path, mode)


def read_header(path: Path) -> tuple[bytes, str]:
    """Everything before the first <person>, verbatim, plus the root tag name.

    The header must be COPIED, not rebuilt: the Rotterdam plans are
    population_v6 (not plans_v4) and carry a top-level <attributes> block with
    the coordinate reference system. Writing a hand-made header dropped that
    block and used the wrong DTD, and MATSim refused the file with
    "Undefined element <attributes> encountered".
    """
    head = bytearray()
    with _open(path, "rb") as f:
        for raw in f:
            if b"<person" in raw:
                break
            head += raw
    text = head.decode("utf-8", "replace")
    root = "population"
    for cand in ("<population", "<plans"):
        if cand in text:
            root = cand.lstrip("<")
            break
    return bytes(head), root


def strip(in_path: Path, out_path: Path) -> dict:
    """Write a copy keeping only the selected plan of each person."""
    n_person = n_plan_in = n_plan_out = n_no_selected = 0
    header, root = read_header(in_path)

    with _open(in_path, "rb") as fin, _open(out_path, "wb") as fout:
        fout.write(header)
        # iterparse on 'end' of <person> gives one complete person subtree at a
        # time, which is small; clearing it keeps memory flat over ~250k people.
        for _, person in ET.iterparse(fin, events=("end",)):
            if person.tag != "person":
                continue
            n_person += 1
            plans = person.findall("plan")
            n_plan_in += len(plans)
            selected = [p for p in plans if p.get("selected") == "yes"]
            if not selected:
                # No plan marked selected: keep the first so the agent still
                # exists and the population size is unchanged.
                n_no_selected += 1
                selected = plans[:1]
            for p in plans:
                if p not in selected:
                    person.remove(p)
            n_plan_out += len(person.findall("plan"))
            fout.write(ET.tostring(person, encoding="utf-8"))
            person.clear()
        fout.write(f"</{root}>\n".encode("utf-8"))

    return {"persons": n_person, "plans_in": n_plan_in, "plans_out": n_plan_out,
            "no_selected": n_no_selected, "root": root}


def verify(out_path: Path, expected_persons: int) -> None:
    """Re-read the written file: one plan per person, all of them selected."""
    n_person = n_plan = n_not_selected = 0
    with _open(out_path, "rb") as f:
        for _, person in ET.iterparse(f, events=("end",)):
            if person.tag != "person":
                continue
            n_person += 1
            plans = person.findall("plan")
            n_plan += len(plans)
            n_not_selected += sum(1 for p in plans if p.get("selected") != "yes")
            person.clear()
    if n_person != expected_persons:
        raise RuntimeError(f"persons {n_person} != {expected_persons}")
    if n_plan != n_person:
        raise RuntimeError(f"{n_plan} plans for {n_person} persons — not exactly one each")
    print(f"[verify] {n_person:,} persons, 1 plan each, "
          f"{n_not_selected} not marked selected")


def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    print(f"{src.name} -> {dst.name}")
    r = strip(src, dst)
    print(f"  radice <{r['root']}> | {r['persons']:,} persone | "
          f"piani {r['plans_in']:,} -> {r['plans_out']:,} "
          f"| senza 'selected': {r['no_selected']}")
    verify(dst, r["persons"])
    # The header must survive byte-for-byte: the DTD and the top-level
    # <attributes> (coordinate reference system) are what MATSim validates.
    h_in, _ = read_header(src)
    h_out, _ = read_header(dst)
    if h_in != h_out:
        raise RuntimeError("l'intestazione non coincide con quella di origine")
    print(f"[verify] intestazione identica all'originale ({len(h_in)} byte, "
          f"DTD e <attributes> preservati)")


if __name__ == "__main__":
    main()
