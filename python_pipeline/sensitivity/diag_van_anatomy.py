"""
Perche' un giro di furgone costa MENO quando i furgoni sono di piu': l'anatomia.

L'OSSERVAZIONE. Campagna numero pacchi, 27/8: la CO2 di un giro nel baseline cala
al crescere della flotta — 12,76 kg con 3 furgoni, 12,59 con 5, 12,07 con 9, con i
link in stallo gia' esclusi. E' il contrario di quello che ci si aspetta.

COSA E' GIA' STATO ESCLUSO, misurandolo e non ragionandoci:
  - NON e' un furgone piu' leggero. La massa di valutazione e' tare + meta' del
    carico con C_van(peso) parcels, e C_van non dipende da N: 2.500 kg in tutti e
    tre i casi. Anche l'ultimo giro, che nella realta' sarebbe parziale, e' caricato
    pieno (registro conservativo, "Every tour dispatched full").
  - NON e' il difetto della griglia di agosto. Verificato sui piani veri: in ogni
    cella lo scenario tiene i PRIMI k slot del baseline, quindi dentro una cella
    baseline e scenario mandano i furgoni alla stessa ora.

RESTANO QUATTRO CANDIDATI, e solo uno e' un difetto vero. Questo script li separa
misurando l'anatomia di ogni singolo giro, non solo il suo costo:

  (a) L'ORA DI PARTENZA. La griglia copre sempre 07:00-09:00, quindi con 3 furgoni
      due su tre stanno ai bordi della finestra e con 9 solo due su nove. Se il
      costo dipende dall'ora, le tre medie campionano la stessa curva a risoluzioni
      diverse.  -> FIRMA: a parita' di ora di partenza, stesso costo.

  (b) GIRI CHE NON FINISCONO. Se un furgone resta bloccato e la simulazione finisce
      prima di lui, i suoi eventi si fermano a meta': meno link, meno secondi, meno
      CO2 — e la media per giro SCENDE. E' lo stesso difetto che fece scartare la
      linea 99470 (solo 20 delle 53 corse arrivavano in fondo). Sarebbe un difetto
      vero e cambierebbe i numeri.  -> FIRMA: meno link e meno km ad alto N.

  (c) PERCORSI DIVERSI. Se il router manda i furgoni su strade diverse, cambiano
      sia i km sia il traffico incontrato.  -> FIRMA: insiemi di link diversi.

  (d) VELOCITA' DIVERSE sugli STESSI link — l'unico caso in cui "piu' furgoni
      cambiano il traffico" e' davvero la spiegazione, ma allora dovrebbe andare
      nella direzione opposta (piu' lenti, non piu' veloci).
      -> FIRMA: stessi link, stessi km, velocita' che CALA e costo che SALE.

Nessuna run MATSim: rilegge i tre baseline gia' su disco, uno alla volta.

    python python_pipeline/sensitivity/diag_van_anatomy.py
"""

from __future__ import annotations

import gzip
import re
from pathlib import Path

import _common as C
from corridor_metrics import load_corridor_links
from parameters import VAN_ID_PREFIX, VAN_LOAD_FACTOR, VAN_TARE_KG, WEIGHT_REGIMES, c_van
from parse_events import parse_events
from scenario_presets import get_preset
from term_b import co2_van_fleet

WEIGHT, CONGESTION, SEEDS = "medium", "peak", [4711, 9876]

CASES = [
    (235, C.OUTPUT_ROOT / "ipft_rotterdam_sens_rn235_runs",
     C.OUTPUT_ROOT / "ipft_rotterdam_sens_rn235_plans"
     / "warmplans_rn235_alpha000_peak_medium_3of3.xml.gz"),
    (470, C.BLOCKING_RUNS,
     C.OUTPUT_ROOT / "ipft_rotterdam_warm_plans"
     / "warmplans_alpha000_peak_medium.xml.gz"),
    (940, C.OUTPUT_ROOT / "ipft_rotterdam_sens_rn940_runs",
     C.OUTPUT_ROOT / "ipft_rotterdam_sens_rn940_plans"
     / "warmplans_rn940_alpha000_peak_medium_9of9.xml.gz"),
]

_PAT_VAN = re.compile(rb'<person id="(backup_van_\d+)"')
_PAT_DEP = re.compile(rb'end_time="(\d\d:\d\d:\d\d)"')


def departures(plans: Path) -> dict[str, str]:
    out, cur = {}, None
    with gzip.open(plans, "rb") as f:
        for line in f:
            m = _PAT_VAN.search(line)
            if m:
                cur = m.group(1).decode()
                continue
            if cur:
                d = _PAT_DEP.search(line)
                if d:
                    out[cur] = d.group(1).decode()
                    cur = None
    return out


