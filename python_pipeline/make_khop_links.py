"""
Build the k-hop neighbourhoods of the van corridor.

Patrick (email 2026-08-06, point 12): congestion propagates through a network,
so the measurement must not stop at the corridor and its direct neighbours —
indirect neighbours can still be affected (Ji, Luo & Geroliminis 2014). The
reply committed to reporting corridor, one hop, two hops, three hops and the
full network, each next to the seed-to-seed noise so it is visible where the
effect stops being resolvable.

A hop is defined the way the existing one-hop check defined it: a link belongs
to hop k if it shares a NODE with a link of hop k-1. Undirected on purpose —
this is about how far a disturbance reaches, not about queue direction (the
bus-stop row uses the directed upstream definition instead, and stays separate).

Each ring is written as its own file so the measurement can report the rings
apart, not just the cumulative sets:
    khop_ring1.txt ... khop_ringN.txt   the links ADDED at that hop
    khop_cum1.txt  ... khop_cumN.txt    corridor + everything up to that hop

Usage (from project root):
    python python_pipeline/make_khop_links.py --hops 3
"""

from __future__ import annotations

import argparse
import gzip
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from scenario_presets import get_preset


def load_topology(network_path: Path):
    """{link: (from, to)} plus node -> set(links), streaming (657k links)."""
    link_nodes: dict[str, tuple[str, str]] = {}
    node_links: dict[str, set[str]] = defaultdict(set)
    opener = gzip.open if network_path.suffix == ".gz" else open
    with opener(network_path, "rb") as f:
        for _, el in ET.iterparse(f, events=["end"]):
            if el.tag == "link":
                lid, a, b = el.get("id"), el.get("from"), el.get("to")
                link_nodes[lid] = (a, b)
                node_links[a].add(lid)
                node_links[b].add(lid)
            el.clear()
    return link_nodes, node_links


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--hops", type=int, default=3)
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--scenario", default="rotterdam",
                    choices=["rotterdam", "rotterdam_L87"],
                    help="Which corridor the rings are built around. The preset "
                         "supplies the network and the corridor file, so a second "
                         "line never reads line 44's links.")
    args = ap.parse_args()

    preset = get_preset(args.scenario)
    root = Path(preset.base_config).parent.parent.parent
    scen = root / "scenarios" / "ipft_rotterdam"
    out_dir = Path(args.out_dir) if args.out_dir else scen

    corridor = {l for l in (scen / "corridor_links.txt").read_text().split() if l}
    link_nodes, node_links = load_topology(root / preset.network_file)
    print(f"rete: {len(link_nodes):,} link | corridoio van: {len(corridor)}")

    seen = set(corridor)
    frontier = set(corridor)
    cum = set(corridor)
    for k in range(1, args.hops + 1):
        nodes = {n for l in frontier if l in link_nodes for n in link_nodes[l]}
        ring = {l for n in nodes for l in node_links[n]} - seen
        seen |= ring
        cum |= ring
        (out_dir / f"khop_ring{k}.txt").write_text("\n".join(sorted(ring)) + "\n")
        (out_dir / f"khop_cum{k}.txt").write_text("\n".join(sorted(cum)) + "\n")
        print(f"  hop {k}: +{len(ring):6,} nuovi  -> cumulato {len(cum):7,} link "
              f"({100*len(cum)/len(link_nodes):.2f}% della rete)")
        frontier = ring
        if not ring:
            print("  (nessun link nuovo, mi fermo)")
            break

    # Guardia sulla definizione di hop: se questo numero cambia SENZA che sia
    # cambiato il corridoio, la definizione è andata alla deriva e ogni figura a
    # valle è sbagliata. Il valore atteso si aggiorna solo insieme al corridoio.
    #   164 -> corridoio 163 link (van sulla strada auto piu' veloce), fino al 2026-08-12
    #   137 -> corridoio 105 link (van instradato sui 7 locker), dal 2026-08-12
    EXPECTED_RING1 = 137
    r1 = len({l for l in (out_dir / "khop_ring1.txt").read_text().split() if l})
    print(f"\n[verify] anello 1-hop = {r1} (atteso {EXPECTED_RING1} per il corridoio "
          f"a {len(corridor)} link)"
          f" -> {'OK' if r1 == EXPECTED_RING1 else 'DIVERSO, controllare la definizione di hop'}")


if __name__ == "__main__":
    main()
