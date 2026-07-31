"""
Streaming parser for MATSim events.xml.zst files.

Extracts from the compressed event stream:
  - v_mean per (vehicle, link): from entered-link / left-link event pairs
  - passenger counts per bus: from PersonEntersPtVehicle / PersonLeavesPtVehicle
    events (the transit-specific types; the plain PersonEntersVehicle on a bus is
    the driver and is counted separately, not as a passenger)
  - per-stop standing time per bus: from VehicleArrivesAtFacility /
    VehicleDepartsAtFacility (tracked buses only)

Bus V_mean excludes standing time. For tracked buses, the standing time at a
stop facility (departure − arrival) is subtracted from the link travel time and
V_mean is computed over the DRIVING part only. Left inside, the reconstruction
would read the stop as very slow driving and charge fuel for stop-and-go
accelerations that never happened — while the dwell fuel is already charged
separately as idle (v = 0, engine on) in Term C. Same seconds, two charges.
The facility→link mapping comes from the facility id itself (the Rotterdam ids
embed "…link:<id>"); ids without that pattern (toy) are skipped, which keeps
the historical toy behaviour byte-identical.

The per-(vehicle, link) standing totals are exposed on the returned DataFrame as
    vmean_df.attrs["stop_standing"] = {vehicle_id: {link_id: standing_s}}
(attrs, not a third return value, so every existing caller keeps working).
Term C uses scenario−baseline standing as the MEASURED extra freight idle.

Never loads the full XML into memory. Uses zstandard streaming + iterparse + elem.clear().

Usage:
    from parse_events import parse_events
    vmean_df, pax_timeline = parse_events(events_zst_path, network_xml_path)
"""

import xml.etree.ElementTree as ET
from pathlib import Path
from collections import OrderedDict, defaultdict

import pandas as pd
import zstandard as zstd

from parameters import BUS_ID_PREFIXES, VAN_ID_PREFIX

# Module-level parse cache: (events_path, network_path) -> (df, pax_timeline)
# Avoids re-parsing the same large .zst file multiple times in sensitivity_surface.py,
# which walks (baseline, alpha_1..alpha_4) per group: the baseline is parsed once
# and hit four times, cutting a 30-cell Rotterdam sweep from 96 parses to 60.
#
# BOUNDED (LRU, 2 entries). The access pattern is baseline, scenario, baseline,
# scenario', ... so a capacity of 2 keeps the baseline resident while the
# scenarios rotate — full benefit, no unbounded growth over 60 parses.
_PARSE_CACHE: "OrderedDict" = OrderedDict()
_PARSE_CACHE_MAX = 2


def _cache_put(key, value) -> None:
    _PARSE_CACHE[key] = value
    _PARSE_CACHE.move_to_end(key)
    while len(_PARSE_CACHE) > _PARSE_CACHE_MAX:
        _PARSE_CACHE.popitem(last=False)


def open_events_stream(events_path: str):
    """Return a decompressed binary stream for a .zst or .gz events file.

    Caller is responsible for closing both the returned reader and, for zstd,
    the underlying file handle (use the returned context manager pair).
    """
    import contextlib
    import gzip

    @contextlib.contextmanager
    def _zst(path):
        with open(path, "rb") as f:
            dctx = zstd.ZstdDecompressor()
            with dctx.stream_reader(f) as reader:
                yield reader

    @contextlib.contextmanager
    def _gz(path):
        with gzip.open(path, "rb") as f:
            yield f

    return _gz(events_path) if str(events_path).endswith(".gz") else _zst(events_path)


# ── Network loader ─────────────────────────────────────────────────────────

# Cache: the Rotterdam network (657k links, gzipped) takes ~1 min to stream;
# sensitivity_surface calls parse_events dozens of times on the same network.
_LINK_ATTR_CACHE: dict = {}


def load_link_attributes(network_xml_path: str) -> tuple[dict, dict]:
    """
    Parse a MATSim network (plain .xml or .xml.gz) and return:
      link_lengths:    {link_id: length_m}
      link_freespeeds: {link_id: freespeed_ms}
    Streaming iterparse — the Rotterdam network is ~700 MB uncompressed, a
    full DOM parse would not fit comfortably in memory.
    """
    key = str(network_xml_path)
    if key in _LINK_ATTR_CACHE:
        return _LINK_ATTR_CACHE[key]

    import gzip

    link_lengths = {}
    link_freespeeds = {}
    opener = gzip.open if key.endswith(".gz") else open
    with opener(network_xml_path, "rb") as f:
        for _, elem in ET.iterparse(f, events=["end"]):
            if elem.tag == "link":
                lid = elem.get("id")
                length = elem.get("length")
                freespeed = elem.get("freespeed")
                if lid and length:
                    link_lengths[lid] = float(length)
                if lid and freespeed:
                    link_freespeeds[lid] = float(freespeed)
            elem.clear()

    _LINK_ATTR_CACHE[key] = (link_lengths, link_freespeeds)
    return link_lengths, link_freespeeds


