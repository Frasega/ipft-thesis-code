"""Deriva TUTTI i valori che il preset di una seconda linea richiede, per qualsiasi
transitLine e qualsiasi hub, senza numeri scritti a mano.

Uso (dalla root del progetto):
    python derive_line.py <line_id> <hub_stop_link>
esempi:
    python derive_line.py 99437 174131      # linea 44, per riprodurre i valori noti
    python derive_line.py 99431 174131      # Centraal -> Meijersplein

Stampa: F, fermate, link di percorso, km, headway, pickup_link_ids in ordine di
route, van_locker_stops con le coordinate dei to-node, bacino a 400 m, N_real,
N_sim, dwell per fermata, van-tour per alpha, e i tre vincoli di fattibilita'.
Scrive anche <line>_<hub>_vehicle_ids.txt e <line>_<hub>_route_links.txt.
"""
from __future__ import annotations

import gzip
import math
import re
import statistics
import sys
from pathlib import Path

SC = Path("scenarios/ipft_rotterdam")
WALK_BUFFER_M = 400.0
SAMPLE_RATE = 0.10
PARCELS_PER_PERSON_DAY = 0.125
DWELL_FIXED_S = 10.0
DWELL_PER_UNIT_S = 5.0
VAN_PAYLOAD_KG = 1100.0
VAN_PARCELS_MAX = 150
BUS_FREIGHT_CAPACITY_KG = 1000.0
BUS_FREIGHT_NMAX = 70
WEIGHTS = {"light": 3.0, "medium": 10.0, "heavy": 25.0}
ALPHAS = [0.0, 0.25, 0.50, 0.75, 1.0]


