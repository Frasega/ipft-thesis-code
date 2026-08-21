"""
Insert backup van agents into a MATSim plans.xml / plans.xml.gz file.

For a given alpha (load success rate) and N total freight units:
  n_backup_vans = round((1 - alpha) * N_freight)          # one van per parcel
but every campaign in the thesis passes n_vans_override with the CONSOLIDATED
tour count instead, n_backup_vans = ceil((1 - alpha) * N_freight / C_van(w)),
together with n_slots = the alpha=0 tour count so the departure grid is the
baseline's (see insert_vans).

Each van agent:
  - subpopulation = 'freight'  (ChangeExpBeta only; no mode choice / re-routing)
  - Plan: freight_hub activity at hub_link → leg → freight_delivery at terminal_link
  - ID: backup_van_0000, backup_van_0001, ... (prefix used for classification in pipeline)

Scenario differences (see scenario_presets.py):
  - toy:       leg mode 'freight' (toy network links carry the freight mode),
               all vans depart at 07:00:00 sharp.
  - rotterdam: leg mode 'car' (the Rotterdam network has no freight mode and
               patching 657k links is not worth it; the backup_van_ id prefix
               keeps vans separable in every downstream filter), departures
               spread evenly over 07:00–09:00 (depot departure window) to avoid
               an artificial 470-van queue spike on the hub link.

Van departure time is the SAME in peak and off-peak scenarios: peak vs off-peak
isolates the effect of background traffic density only.

Usage:
    python insert_vans.py --scenario rotterdam --base-plans <plans.xml.gz>
                          --output <out.xml.gz> --alpha 0.5 --n-freight 470
"""

from __future__ import annotations

import argparse
import gzip
import io
import xml.etree.ElementTree as ET
from pathlib import Path

# ── Toy network constants (defaults — overridden by --scenario rotterdam) ──
HUB_LINK = "H_5_0_H_5_1"        # Hub H: west side, center row (x=0→200, y=1000)
TERMINAL_LINK = "B_5_14_B_5_15"  # Terminal B: east side, center (x=10300→10500, y=1000)

HUB_X, HUB_Y = 100.0, 1000.0
TERMINAL_X, TERMINAL_Y = 10400.0, 1000.0

VAN_DEPARTURE = "07:00:00"
PEAK_DEPARTURE = VAN_DEPARTURE       # kept for backward compatibility
OFF_PEAK_DEPARTURE = VAN_DEPARTURE   # SAME as peak — by design

VAN_ID_PREFIX = "backup_van_"


def _open_text(path: str | Path, mode: str):
    """Open plain / gzip / zstd XML transparently ('r' or 'w' text mode).

    `.zst` is read-only here (the Rotterdam warm-start seed is
    output_plans.xml.zst): it is streamed and decoded to text so insert_vans can
    consume the Rotterdam longbase directly, without a manual .zst->.xml step.
    We never write .zst (outputs stay .gz/.xml for MATSim).
    """
    path = str(path)
    if path.endswith(".gz"):
        return gzip.open(path, mode + "t", encoding="utf-8")
    if path.endswith(".zst"):
        if "r" not in mode:
            raise ValueError("writing .zst is not supported; use .gz or .xml output")
        import zstandard
        fh = open(path, "rb")
        reader = zstandard.ZstdDecompressor().stream_reader(fh, closefd=True)
        return io.TextIOWrapper(reader, encoding="utf-8")
    return open(path, mode, encoding="utf-8")


def _hms_to_s(hms: str) -> int:
    h, m, s = hms.split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def _s_to_hms(t: float) -> str:
    t = int(round(t))
    return f"{t // 3600:02d}:{(t % 3600) // 60:02d}:{t % 60:02d}"


# ── Core insertion function ────────────────────────────────────────────────