# ── Vehicle classifier ─────────────────────────────────────────────────────

def classify_vehicle(vehicle_id: str, bus_prefixes: tuple[str, ...] = BUS_ID_PREFIXES) -> str:
    """Returns 'bus', 'van', or 'background'.

    `bus_prefixes` marks ALL transit vehicles (so they never count as
    background). For Rotterdam pass ("veh_",); the toy default reproduces
    the historical behaviour.
    """
    if vehicle_id.startswith(bus_prefixes):
        return "bus"
    if vehicle_id.startswith(VAN_ID_PREFIX):
        return "van"
    return "background"


# ── Core streaming parser ──────────────────────────────────────────────────

def parse_events(
    events_zst_path: str,
    network_xml_path: str,
    verbose: bool = True,
    bus_prefixes: tuple[str, ...] | None = None,
    pax_bus_ids: frozenset[str] | set[str] | None = None,
    keep_link_ids: frozenset[str] | set[str] | None = None,
) -> tuple[pd.DataFrame, dict]:
    """
    Stream-parse a MATSim events.xml.zst file.

    Parameters
    ----------
    bus_prefixes : vehicle-id prefixes that mark transit vehicles for the
        'bus' classification (default: parameters.BUS_ID_PREFIXES — toy).
        For Rotterdam pass ("veh_",) so ALL transit is excluded from background.
    pax_bus_ids : if given, the passenger timeline is tracked ONLY for these
        exact vehicle ids (e.g. the 197 line-44 buses). If None, all vehicles
        matching bus_prefixes are tracked (toy behaviour).

    Returns
    -------
    vmean_df : pd.DataFrame
        Columns: vehicle_id, link_id, v_mean_ms, time_entered_s, travel_time_s, vehicle_type
    pax_timeline : dict
        {bus_vehicle_id: list of (time_s, cumulative_count)}
        Sorted by time. Use get_passengers_at_time() to query.
    """
    if bus_prefixes is None:
        bus_prefixes = BUS_ID_PREFIXES
    pax_ids = frozenset(pax_bus_ids) if pax_bus_ids is not None else None
    keep_ids = frozenset(keep_link_ids) if keep_link_ids is not None else None

    def _track_pax(vid: str) -> bool:
        if pax_ids is not None:
            return vid in pax_ids
        return vid.startswith(bus_prefixes)

    # Memory-lean filtering (Rotterdam): when keep_link_ids is given, only the
    # records the CO2 terms actually need are accumulated, which shrinks the
    # DataFrame from every car on every link (~16M rows, 6-8 GB) to a small set.
    # Kept: vans (Term B), the freight-carrying buses in pax_ids (Term C), and any
    # vehicle on a corridor link (the S_cong corridor-background metric). Region-wide
    # background is not needed here (Rotterdam Term A is HBEFA-stub, emissions off).
    # keep_link_ids is None (toy) keeps everything, unchanged.
    def _keep(vid: str, lid: str) -> bool:
        if keep_ids is None:
            return True
        if vid.startswith(VAN_ID_PREFIX) or lid in keep_ids:
            return True
        return pax_ids is not None and vid in pax_ids

    cache_key = (str(events_zst_path), str(network_xml_path),
                 tuple(bus_prefixes), pax_ids, keep_ids)
    if cache_key in _PARSE_CACHE:
        _PARSE_CACHE.move_to_end(cache_key)          # LRU touch
        cached_df, cached_pax = _PARSE_CACHE[cache_key]
        # Deep copy of pax_timeline: each value is a list of (time, count) tuples
        # that callers may not modify, but the defensive copy prevents cache
        # corruption if a future caller does (e.g. sorting/filtering in place).
        out_df = cached_df.copy()
        out_df.attrs["stop_standing"] = cached_df.attrs.get("stop_standing", {})
        return out_df, {k: list(v) for k, v in cached_pax.items()}

    link_lengths, link_freespeeds = load_link_attributes(network_xml_path)

    entry_times: dict = {}       # {(vehicle_id, link_id): time_entered_s}
    records: list = []           # accumulated (vehicle_id, link_id, v_mean_ms, t_enter)
    pax_counts: dict = defaultdict(int)    # {bus_vehicle_id: current count}
    pax_timeline: dict = defaultdict(list) # {bus_vehicle_id: [(time, count), ...]}

    # Stop-facility standing (tracked buses): the facility id embeds the link
    # ("2685255.link:448306"); ids without "link:" are skipped (toy).
    open_stop: dict = {}                       # {vehicle_id: (link_id, t_arrival)}
    stop_intervals: dict = defaultdict(list)   # {(veh, link): [(t_arr, t_dep), ...]}
    stop_standing: dict = defaultdict(lambda: defaultdict(float))  # veh -> link -> s

    def _facility_link(fac_id: str | None) -> str | None:
        if fac_id and "link:" in fac_id:
            return fac_id.split("link:", 1)[1]
        return None

    # Counters for diagnostics
    n_entered = 0
    n_left = 0
    n_stuck = 0
    n_zero_dt = 0
    n_overspeed = 0
    n_pax_board = 0      # PersonEntersPtVehicle on tracked buses (real passengers)
    n_driver_board = 0   # PersonEntersVehicle on tracked buses (drivers, not counted)
    n_stops_matched = 0  # facility stops whose standing was subtracted from a link
    total_standing_s = 0.0

    with open_events_stream(events_zst_path) as reader:
            for _, elem in ET.iterparse(reader, events=["end"]):
                if elem.tag != "event":
                    elem.clear()
                    continue

                etype = elem.get("type")
                t = float(elem.get("time", 0))

                # ── Link travel events ─────────────────────────────────
                if etype == "entered link":
                    vid = elem.get("vehicle")
                    lid = elem.get("link")
                    if vid and lid:
                        entry_times[(vid, lid)] = t
                        n_entered += 1

                elif etype == "left link":
                    vid = elem.get("vehicle")
                    lid = elem.get("link")
                    if vid and lid:
                        key = (vid, lid)
                        t_enter = entry_times.pop(key, None)
                        if t_enter is not None:
                            n_left += 1
                            dt = t - t_enter
                            if dt <= 0:
                                n_zero_dt += 1
                            elif lid in link_lengths:
                                # Subtract the standing time of any stop served
                                # during THIS traversal: V_mean must describe the
                                # driving part only (the stop is charged as idle
                                # in Term C, never as slow driving).
                                standing = 0.0
                                pending = stop_intervals.get(key)
                                if pending:
                                    remaining = []
                                    for t_arr, t_dep in pending:
                                        if t_arr >= t_enter - 0.5 and t_dep <= t + 0.5:
                                            standing += t_dep - t_arr
                                        elif t_dep > t + 0.5:
                                            remaining.append((t_arr, t_dep))
                                        # intervals before t_enter: stale, drop
                                    if remaining:
                                        stop_intervals[key] = remaining
                                    else:
                                        del stop_intervals[key]
                                if standing > 0:
                                    n_stops_matched += 1
                                    total_standing_s += standing
                                    dt = max(1.0, dt - standing)
                                v = link_lengths[lid] / dt
                                # Sanity: clamp to 1.5× freespeed (MATSim can briefly exceed)
                                fs = link_freespeeds.get(lid)
                                if fs and v > 1.5 * fs:
                                    n_overspeed += 1
                                    v = fs  # use freespeed as ceiling
                                    dt = link_lengths[lid] / v  # recompute after clamp
                                if _keep(vid, lid):
                                    records.append((vid, lid, v, t_enter, dt))

                # ── Stuck vehicle: discard all pending entries ─────────
                elif etype == "stuckAndAbort":
                    vid = elem.get("vehicle") or elem.get("person")
                    if vid:
                        keys_to_drop = [k for k in entry_times if k[0] == vid]
                        for k in keys_to_drop:
                            del entry_times[k]
                        n_stuck += 1

                # ── Passenger boarding / alighting ─────────────────────
                # MATSim 2026 writes transit boardings as PersonEntersPtVehicle,
                # a SEPARATE xml type (the Java class extends PersonEntersVehicleEvent,
                # so Java handlers see both, but a string match on the xml does not).
                # The plain PersonEntersVehicle on a transit vehicle is the DRIVER:
                # it is deliberately NOT counted here, because the driver mass is
                # added explicitly as +1 occupant in feasibility.compute_alpha_max.
                elif etype == "PersonEntersPtVehicle":
                    vid = elem.get("vehicle")
                    if vid and _track_pax(vid):
                        pax_counts[vid] += 1
                        pax_timeline[vid].append((t, pax_counts[vid]))
                        n_pax_board += 1

                elif etype == "PersonLeavesPtVehicle":
                    vid = elem.get("vehicle")
                    if vid and _track_pax(vid):
                        pax_counts[vid] = max(0, pax_counts[vid] - 1)
                        pax_timeline[vid].append((t, pax_counts[vid]))

                elif etype == "PersonEntersVehicle":
                    vid = elem.get("vehicle")
                    if vid and _track_pax(vid):
                        n_driver_board += 1

                # ── Stop-facility standing time (tracked buses) ────────
                elif etype == "VehicleArrivesAtFacility":
                    vid = elem.get("vehicle")
                    if vid and _track_pax(vid):
                        flink = _facility_link(elem.get("facility"))
                        if flink is not None:
                            open_stop[vid] = (flink, t)

                elif etype == "VehicleDepartsAtFacility":
                    vid = elem.get("vehicle")
                    if vid and vid in open_stop:
                        flink, t_arr = open_stop.pop(vid)
                        if t >= t_arr:
                            stop_intervals[(vid, flink)].append((t_arr, t))
                            stop_standing[vid][flink] += t - t_arr

                elem.clear()

    # ── Build DataFrame ────────────────────────────────────────────────────
    df = pd.DataFrame(
        records,
        columns=["vehicle_id", "link_id", "v_mean_ms", "time_entered_s", "travel_time_s"],
    )
    df["vehicle_type"] = df["vehicle_id"].apply(
        lambda vid: classify_vehicle(vid, bus_prefixes)
    )

    # Per-(vehicle, link) standing totals for Term C's measured extra idle.
    # Carried on df.attrs so the (df, pax) return signature stays unchanged
    # for every existing caller.
    df.attrs["stop_standing"] = {vid: dict(links)
                                 for vid, links in stop_standing.items()}

    if verbose:
        print(f"[parse_events] entered-link events: {n_entered:,}")
        print(f"[parse_events] left-link events matched: {n_left:,}")
        print(f"[parse_events] stuck vehicles discarded: {n_stuck}")
        print(f"[parse_events] zero-dt links skipped: {n_zero_dt}")
        print(f"[parse_events] overspeed links clamped: {n_overspeed}")
        print(f"[parse_events] total v_mean records: {len(df):,}")
        print(f"[parse_events] pt boardings on tracked buses: {n_pax_board:,} "
              f"(drivers seen, not counted: {n_driver_board:,})")
        if n_driver_board and not n_pax_board:
            print("[parse_events] WARNING: drivers but ZERO passenger boardings — "
                  "check the event type written by this MATSim version")
        print(f"[parse_events] bus standing time subtracted from V_mean: "
              f"{total_standing_s:,.0f} s over {n_stops_matched:,} stop traversals "
              f"({len(df.attrs['stop_standing'])} buses with facility events)")
        print(f"[parse_events] vehicle types: {df['vehicle_type'].value_counts().to_dict()}")

    pax_dict = dict(pax_timeline)
    _cache_put(cache_key, (df, pax_dict))
    out_df = df.copy()
    out_df.attrs["stop_standing"] = df.attrs["stop_standing"]  # copy() may drop attrs
    return out_df, {k: list(v) for k, v in pax_dict.items()}


