"""
Euro-class emission factors for the van and the bus. NOT from HBEFA, and not
from the fuel model either.

WHY THIS FILE EXISTS AT ALL

The background traffic gets NOx for free: it is `pass. car` in the HBEFA tables,
which carry the full pollutant set, so recompute_link_co2.py just reads a
different Component column. The van and the bus cannot do that, for two
independent reasons:

  1. In those same tables, `LCV`, `urban bus`, `coach` and `motorcycle` carry
     ONLY CO2(total). There is no NOx row to read.
  2. Van and bus emissions in this model do not come from HBEFA at all. They
     come from longitudinal dynamics (emission_formula.py): tractive energy ->
     litres of diesel -> kg CO2. NOx is NOT proportional to fuel - it depends on
     combustion temperature, EGR, and whether the SCR is above light-off - so
     scaling NOx off litres would be indefensible and trivially attacked.

WHY CO2 USES PHYSICS AND NOx USES A MEASURED CURVE

This asymmetry is the first thing a careful reader asks about, so it is stated
here rather than buried. CO2 is proportional to the fuel burned: one litre of
diesel gives 2.65 kg of CO2 whatever the engine, its age or its aftertreatment.
A tractive-energy -> litres -> CO2 chain therefore computes it exactly, which is
why the thesis uses it. NOx is not proportional to fuel: two buses burning
identical fuel can differ tenfold depending on combustion temperature, EGR and
SCR thermal state. That information is not in this model and cannot be, so it
has to come from measurements. Using the fuel model for NOx would be the
indefensible move, not the other way round.

The cost of mixing the two is that they are not guaranteed to agree about how
much idling there is. That is checkable, and checked: the same source publishes
ENERGY CONSUMPTION (EC, MJ/km) on the same axes, so the freight's CO2 can be
computed empirically and compared against what the physics model says. See
`bus_ec()` and PIANO.md 4.2quater section D.

THE SOURCE, AND WHY IT IS TIER 3 AND NOT TIER 2

EMEP/EEA air pollutant emission inventory guidebook 2023 (Update 2024), chapter
1.A.3.b.i-iv Road transport. The two tiers are NOT interchangeable here:

  * Tier 2 gives one aggregated g/veh-km per technology, with NO speed
    dependence - "average European emission factors determined using the Tier 3
    methodology by using typical values for driving speeds [...] highway-rural-
    urban mode mix". Pasting one of those into a speed curve would make the
    congestion response of this model fake.
  * Tier 3 is the speed-dependent approach, and its coefficients are Appendix 4.

Equation (25) of that chapter, stated there as generic "for all vehicle classes
and pollutants":

    EF = (Alpha*V^2 + Beta*V + Gamma + Delta/V)
       / (Epsilon*V^2 + Zeta*V + Eta) * (1 - RF)

VERIFIED, NOT ASSUMED. `validate_equation_25()` below re-evaluates that formula
against the workbook's own pre-computed EF column on every row that carries one -
53,277 rows, all categories and pollutants - and the worst relative error is
2e-14. Run it with `python python_pipeline/euro_factors.py`.

WHAT THE VAN AND THE BUS EACH NEED, AND WHY THEY DIFFER

  Van, g/km.  The van scenario removes tours from the road, so the van kilometre
              delta is real and large. The factor is evaluated link by link at
              that link's own mean speed, so van NOx responds to the congestion
              the scenario creates.

  Bus, g/km at two speeds and two loads. Term C is a marginal quantity: the bus
              drives the SAME F trips on the SAME links whether or not it carries
              freight, so delta-bus-km is exactly ZERO and a plain g/km factor
              would return exactly zero. What actually changes is (a) the mass it
              carries and (b) how long it stands. Both are in this table:
              (a) is the Load axis, published at 0, 0.5 and 1;
              (b) is the SPEED axis - a bus that stands longer on the same route
                  simply has a lower mean speed, and these curves are built from
                  measurements that INCLUDE standing at stops and traffic lights.
              So Term C in NOx is one expression, from one source:

                  dNOx = [EF(v_scenario, load+dLoad) - EF(v_baseline, load)] * km

              This is why there is no g/h idle factor here. Adding one on top of
              a real-world g/km curve would count the idling twice, and no such
              factor is published for buses anyway.

THE ONE ASSUMPTION TO DECLARE

That the extra freight dwell behaves like the idling already contained in the
source data at that mean speed. Declarable and defensible; an invented g/h was
neither.

Usage:
    from euro_factors import van_nox, bus_nox, bus_ec, FLEET_SCENARIOS
    van_nox("Euro 6 d/e").at(17.5)            # g/km
    bus_nox("Euro VI D/E").at(17.5, 0.101)    # g/km at 10.1% load
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from pathlib import Path

WORKBOOK = (Path(__file__).resolve().parent.parent
            / "scenarios" / "ipft_rotterdam" / "EMEP_EEA_Appendix4_EF.xlsx")

SOURCE = ("EMEP/EEA air pollutant emission inventory guidebook 2023 (Update 2024), "
          "1.A.3.b.i-iv Road transport, Appendix 4, equation (25)")


class FactorNotSourced(RuntimeError):
    """Raised when a factor is requested that the workbook does not contain."""


# ── The vehicles this thesis prices, and why these rows and not others ──────
#
# VAN. The delivery van of the model is a Ford Transit Custom L2, ~1,950 kg kerb
# and >1,760 kg reference mass, i.e. an N1 Class III. The guidebook treats diesel
# light commercial vehicles as passenger cars: speed-dependent hot factors, no
# load or slope axis.
VAN_CATEGORY, VAN_SEGMENT = "Light Commercial Vehicles", "N1-III"

# BUS. MATSim runs an 18 m articulated on line 44, so the articulated row is the
# one that matches the vehicle actually simulated. The thesis convention is to
# price the vehicle the simulation moves, which is also the conservative choice
# here: a larger nominal capacity would make the freight a smaller share of the
# load and the bus look cheaper.
BUS_CATEGORY, BUS_SEGMENT = "Buses", "Urban Buses Articulated >18 t"

# Road slope: 0 %. The workbook publishes seven gradients (-6 % .. +6 %) and at
# urban speeds the spread between them is larger than the effect being measured,
# so the flat-road row is used and the choice is declared rather than averaged.
SLOPE = 0

# Technology shares within a Euro class. Euro V heavy duty splits into SCR and
# EGR, and the guidebook states the split: "approximately 75 % of Euro V heavy-
# duty vehicles are equipped with SCR, the rest being equipped with EGR".
# Euro VI has a single technology. Euro 6 d/e vans list three (DPF, DPF+SCR,
# LNT+DPF); they are checked for agreement at import rather than assumed equal.
TECH_SHARES: dict[tuple[str, str], dict[str, float]] = {
    (BUS_CATEGORY, "Euro V"): {"SCR": 0.75, "EGR": 0.25},
}

# The two worlds the thesis reports, plus the electric column handled outside
# this module (bus tailpipe NOx is exactly zero there, so no factor is needed).
FLEET_SCENARIOS: dict[str, dict[str, str]] = {
    "euro5": {"van": "Euro 5", "bus": "Euro V"},
    "euro6": {"van": "Euro 6 d/e", "bus": "Euro VI D/E"},
}


# ── Measured idle NOx: the one thing the speed curves cannot give ──────────
#
# Appendix 4 is entirely g/km against speed. Counted: 16,059 bus rows, nine
# pollutants, and not one idle row. There is no g/h anywhere in the source.
#
# The extra dwell this operation creates used to be priced by reading the speed
# curve at the lower journey speed the dwell produces, bracketed between two
# readings because neither was right. That bracket is now replaced by a
# measurement, which is what the CO2 side has always had (an idle litres/second
# rate) and the NOx side did not.
#
# SOURCE. Leach, Peckham & Hammond (2020), "Identifying NOx Hotspots in
# Transient Urban Driving of Two Diesel Buses and a Diesel Car", Atmosphere
# 11(4), 355, Table 2. Two buses in full passenger service in Oxford, NOx
# measured at 10 ms resolution. The mass rate is derived from the measured
# concentration through the UNECE R49 relation m = u * c * q_exh, with q_exh
# estimated from displacement, idle rpm and volumetric efficiency. Backing
# their own published figures out of that formula returns ~764 rpm for both
# vehicles, so the pair is internally consistent.
#
# WHAT THIS IS NOT, stated so it can be attacked properly. It is ONE bus per
# class, not a fleet average, and the concentration is measured while the mass
# flow is estimated. Their engines are 4.8 L (Euro V) and 5.1 L (Euro VI); the
# vehicle simulated here is an 18 m articulated, which carries a larger one.
# q_exh is LINEAR in displacement, so a bigger engine idles dirtier and the true
# cost for this bus is HIGHER than these figures. Using them unscaled is
# therefore the conservative choice for the finding they support, which is that
# the bus side outweighs the van saving in NOx. The Euro V vehicle is also a
# hybrid, which is recorded here because it affects what its idle point is.
#
# CROSS-CHECK, independent of the above. EPA Emission Facts EPA420-F-08-026
# (2008), Table 2, gives 61.1 g/h for the US in-use urban diesel bus fleet, a
# pre-SCR fleet whose running rate (14.793 g/mile = 9.19 g/km) sits alongside
# this model's Euro V curve (8.14 g/km at 21.5 km/h). Different continent,
# different model family (MOBILE6.2, not COPERT), and it lands 8 % from the
# Euro V figure below.
BUS_IDLE_NOX_G_PER_H: dict[str, float] = {
    "Euro V": 66.0,
    "Euro VI D/E": 20.0,
}

IDLE_NOX_SOURCE = ("Leach, Peckham and Hammond (2020), Atmosphere 11(4), 355, "
                   "Table 2 - idling NOx mass emissions estimate")


def bus_idle_nox_g_per_h(euro: str) -> float:
    """Measured idle NOx [g/h] for a bus standing with the engine running."""
    try:
        return BUS_IDLE_NOX_G_PER_H[euro]
    except KeyError:
        raise FactorNotSourced(
            f"no measured idle NOx rate for '{euro}'. Sourced: "
            f"{sorted(BUS_IDLE_NOX_G_PER_H)}. Do not interpolate one - idle NOx "
            f"is set by the SCR thermal state, not by the Euro number, so a "
            f"value between two classes is not a value at all.") from None


# ── One published curve ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class CopertEF:
    """One row of Appendix 4: equation (25) plus the speed range it is valid in.

    Outside [v_min, v_max] the value is CLAMPED to the nearest endpoint rather
    than extrapolated. A fitted curve is only meaningful over the speeds its
    source states, and the Delta/V term makes it diverge as V -> 0, which is
    exactly the range a congested corridor spends its time in. Clamping is
    recorded, not hidden: `n_clamped` counts how often it happened, so a table
    caption can say how much of the fleet fell outside the published range.
    """
    alpha: float
    beta: float
    gamma: float
    delta: float
    epsilon: float
    zita: float
    hta: float
    reduction_factor: float
    v_min: float
    v_max: float
    unit: str
    label: str
    _clamped: list = field(default_factory=list, compare=False, repr=False)

    def at(self, v_kmh: float) -> float:
        v = v_kmh
        if v <= self.v_min:
            self._clamped.append(v_kmh)
            v = self.v_min
        elif v >= self.v_max:
            self._clamped.append(v_kmh)
            v = self.v_max
        num = self.alpha * v * v + self.beta * v + self.gamma + self.delta / v
        den = self.epsilon * v * v + self.zita * v + self.hta
        if den == 0:
            raise FactorNotSourced(f"{self.label}: zero denominator at V={v}")
        return num / den * (1.0 - self.reduction_factor)

    @property
    def n_clamped(self) -> int:
        return len(self._clamped)

    @property
    def lowest_valid_speed(self) -> float:
        return self.v_min

    def cite(self) -> str:
        return f"{SOURCE} - {self.label}, valid {self.v_min}-{self.v_max} km/h"


@dataclass(frozen=True)
class MixedEF:
    """Share-weighted combination of curves, for a Euro class with more than one
    aftertreatment technology. Shares must sum to 1 and come from the source."""
    parts: tuple[tuple[float, CopertEF], ...]
    label: str

    def at(self, v_kmh: float) -> float:
        return sum(share * ef.at(v_kmh) for share, ef in self.parts)

    @property
    def n_clamped(self) -> int:
        return sum(ef.n_clamped for _, ef in self.parts)

    @property
    def lowest_valid_speed(self) -> float:
        return max(ef.lowest_valid_speed for _, ef in self.parts)

    def cite(self) -> str:
        return " + ".join(f"{s:.0%} {ef.cite()}" for s, ef in self.parts)


@dataclass(frozen=True)
class LoadedEF:
    """A bus curve on both axes: speed, and load between the published points.

    The workbook gives each bus curve at load 0, 0.5 and 1. Interpolation
    between them is LINEAR, which is an assumption of ours and not of the
    source - it is small: the slope over 0->0.5 and over 0.5->1 differ by 8 %
    (Euro V) and 13 % (Euro VI D/E). Outside [0, 1] it raises rather than
    extrapolating.
    """
    by_load: tuple[tuple[float, object], ...]   # sorted (load, curve)
    label: str

    def at(self, v_kmh: float, load: float) -> float:
        pts = self.by_load
        if not (pts[0][0] <= load <= pts[-1][0]):
            raise FactorNotSourced(
                f"{self.label}: load {load} outside the published range "
                f"[{pts[0][0]}, {pts[-1][0]}]")
        for (l0, c0), (l1, c1) in zip(pts, pts[1:]):
            if l0 <= load <= l1:
                w = 0.0 if l1 == l0 else (load - l0) / (l1 - l0)
                return (1 - w) * c0.at(v_kmh) + w * c1.at(v_kmh)
        raise FactorNotSourced(f"{self.label}: no bracket for load {load}")

    @property
    def n_clamped(self) -> int:
        return sum(c.n_clamped for _, c in self.by_load)

    @property
    def lowest_valid_speed(self) -> float:
        """The strictest floor across the load curves: below it every one of them
        is clamped, so searching there is meaningless."""
        return max(c.lowest_valid_speed for _, c in self.by_load)

    def cite(self) -> str:
        return f"{self.by_load[0][1].cite()} (loads {[l for l, _ in self.by_load]})"


# ── Reading the workbook ────────────────────────────────────────────────────

_COLS = ("Category", "Fuel", "Segment", "Euro Standard", "Technology", "Pollutant",
         "Road Slope", "Load", "Min Speed [km/h]", "Max Speed [km/h]",
         "Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zita", "Hta",
         "Reduction Factor [%]")

UNITS = {"NOx": "g/km", "EC": "MJ/km", "CO": "g/km", "VOC": "g/km",
         "PM Exhaust": "g/km"}


@functools.lru_cache(maxsize=1)
def _rows() -> tuple[dict, ...]:
    """Every hot-emission row, read once per process.

    The workbook is ~11 MB, so this is deliberately cached: a surface driver
    calls into Term B and Term C dozens of times in one process and must not
    re-read it each time.
    """
    try:
        import openpyxl
    except ImportError as e:                                   # pragma: no cover
        raise FactorNotSourced(
            "openpyxl is needed to read the EMEP/EEA Appendix 4 workbook") from e
    if not WORKBOOK.exists():
        raise FactorNotSourced(
            f"{WORKBOOK} not found. It is the free public Appendix 4 spreadsheet "
            f"of the EMEP/EEA Guidebook 2023; no licence is required.")
    wb = openpyxl.load_workbook(WORKBOOK, read_only=True, data_only=True)
    ws = wb["HOT_EMISSIONS_PARAMETERS"]
    it = ws.iter_rows(values_only=True)
    hdr = next(it)
    idx = {name: i for i, name in enumerate(hdr) if name in _COLS}
    ef_col = next((i for i, n in enumerate(hdr)
                   if n and str(n).startswith("EF [g/km]")), None)
    v_col = next((i for i, n in enumerate(hdr) if str(n) == "80"), None)
    out = []
    for r in it:
        if r[idx["Category"]] is None:
            continue
        d = {c: r[idx[c]] for c in _COLS if c in idx}
        d["_ef_published"] = r[ef_col] if ef_col is not None else None
        d["_ef_speed"] = r[v_col] if v_col is not None else None
        out.append(d)
    wb.close()
    return tuple(out)


def _num(x) -> float:
    return float(x) if x not in (None, "") else 0.0


def _curve_from(row: dict, pollutant: str) -> CopertEF:
    label = (f"{row['Category']} / {row['Segment']} / {row['Euro Standard']}"
             f" / {row['Technology']} / {pollutant}"
             + (f" / load {row['Load']}" if row["Load"] not in (None, "") else ""))
    return CopertEF(
        alpha=_num(row["Alpha"]), beta=_num(row["Beta"]), gamma=_num(row["Gamma"]),
        delta=_num(row["Delta"]), epsilon=_num(row["Epsilon"]),
        zita=_num(row["Zita"]), hta=_num(row["Hta"]),
        reduction_factor=_num(row["Reduction Factor [%]"]),
        v_min=_num(row["Min Speed [km/h]"]), v_max=_num(row["Max Speed [km/h]"]),
        unit=UNITS.get(pollutant, "?"), label=label)


def _select(category: str, segment: str, euro: str, pollutant: str,
            load=None, slope=None) -> list[dict]:
    hits = [r for r in _rows()
            if r["Category"] == category and r["Fuel"] == "Diesel"
            and str(r["Segment"]) == segment
            and str(r["Euro Standard"]) == euro
            and str(r["Pollutant"]) == pollutant
            and (slope is None or r["Road Slope"] == slope)
            and (load is None or r["Load"] == load)]
    if not hits:
        raise FactorNotSourced(
            f"no Appendix 4 row for {category} / {segment} / {euro} / {pollutant}"
            + (f" / load {load}" if load is not None else "")
            + ". Check the Euro Standard spelling against the workbook.")
    return hits


def _mix(category: str, euro: str, hits: list[dict], pollutant: str):
    """Collapse the technology dimension, either by declared shares or by
    checking that the technologies agree (and saying so if they do not)."""
    if len(hits) == 1:
        return _curve_from(hits[0], pollutant)
    shares = TECH_SHARES.get((category, euro))
    if shares:
        parts = []
        for tech, share in shares.items():
            match = [h for h in hits if h["Technology"] == tech]
            if len(match) != 1:
                raise FactorNotSourced(
                    f"{category}/{euro}: expected exactly one '{tech}' row, "
                    f"found {len(match)}")
            parts.append((share, _curve_from(match[0], pollutant)))
        total = sum(s for s, _ in parts)
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"technology shares for {euro} sum to {total}")
        return MixedEF(parts=tuple(parts), label=f"{category}/{euro}/{pollutant}")
    # No declared split: the technologies must agree, or the choice matters and
    # a share has to be sourced rather than picked.
    curves = [_curve_from(h, pollutant) for h in hits]
    probe = [10.0, 20.0, 40.0]
    spread = max(abs(c.at(v) - curves[0].at(v)) / max(abs(curves[0].at(v)), 1e-12)
                 for c in curves for v in probe)
    if spread > 1e-6:
        techs = sorted({str(h["Technology"]) for h in hits})
        raise FactorNotSourced(
            f"{category}/{euro}/{pollutant}: technologies {techs} give different "
            f"factors (up to {spread:.1%}). Add a sourced share to TECH_SHARES "
            f"instead of picking one.")
    return curves[0]


# ── The four public factors ─────────────────────────────────────────────────

@functools.lru_cache(maxsize=None)
def van_nox(euro: str):
    """Diesel LCV N1-III NOx, g/km as a function of mean speed."""
    return _mix(VAN_CATEGORY, euro, _select(VAN_CATEGORY, VAN_SEGMENT, euro, "NOx"), "NOx")


@functools.lru_cache(maxsize=None)
def van_ec(euro: str):
    """Same van, energy consumption MJ/km - the cross-check currency."""
    return _mix(VAN_CATEGORY, euro, _select(VAN_CATEGORY, VAN_SEGMENT, euro, "EC"), "EC")


@functools.lru_cache(maxsize=None)
def bus_nox(euro: str) -> LoadedEF:
    """Urban bus NOx, g/km as a function of mean speed AND load."""
    return _loaded(BUS_CATEGORY, BUS_SEGMENT, euro, "NOx")


@functools.lru_cache(maxsize=None)
def bus_ec(euro: str) -> LoadedEF:
    """Urban bus energy consumption, MJ/km, same axes - used to test the mixed
    method against the physics model (PIANO.md 4.2quater D)."""
    return _loaded(BUS_CATEGORY, BUS_SEGMENT, euro, "EC")


def _loaded(category: str, segment: str, euro: str, pollutant: str) -> LoadedEF:
    by_load = []
    for load in (0, 0.5, 1):
        hits = _select(category, segment, euro, pollutant, load=load, slope=SLOPE)
        by_load.append((float(load), _mix(category, euro, hits, pollutant)))
    return LoadedEF(by_load=tuple(by_load), label=f"{segment}/{euro}/{pollutant}")


# ── Self-test: the formula against the workbook's own numbers ───────────────

def validate_equation_25() -> dict:
    """Re-evaluate equation (25) against the EF column the workbook itself
    publishes, on every row that carries one. This is what makes the formula a
    verified fact rather than a reading of the text."""
    checked = worst = 0
    worst_label = ""
    for r in _rows():
        pub, v = r["_ef_published"], r["_ef_speed"]
        try:
            pub, v = float(pub), float(v)
        except (TypeError, ValueError):
            continue
        if pub == 0:
            continue
        try:
            got = _curve_from(r, str(r["Pollutant"])).at(v)
        except (FactorNotSourced, ZeroDivisionError):
            continue
        rel = abs(got - pub) / abs(pub)
        checked += 1
        if rel > worst:
            worst, worst_label = rel, f"{r['Category']}/{r['Pollutant']}"
    return {"rows_checked": checked, "worst_relative_error": worst,
            "worst_at": worst_label}


def status() -> str:
    lines = [f"workbook: {WORKBOOK}",
             f"present : {WORKBOOK.exists()}", ""]
    if not WORKBOOK.exists():
        return "\n".join(lines)
    v = validate_equation_25()
    lines.append(f"equation (25) vs the workbook's own EF column: "
                 f"{v['rows_checked']:,} rows, worst rel. error "
                 f"{v['worst_relative_error']:.2e} ({v['worst_at']})")
    lines.append("")
    for name, cls in FLEET_SCENARIOS.items():
        vn, bn = van_nox(cls["van"]), bus_nox(cls["bus"])
        lines.append(f"{name}: van {cls['van']} NOx @17.5 km/h = {vn.at(17.5):.3f} g/km"
                     f" | bus {cls['bus']} NOx @17.5 km/h, load 0 = {bn.at(17.5, 0.0):.3f}"
                     f", load 0.101 = {bn.at(17.5, 0.101):.3f} g/km")
    return "\n".join(lines)


if __name__ == "__main__":
    print(__doc__.strip().splitlines()[0])
    print()
    print(status())