def main() -> None:
    C.use_project_root()
    preset = get_preset("rotterdam")
    w = WEIGHT_REGIMES[WEIGHT]
    cvan = c_van(w)
    van_mass = VAN_TARE_KG + VAN_LOAD_FACTOR * cvan * w
    keep = frozenset(load_corridor_links(preset.corridor_links_file)
                     | load_corridor_links(preset.bus_stop_links_file))
    dead = load_corridor_links(str(C.DEADLOCK_LINKS[CONGESTION]))
    terminal = preset.terminal_link
    print(f"van {van_mass:.0f} kg (identico in tutti i casi), {len(dead)} link in "
          f"stallo esclusi, link finale atteso {terminal}\n")

    rows = []
    for n, runs_dir, plans in CASES:
        if not plans.exists():
            print(f"[skip] N={n}: piani mancanti"); continue
        dep = departures(plans)
        for seed in SEEDS:
            ev = C.find_events(runs_dir, f"alpha000_{CONGESTION}_{WEIGHT}_seed{seed}")
            if not ev:
                print(f"[skip] N={n} seed {seed}: eventi mancanti"); continue
            print(f"[parse] N={n} seed {seed}", flush=True)
            vm, _ = parse_events(ev, preset.network_file, verbose=False,
                                 bus_prefixes=preset.transit_prefixes,
                                 pax_bus_ids=preset.term_c_bus_ids,
                                 keep_link_ids=keep)
            vans = sorted(v for v in vm["vehicle_id"].unique()
                          if str(v).startswith(VAN_ID_PREFIX))
            for vid in vans:
                d = vm[vm["vehicle_id"] == vid].sort_values("time_entered_s")
                dx = d[~d["link_id"].isin(dead)]        # come fa Term B
                km = float((dx["v_mean_ms"] * dx["travel_time_s"]).sum()) / 1000.0
                sec = float(dx["travel_time_s"].sum())
                rows.append(dict(
                    n=n, seed=seed, van=vid, departure=dep.get(vid, "?"),
                    co2_kg=co2_van_fleet(vm, [vid], van_mass, exclude_links=dead),
                    n_links=int(len(dx)), n_links_all=int(len(d)), km=km, sec=sec,
                    kmh=(km / sec * 3600.0) if sec else float("nan"),
                    last_link=str(d["link_id"].iloc[-1]) if len(d) else "",
                    reached_terminal=bool((d["link_id"] == terminal).any()),
                    t_first=float(d["time_entered_s"].iloc[0]) if len(d) else float("nan"),
                    t_last=float(d["time_entered_s"].iloc[-1]) if len(d) else float("nan"),
                ))
            del vm

    if not rows:
        raise SystemExit("nessuna cella leggibile")

    import pandas as pd
    df = pd.DataFrame(rows)
    out = C.ROOT / "output" / "diag_van_anatomy.csv"
    df.to_csv(out, index=False)
    pd.set_option("display.width", 220)

    print("\n=== ogni giro, per intero ===")
    print(df[["n", "seed", "departure", "n_links", "km", "sec", "kmh", "co2_kg",
              "reached_terminal"]].round(3).to_string(index=False))

    print("\n=== (b) I GIRI FINISCONO? ===")
    for n, g in df.groupby("n"):
        done = int(g["reached_terminal"].sum())
        print(f"  N={n:4d}  {done}/{len(g)} arrivano al capolinea   "
              f"link/giro {g['n_links'].mean():6.1f}   km/giro {g['km'].mean():6.2f}   "
              f"s/giro {g['sec'].mean():7.0f}")
    print("  -> se link e km CALANO al crescere di N, e' (b): giri troncati, difetto vero.")

    print("\n=== (a) STESSA ORA DI PARTENZA, FLOTTE DIVERSE ===")
    shared = sorted(t for t, g in df.groupby("departure")
                    if g["n"].nunique() == df["n"].nunique())
    for t in shared:
        s = df[df.departure == t].groupby("n")[["co2_kg", "km", "kmh"]].mean()
        txt = "  ".join(f"N={i}: {r.co2_kg:6.3f} kg / {r.km:5.2f} km / {r.kmh:5.2f} km/h"
                        for i, r in s.iterrows())
        print(f"  {t}   {txt}")
    print("  -> costo piatto lungo la riga = (a) l'ora, non il numero di furgoni.")

    print("\n=== (c) STESSI PERCORSI? ===")
    for n, g in df.groupby("n"):
        print(f"  N={n:4d}  link per giro: {sorted(g['n_links'].unique())}   "
              f"ultimo link visto: {sorted(g['last_link'].unique())[:4]}")

    print("\n=== (d) VELOCITA' ===")
    for n, g in df.groupby("n"):
        print(f"  N={n:4d}  media {g['kmh'].mean():5.2f} km/h   "
              f"per giro {[round(x, 2) for x in sorted(g['kmh'])]}")
    print("  -> se la velocita' SALE con N, non e' congestione: la congestione rallenta.")

    print(f"\nsaved -> {out}")


if __name__ == "__main__":
    main()