# ── Passenger count helper ─────────────────────────────────────────────────

def get_passengers_at_time(pax_timeline: dict, bus_id: str, query_time: float) -> int:
    """
    Return the passenger count on bus_id at query_time (seconds since midnight).
    Uses the most recent boarding/alighting event before query_time.
    Returns 0 if no events recorded before query_time.
    """
    events = pax_timeline.get(bus_id, [])
    count = 0
    for t, c in events:
        if t <= query_time:
            count = c
        else:
            break
    return count


def get_avg_passengers_on_link(
    pax_timeline: dict, bus_id: str, t_enter: float, t_leave: float
) -> float:
    """
    Average passenger count on bus_id during [t_enter, t_leave].
    Simple trapezoidal average over the boarding/alighting events in that window.
    """
    events = pax_timeline.get(bus_id, [])
    if not events:
        return 0.0

    # Collect all (time, count) points in the window, plus boundary values
    window = []
    prev_count = 0
    for t, c in events:
        if t < t_enter:
            prev_count = c
        elif t_enter <= t <= t_leave:
            if not window:
                window.append((t_enter, prev_count))
            window.append((t, c))
        else:
            break

    if not window:
        return float(get_passengers_at_time(pax_timeline, bus_id, t_enter))

    window.append((t_leave, window[-1][1]))

    # Trapezoidal mean
    total_time = t_leave - t_enter
    if total_time <= 0:
        return float(window[0][1])

    area = 0.0
    for i in range(len(window) - 1):
        dt = window[i + 1][0] - window[i][0]
        avg_c = (window[i][1] + window[i + 1][1]) / 2.0
        area += avg_c * dt
    return area / total_time


# ── CLI entry point for standalone testing ─────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage: python parse_events.py <events.xml.zst> <network.xml>")
        sys.exit(1)

    events_path = sys.argv[1]
    network_path = sys.argv[2]

    vmean_df, pax_tl = parse_events(events_path, network_path)

    out = Path(events_path).with_suffix("").with_suffix("_vmean.parquet")
    vmean_df.to_parquet(out, index=False)
    print(f"[parse_events] saved v_mean DataFrame to {out}")

    print("\nSample rows:")
    print(vmean_df.groupby("vehicle_type")["v_mean_ms"].describe())