def c_van(w: float) -> int:
    return min(VAN_PARCELS_MAX, int(VAN_PAYLOAD_KG // w))


def main(line_id: str, hub_link: str) -> None:
    sched = gzip.open(SC / "ptSchedule36Hour.xml.gz", "rt", encoding="utf-8").read()

    fac = {}
    for m in re.finditer(r'<stopFacility ([^>]+?)/?>', sched):
        a = dict(re.findall(r'([\w:]+)="([^"]*)"', m.group(1)))
        if "id" in a:
            fac[a["id"]] = (a.get("name", "?"), a.get("linkRefId", "?"),
                            float(a.get("x", 0)), float(a.get("y", 0)))

    block = re.search(rf'<transitLine id="{line_id}"[^>]*>(.*?)</transitLine>', sched, re.S)
    if not block:
        sys.exit(f"transitLine {line_id} non trovata")
    stops = None
    links = None
    veh: list[str] = []
    deps: list[str] = []
    n_routes = 0
    for rid, rb in re.findall(r'<transitRoute id="([^"]+)">(.*?)</transitRoute>',
                              block.group(1), re.S):
        st = re.findall(r'<stop refId="([^"]+)"', rb)
        if not st or hub_link not in st[0]:
            continue
        n_routes += 1
        rt = re.search(r'<route>(.*?)</route>', rb, re.S)
        L = re.findall(r'<link refId="([^"]+)"', rt.group(1)) if rt else []
        if stops is None or len(st) > len(stops):
            stops, links = st, L
        veh += re.findall(r'vehicleRefId="([^"]+)"', rb)
        deps += re.findall(r'departureTime="([^"]+)"', rb)
    if stops is None:
        sys.exit(f"nessuna route della linea {line_id} parte dal link {hub_link}")

    # rete: lunghezze, modes, to-node coords
    node = {}
    length, modes, tonode = {}, {}, {}
    with gzip.open(SC / "networkWithRideAndBike.xml.gz", "rt", encoding="utf-8") as f:
        for line in f:
            if "<node " in line:
                m = re.search(r'<node id="([^"]+)" x="([^"]+)" y="([^"]+)"', line)
                if m:
                    node[m.group(1)] = (float(m.group(2)), float(m.group(3)))
            elif "<link " in line:
                m = re.search(r'<link id="([^"]+)" from="[^"]+" to="([^"]+)" '
                              r'length="([^"]+)" freespeed="[^"]+" capacity="[^"]+" '
                              r'permlanes="[^"]+" oneway="[^"]+" modes="([^"]+)"', line)
                if m:
                    length[m.group(1)] = float(m.group(3))
                    modes[m.group(1)] = m.group(4)
                    tonode[m.group(1)] = m.group(2)

    F = len(veh)
    km = sum(length.get(l, 0.0) for l in links) / 1000
    n_delivery = len(stops) - 1
    def hms(v):
        h, m, s = v.split(":")
        return int(h) * 3600 + int(m) * 60 + int(s)
    t = sorted(hms(d) for d in deps)
    span_h = (t[-1] - t[0]) / 3600

    print(f"=== transitLine {line_id}, direzione con prima fermata su link {hub_link} ===")
    print(f"route variant aggregate: {n_routes} | F = {F} corse | {len(stops)} fermate "
          f"({n_delivery} di consegna) | {len(links)} link | {km:.2f} km")
    print(f"servizio {t[0]/3600:.2f}-{t[-1]/3600:.2f} h | headway medio {span_h*60/(F-1):.1f} min")
    print(f"fermate per km {n_delivery/km:.2f} | spaziatura media {km/n_delivery*1000:.0f} m")

    stoplinks = [fac[s][1] for s in stops]
    print(f"\nlink di fermata (il primo e' l'hub):")
    for s, l in zip(stops, stoplinks):
        print(f"  {l:>8} modes={modes.get(l,'?'):<14} {fac[s][0]}")
    car_stops = [l for l in stoplinks[1:] if "car" in modes.get(l, "")]
    print(f"\npickup_link_ids = {tuple(stoplinks[1:])}")
    print(f"van_locker_stops = (")
    for l in car_stops:
        n = tonode.get(l)
        if n and n in node:
            print(f"    ({l!r}, {node[n][0]:.1f}, {node[n][1]:.1f}),")
    print(f")   # {len(car_stops)} di {n_delivery} fermate accessibili alle auto")

    # bacino
    coords = [(fac[s][2], fac[s][3]) for s in stops]
    n_tot = n_catch = 0
    with gzip.open(SC / "planExternalProcessed_lowerCase.xml.gz", "rt", encoding="utf-8") as f:
        inp = found = False
        for line in f:
            if "<person " in line:
                inp, found = True, False
                n_tot += 1
            elif inp and not found and "<activity" in line and 'type="home' in line:
                m = re.search(r'x="([^"]+)" y="([^"]+)"', line)
                if m:
                    found = True
                    x, y = float(m.group(1)), float(m.group(2))
                    if any(math.hypot(x - sx, y - sy) <= WALK_BUFFER_M for sx, sy in coords):
                        n_catch += 1
            elif "</person>" in line:
                inp = False
    pop = n_catch / SAMPLE_RATE
    n_real = pop * PARCELS_PER_PERSON_DAY
    n_sim = n_real * SAMPLE_RATE
    print(f"\nbacino {WALK_BUFFER_M:.0f} m: {n_catch} persone su {n_tot} nel campione "
          f"-> {pop:.0f} reali")
    print(f"N_real = {n_real:.0f} colli/giorno | n_freight_units_sim = {n_sim:.0f}")

    print(f"\n{'alpha':>6} {'colli/corsa':>12} {'dwell/fermata':>14} "
          + " ".join(f"{'van_'+w:>10}" for w in WEIGHTS))
    for a in ALPHAS:
        per_trip = a * n_real / F
        u = per_trip / n_delivery
        dw = DWELL_FIXED_S + DWELL_PER_UNIT_S * u
        tours = " ".join(f"{math.ceil((1-a)*n_sim/c_van(w)) if (1-a)*n_sim > 0 else 0:>10}"
                         for w in WEIGHTS.values())
        print(f"{a:6.2f} {per_trip:12.1f} {dw:13.1f}s {tours}")

    print(f"\nvincoli a alpha=1 (F={F}):")
    per_trip = n_real / F
    print(f"  C3 spazio: {per_trip:.0f} colli/corsa vs N_max {BUS_FREIGHT_NMAX} "
          f"-> {'OK' if per_trip <= BUS_FREIGHT_NMAX else 'INFATTIBILE'}, "
          f"margine {BUS_FREIGHT_NMAX - per_trip:.0f}")
    for w_name, w in WEIGHTS.items():
        kg = per_trip * w
        print(f"  C1 peso {w_name:>6} ({w:.0f} kg): {kg:.0f} kg vs {BUS_FREIGHT_CAPACITY_KG:.0f} "
              f"-> {'OK' if kg <= BUS_FREIGHT_CAPACITY_KG else 'INFATTIBILE sopra alpha ' + f'{BUS_FREIGHT_CAPACITY_KG/kg:.2f}'}")

    out_v = SC / f"line{line_id}_{hub_link}_vehicle_ids.txt"
    out_l = SC / f"line{line_id}_{hub_link}_route_links.txt"
    out_v.write_text("\n".join(sorted(set(veh))) + "\n", encoding="utf-8")
    out_l.write_text("\n".join(links) + "\n", encoding="utf-8")
    print(f"\nscritti {out_v.name} ({len(set(veh))} id) e {out_l.name} ({len(links)} link)")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