def _build_van_xml(
    van_id: str,
    hub_link: str,
    hub_x: float,
    hub_y: float,
    terminal_link: str,
    terminal_x: float,
    terminal_y: float,
    departure_time: str,
    van_mode: str = "freight",
    locker_stops: tuple[tuple[str, float, float], ...] = (),
) -> str:
    """
    Return XML string for one freight van agent.
    Subpopulation is stored as a child <attributes> element (MATSim v6 format),
    NOT as an XML attribute on <person> (which is not declared in population_v6.dtd).

    locker_stops = ((link_id, x, y), ...) — the kerbside lockers the van serves,
    in order. Each becomes an activity ON that link, which is what forces the
    router to take the van there: a single hub->terminal leg is free to pick the
    fastest car road and, on the Rotterdam corridor, missed every locker the bus
    serves. That contradicted the thesis, which states the van visits the same
    lockers, and it put the congestion relief on the wrong streets.

    The activities have ZERO duration: the van leaves the network for an instant
    and departs again, so the handover is NOT simulated as a stop. The per-stop
    idle stays an a-priori post-processing term and nothing is counted twice.
    A car-mode agent performing an activity is off the road anyway (it parks
    off-network), so the van still does not block traffic — now as an explicit,
    reportable property rather than an accident of the routing.
    """
    # Toy: mode='freight' — bound to the `freight` vehicleType in emission_vehicles.xml
    # (HBEFA LCV, PCE 1.5), keeping van HBEFA emissions separable from background.
    # Rotterdam: mode='car' — vans are filtered by id prefix instead; their HBEFA
    # values are discarded anyway (Term B uses the longitudinal-dynamics formula).
    leg = f'\t\t\t<leg mode="{van_mode}">\n\t\t\t</leg>\n'
    middle = "".join(
        f'\t\t\t<activity type="freight_locker" link="{lid}" '
        f'x="{x}" y="{y}" max_dur="00:00:00" />\n' + leg
        for lid, x, y in locker_stops
    )
    return (
        f'\t<person id="{van_id}">\n'
        f'\t\t<attributes>\n'
        f'\t\t\t<attribute name="subpopulation" class="java.lang.String">freight</attribute>\n'
        f'\t\t</attributes>\n'
        f'\t\t<plan selected="yes">\n'
        f'\t\t\t<activity type="freight_hub" link="{hub_link}" '
        f'x="{hub_x}" y="{hub_y}" end_time="{departure_time}" />\n'
        + leg
        + middle
        + f'\t\t\t<activity type="freight_delivery" link="{terminal_link}" '
        f'x="{terminal_x}" y="{terminal_y}" />\n'
        f'\t\t</plan>\n'
        f'\t</person>\n'
    )


def insert_vans(
    base_plans_path: str,
    output_path: str,
    n_vans: int,
    hub_link: str = HUB_LINK,
    terminal_link: str = TERMINAL_LINK,
    hub_x: float = HUB_X,
    hub_y: float = HUB_Y,
    terminal_x: float = TERMINAL_X,
    terminal_y: float = TERMINAL_Y,
    departure_time: str = PEAK_DEPARTURE,
    van_mode: str = "freight",
    spread_minutes: float = 0.0,
    locker_stops: tuple[tuple[str, float, float], ...] = (),
    n_slots: int | None = None,
    verbose: bool = True,
) -> None:
    """
    Read base_plans_path (plain or .gz), inject n_vans freight van agents before
    </population>, write to output_path (plain or .gz). The original agents are
    copied byte-for-byte — no ElementTree re-formatting, no DTD issues.

    spread_minutes > 0 distributes van departures evenly over
    [departure_time, departure_time + spread_minutes].

    n_slots — the DEPARTURE GRID, and the reason it exists (2026-08-21):
    without it the grid was spaced by n_vans, so the baseline (11 tours, one
    every 12 min) and a scenario (5 tours, one every 30 min) sent their vans at
    DIFFERENT times. Subtracting the two then mixed "fewer vans" with "vans
    moved to another part of the peak", and the congestion delta carried both.
    Pass the BASELINE tour count: every scenario then keeps the baseline's own
    slots and simply stops early, so a van that survives departs at exactly the
    time it departed in the baseline and the delta is only the missing tours.
    The vans dropped are the LAST ones (the latest departures) — the convention
    agreed in the plan; the alternative, thinning the grid evenly, would spread
    the relief over the window instead of concentrating it at the end.
    n_slots=None keeps the historical behaviour, and n_slots == n_vans (the
    alpha=0 baseline) reproduces it exactly, so BASELINE RUNS ARE UNCHANGED.
    """
    with _open_text(base_plans_path, "r") as f:
        content = f.read()

    base_s = _hms_to_s(departure_time)

    grid = n_slots if n_slots else n_vans

    def _dep(i: int) -> str:
        if spread_minutes <= 0 or grid <= 1:
            return departure_time
        return _s_to_hms(base_s + i * (spread_minutes * 60.0) / (grid - 1))

    van_blocks = "".join(
        _build_van_xml(
            van_id=f"{VAN_ID_PREFIX}{i:04d}",
            hub_link=hub_link, hub_x=hub_x, hub_y=hub_y,
            terminal_link=terminal_link, terminal_x=terminal_x, terminal_y=terminal_y,
            departure_time=_dep(i),
            van_mode=van_mode,
            locker_stops=locker_stops,
        )
        for i in range(n_vans)
    )

    close_tag = "</population>"
    if close_tag not in content:
        raise ValueError(f"Could not find {close_tag!r} in {base_plans_path}")

    new_content = content.replace(close_tag, van_blocks + close_tag, 1)

    with _open_text(output_path, "w") as f:
        f.write(new_content)

    if verbose:
        spread_note = (f" spread over {spread_minutes:.0f} min on a {grid}-slot grid"
                       if spread_minutes > 0 else "")
        print(f"[insert_vans] Added {n_vans} vans (mode={van_mode}) -> {output_path}")
        print(f"  departure: {departure_time}{spread_note}  hub: {hub_link}  terminal: {terminal_link}")
        if locker_stops:
            print(f"  lockers served (zero-duration stops): "
                  f"{', '.join(l for l, _, _ in locker_stops)}")
        else:
            print("  lockers: NONE — single hub->terminal leg (toy behaviour)")


