"""
Il patch al tipo van e' inerte anche su EVENTI VERI, non solo su dati sintetici.

test_van_type_plumbing.py confronta il term_b attuale con dei valori d'oro su
DataFrame costruiti a mano. Sono buoni, ma non sono un file di eventi: le velocita'
sono estratte da una uniforme, i tempi di percorrenza pure, e nessuna sequenza di
link e' quella che un router ha davvero prodotto. Questo file chiude quel divario
usando gli eventi del sandbox — 21 MB compressi contro i gigabyte di Rotterdam,
quindi due minuti e poche centinaia di MB invece di mezz'ora e diversi GB.

COME: gli eventi si parsano UNA volta, poi lo stesso DataFrame viene dato al
term_b committato (estratto da git al commit di riferimento, prima che il tipo van
esistesse) e a quello nell'albero di lavoro. Ogni campo restituito deve coincidere.
E' lo stesso patto di layer3_baseline_variants: se la configurazione non-swept
riproduce il numero pubblicato, allora il parse e ogni costante dietro combaciano.

Salta con esito positivo se il sandbox non e' piu' su disco (le run toy sono state
cancellate una volta per fare spazio) o se git non e' disponibile: e' un controllo
in piu', non un cancello.

Run:  python python_pipeline/test_term_b_real_events.py
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

PIPE = Path(__file__).resolve().parent
ROOT = PIPE.parent
sys.path.insert(0, str(PIPE))

# Il commit PRIMA che il tipo van esistesse. Fisso, non HEAD: una volta committato
# il lavoro, HEAD conterrebbe la versione nuova e il confronto diventerebbe vuoto.
REFERENCE_COMMIT = "26e775e"

from scenario_presets import OUTPUT_ROOT  # noqa: E402

TOY_RUNS = OUTPUT_ROOT / "ipft_toy_warm_runs"


def _skip(reason: str) -> None:
    print(f"[skip] {reason}")
    print("       (controllo facoltativo: e' un rinforzo di "
          "test_van_type_plumbing.py, non un cancello)")
    sys.exit(0)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> None:
    # ── il term_b di riferimento, dal commit fissato ───────────────────────
    try:
        old_src = subprocess.run(
            ["git", "show", f"{REFERENCE_COMMIT}:python_pipeline/term_b.py"],
            cwd=str(ROOT), capture_output=True, text=True, check=True).stdout
    except Exception as exc:
        _skip(f"git show {REFERENCE_COMMIT} non disponibile ({exc})")
    if not old_src.strip():
        _skip(f"{REFERENCE_COMMIT} non contiene term_b.py")

    # ── una cella di baseline del sandbox ──────────────────────────────────
    cells = sorted(TOY_RUNS.glob("alpha000_*_seed4711")) if TOY_RUNS.is_dir() else []
    events = next((e for c in cells for e in c.glob("*output_events.xml*")), None)
    if events is None:
        _skip(f"nessun evento sandbox sotto {TOY_RUNS}")

    from parse_events import parse_events
    from parameters import WEIGHT_REGIMES
    from scenario_presets import get_preset

    preset = get_preset("toy")
    network = str(ROOT / preset.network_file)
    print(f"parso una volta sola: {events.parent.name}/{events.name}")
    vmean, _ = parse_events(str(events), network, verbose=False,
                            bus_prefixes=preset.transit_prefixes)
    n_van = vmean.loc[vmean["vehicle_id"].str.startswith("backup_van_"),
                      "vehicle_id"].nunique()
    print(f"  {len(vmean):,} record, {n_van} furgoni nel run")
    if n_van == 0:
        _skip("questa cella non contiene furgoni: term_b userebbe il ramo proxy")

    with tempfile.TemporaryDirectory() as td:
        old_path = Path(td) / "term_b_reference.py"
        old_path.write_text(old_src, encoding="utf-8")
        old = _load("term_b_reference", old_path)
        new = _load("term_b_current", PIPE / "term_b.py")

        # Lo scenario e' il baseline stesso: il confronto e' fra due versioni del
        # codice sugli stessi identici dati, non fra due mondi.
        failures = 0
        print("\nstesso DataFrame, due versioni di term_b:")
        for regime, w in WEIGHT_REGIMES.items():
            for alpha in (0.0, 0.5, 1.0):
                kw = dict(baseline_vmean_df=vmean, scenario_vmean_df=vmean,
                          alpha=alpha, n_total_vans=preset.n_freight_units_sim,
                          van_payload_kg=w, n_pickup_stops=preset.n_pickup_stops)
                a, b = old.compute_term_b(**kw), new.compute_term_b(**kw)
                diff = [k for k in a
                        if not (a[k] == b[k]
                                or (isinstance(a[k], float) and a[k] != a[k]
                                    and b[k] != b[k]))]
                ok = not diff
                failures += not ok
                print(f"  [{'ok' if ok else 'FAIL'}] {regime:<6} alpha={alpha:<4} "
                      f"term_b = {b['term_b_kg']:14.6f}"
                      + ("" if ok else f"   differiscono: {diff}"))

    print()
    print("il patch e' inerte anche su eventi veri" if not failures
          else f"{failures} CELLE DIVERSE — il patch ha mosso qualcosa")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
