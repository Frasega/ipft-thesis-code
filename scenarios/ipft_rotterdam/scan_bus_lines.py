"""Classifica tutte le linee BUS dello schedule come candidati corridoio H->B.

Criteri: n. fermate (granularita' consegna), partenze/giorno per direzione (F),
lunghezza percorso (km van evitati). Mostra anche i capolinea per identificare
la linea umanamente.
"""
import gzip
import re
from pathlib import Path

HERE = Path(__file__).parent

with gzip.open(HERE / "ptSchedule36Hour.xml.gz", "rt", encoding="utf-8") as f:
    sched = f.read()

# stopFacility id -> name
fac = {}
for m in re.finditer(r"<stopFacility ([^>]+)>", sched):
    attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
    if "id" in attrs:
        fac[attrs["id"]] = attrs.get("name", "?")

# lunghezze link dalla rete (per la lunghezza percorso)
link_len = {}
with gzip.open(HERE / "networkWithRideAndBike.xml.gz", "rt", encoding="utf-8") as f:
    for line in f:
        if "<link " in line:
            m = re.search(r'<link id="([^"]+)"[^>]*length="([^"]+)"', line)
            if m:
                link_len[m.group(1)] = float(m.group(2))

rows = []
for lm in re.finditer(r'<transitLine id="([^"]+)"[^>]*>(.*?)</transitLine>', sched, re.S):
    lid, body = lm.group(1), lm.group(2)
    routes = re.findall(r"<transitRoute id=\"([^\"]+)\">(.*?)</transitRoute>", body, re.S)
    if not routes:
        continue
    modes = set(re.findall(r"<transportMode>([^<]+)</transportMode>", body))
    if modes != {"bus"}:
        continue

    # raggruppa per direzione usando la prima fermata; tieni la direzione migliore
    best = None
    for rid, rbody in routes:
        stops = re.findall(r'<stop refId="([^"]+)"', rbody)
        ndeps = rbody.count("<departure ")
        links = re.findall(r'<link refId="([^"]+)"/>', rbody)
        length_km = sum(link_len.get(l, 0.0) for l in links) / 1000.0
        key = stops[0] if stops else "?"
        cand = dict(first=stops[0], last=stops[-1], n_stops=len(stops),
                    deps=ndeps, km=length_km)
        if best is None:
            best = {}
        if key not in best:
            best[key] = dict(cand, deps=0)
        b = best[key]
        b["deps"] += ndeps
        if cand["n_stops"] > b["n_stops"]:
            b.update(n_stops=cand["n_stops"], km=cand["km"],
                     first=cand["first"], last=cand["last"])

    # direzione con piu' partenze
    d = max(best.values(), key=lambda x: x["deps"])
    rows.append(dict(line=lid, n_stops=d["n_stops"], deps_dir=d["deps"],
                     km=d["km"],
                     from_=fac.get(d["first"], "?"), to=fac.get(d["last"], "?")))

# punteggio semplice: fermate * partenze * km (proxy di pacchi consegnabili x copertura)
for r in rows:
    r["score"] = r["n_stops"] * r["deps_dir"] * r["km"]

rows.sort(key=lambda r: r["score"], reverse=True)
print(f"{'line':>7} {'stops':>5} {'dep/dir':>7} {'km':>6}  route")
for r in rows[:20]:
    print(f"{r['line']:>7} {r['n_stops']:>5} {r['deps_dir']:>7} {r['km']:>6.1f}  "
          f"{r['from_'][:38]} -> {r['to'][:38]}")

l44 = [r for r in rows if r["line"] == "99437"]
if l44:
    print(f"\nline 44 (99437) rank: {rows.index(l44[0]) + 1} of {len(rows)} bus lines")
    print(l44[0])

# ── Vista filtrata: corridoi URBANI di Rotterdam ──────────────────────────
# Criteri IPFT last-mile: entrambi i capolinea a Rotterdam, alta frequenza.
# Punteggio senza il bias dei km: fermate x partenze (capacita' di consegna),
# km mostrati come info.
urban = [r for r in rows
         if "Rotterdam" in r["from_"] and "Rotterdam" in r["to"]
         and r["deps_dir"] >= 50]
urban.sort(key=lambda r: r["n_stops"] * r["deps_dir"], reverse=True)
print(f"\n=== URBAN ROTTERDAM candidates (both termini in Rotterdam, >=50 dep/dir) ===")
print(f"{'line':>7} {'stops':>5} {'dep/dir':>7} {'km':>6}  route")
for r in urban[:15]:
    print(f"{r['line']:>7} {r['n_stops']:>5} {r['deps_dir']:>7} {r['km']:>6.1f}  "
          f"{r['from_'][:38]} -> {r['to'][:38]}")