def create_plans_file(
    base_plans_path: str,
    output_path: str,
    alpha: float,
    n_freight: int,
    congestion: str = "peak",
    verbose: bool = True,
    hub_link: str = HUB_LINK,
    terminal_link: str = TERMINAL_LINK,
    hub_x: float = HUB_X,
    hub_y: float = HUB_Y,
    terminal_x: float = TERMINAL_X,
    terminal_y: float = TERMINAL_Y,
    van_mode: str = "freight",
    spread_minutes: float = 0.0,
    n_vans_override: int | None = None,
    locker_stops: tuple[tuple[str, float, float], ...] = (),
    n_slots: int | None = None,
) -> int:
    """
    Create one plans file for a given (alpha, congestion) combination.

    By default inserts (1-alpha)*n_freight vans (one per parcel). Pass
    n_vans_override to insert the CONSOLIDATED tour count instead
    (⌈(1-alpha)*N / C_van(w)⌉), so the simulated congestion (Term A) and the van
    speeds (Term B) reflect the real number of van vehicles, not one-per-parcel.

    n_slots is the departure grid — pass the alpha=0 tour count so every
    scenario reuses the baseline's departure times (see insert_vans).

    Returns the number of vans inserted.
    """
    n_vans = int(n_vans_override) if n_vans_override is not None else int(round((1 - alpha) * n_freight))
    departure = PEAK_DEPARTURE if congestion == "peak" else OFF_PEAK_DEPARTURE
    insert_vans(
        base_plans_path=base_plans_path,
        output_path=output_path,
        n_vans=n_vans,
        hub_link=hub_link, terminal_link=terminal_link,
        hub_x=hub_x, hub_y=hub_y, terminal_x=terminal_x, terminal_y=terminal_y,
        departure_time=departure,
        van_mode=van_mode,
        spread_minutes=spread_minutes,
        locker_stops=locker_stops,
        n_slots=n_slots,
        verbose=verbose,
    )
    return n_vans


# ── Off-peak subsampling ───────────────────────────────────────────────────

def _read_subpopulation(person_elem) -> str | None:
    """
    Read the subpopulation of a <person>. Supports both MATSim formats:
      - v5/legacy: <person id=".." subpopulation="middle">
      - v6:       <person id=".."><attributes><attribute name="subpopulation" ...>middle</attribute>...
    Returns None if no subpopulation is declared.
    """
    sub = person_elem.get("subpopulation")
    if sub:
        return sub
    attrs = person_elem.find("attributes")
    if attrs is not None:
        for a in attrs.findall("attribute"):
            if a.get("name") == "subpopulation":
                return (a.text or "").strip() or None
    return None


def create_offpeak_base(
    peak_plans_path: str,
    output_path: str,
    sample_fraction: float = 0.50,
    seed: int = 42,
    verbose: bool = True,
) -> int:
    # Default 0.50: TomTom Traffic Index 2025 reports Rotterdam peak congestion
    # 55%–254% above free-flow vs ~0–10% off-peak. The peak/off-peak volume
    # ratio for urban arterials is typically 1.8–2.2× (K-factor 8–12%) →
    # off-peak ≈ 45–55% of peak. 0.50 is the midpoint.
    """
    Create a reduced-population base plans file for off-peak scenarios.

    Randomly retains sample_fraction of background agents (subpopulation != 'freight').
    Freight agents (if any) are always retained.

    Large .gz inputs (Rotterdam, ~300 MB uncompressed) are processed with a
    line-based streaming filter — the MATSim population writer puts <person ...>
    and </person> on their own lines, so person blocks can be cut without a
    full DOM parse. Plain-XML inputs (toy) keep the original ElementTree path
    byte-for-byte compatible with the historical output.

    Returns the number of background agents kept.
    """
    import random
    random.seed(seed)

    if str(peak_plans_path).endswith(".gz") or str(output_path).endswith(".gz"):
        return _create_offpeak_base_streaming(
            peak_plans_path, output_path, sample_fraction, random, verbose
        )

    tree = ET.parse(peak_plans_path)
    root = tree.getroot()
    population = root if root.tag == "population" else root.find("population")

    persons = list(population.findall("person"))
    background = [p for p in persons if _read_subpopulation(p) != "freight"]
    freight = [p for p in persons if _read_subpopulation(p) == "freight"]

    n_keep = int(round(len(background) * sample_fraction))
    kept = random.sample(background, n_keep)
    kept_ids = {p.get("id") for p in kept} | {p.get("id") for p in freight}

    for person in persons:
        if person.get("id") not in kept_ids:
            population.remove(person)

    ET.indent(tree, space="  ")
    # ET.write() silently drops the DOCTYPE declaration, but MATSim's
    # PopulationReader requires it. Serialize and prepend the DTD manually.
    buf = io.BytesIO()
    tree.write(buf, xml_declaration=False, encoding="utf-8")
    xml_body = buf.getvalue().decode("utf-8")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("<?xml version='1.0' encoding='utf-8'?>\n")
        f.write('<!DOCTYPE population SYSTEM "http://www.matsim.org/files/dtd/population_v6.dtd">\n')
        f.write(xml_body)
        if not xml_body.endswith("\n"):
            f.write("\n")

    if verbose:
        print(f"[create_offpeak_base] {len(background)} -> {n_keep} background agents "
              f"({sample_fraction:.0%} sample)  seed={seed}; "
              f"freight retained: {len(freight)}")
        print(f"  output: {output_path}")

    return n_keep


def _create_offpeak_base_streaming(
    peak_plans_path: str,
    output_path: str,
    sample_fraction: float,
    rng,
    verbose: bool,
) -> int:
    """Streaming person-block filter for large (gzipped) plans files."""
    n_seen = 0
    n_kept = 0
    n_freight = 0

    with _open_text(peak_plans_path, "r") as fin, _open_text(output_path, "w") as fout:
        in_person = False
        block: list[str] = []
        for line in fin:
            if not in_person and "<person " in line:
                in_person = True
                block = [line]
                # one-line person (<person ... />) cannot occur in MATSim v6 output
            elif in_person:
                block.append(line)
                if "</person>" in line:
                    in_person = False
                    n_seen += 1
                    body = "".join(block)
                    is_freight = ">freight</attribute>" in body
                    if is_freight:
                        n_freight += 1
                        fout.write(body)
                    elif rng.random() < sample_fraction:
                        n_kept += 1
                        fout.write(body)
                    block = []
            else:
                fout.write(line)

    if verbose:
        print(f"[create_offpeak_base] streaming: {n_seen} persons -> kept {n_kept} "
              f"background ({sample_fraction:.0%} expected) + {n_freight} freight")
        print(f"  output: {output_path}")
    return n_kept


# ── CLI ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Insert backup van agents into plans.xml(.gz)")
    p.add_argument("--scenario", default="toy", choices=["toy", "rotterdam"],
                   help="Preset for hub/terminal links, coordinates, van mode and "
                        "departure spread (default: toy)")
    p.add_argument("--base-plans", default=None,
                   help="Base plans file (default: preset peak base plans)")
    p.add_argument("--output", required=True)
    p.add_argument("--alpha", type=float, default=0.5)
    p.add_argument("--n-freight", type=int, default=None,
                   help="SIMULATED freight units (default: preset value)")
    p.add_argument("--congestion", choices=["peak", "offpeak"], default="peak")
    p.add_argument("--make-offpeak-base", action="store_true",
                   help="Subsample base plans for off-peak (run once, before inserting vans)")
    p.add_argument("--sample-fraction", type=float, default=0.50,
                   help="Off-peak background fraction of peak (default 0.50)")
    args = p.parse_args()

    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from scenario_presets import get_preset
    preset = get_preset(args.scenario)

    base_plans = args.base_plans or preset.peak_base_plans
    n_freight = args.n_freight if args.n_freight is not None else preset.n_freight_units_sim

    if args.make_offpeak_base:
        create_offpeak_base(base_plans, args.output, args.sample_fraction)
    else:
        n = create_plans_file(
            base_plans_path=base_plans,
            output_path=args.output,
            alpha=args.alpha,
            n_freight=n_freight,
            congestion=args.congestion,
            hub_link=preset.hub_link,
            terminal_link=preset.terminal_link,
            hub_x=preset.hub_xy[0], hub_y=preset.hub_xy[1],
            terminal_x=preset.terminal_xy[0], terminal_y=preset.terminal_xy[1],
            van_mode=preset.van_mode,
            spread_minutes=preset.van_spread_minutes,
        )
        print(f"Inserted {n} vans for alpha={args.alpha:.0%}, congestion={args.congestion}")
