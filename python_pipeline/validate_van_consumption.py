"""
Validate the van emission model (longitudinal formula + in-cycle idle) against the
OFFICIAL Ford Transit Custom 2.0 EcoBlue WLTP fuel consumption (~6.8-6.9 L/100km
combined). Same cycle (WLTP), same vehicle -> the right yardstick. See HANDOFF §14.9.

Result (kerb mass 1950 kg, idle 0.7 L/h): combined 6.45 L/100km vs official ~6.85
-> within ~6%, no calibration multiplier needed. The residual is explained by the
official test mass (~2100-2200 kg) being above bare kerb, plus auxiliaries.

Run from the project root:  python python_pipeline/validate_van_consumption.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import numpy as np
from wltp.cycles import class3
from emission_formula import compute_co2_running, compute_co2_idle, speed_to_accel
from parameters import (VAN_TARE_KG, VAN_CD, VAN_FRONTAL_AREA_M2, VAN_ROLLING_RESISTANCE,
                        VAN_DRIVETRAIN_EFF, VAN_IDLE_FUEL_RATE_L_PER_S)
from van_cycles import IDLE_THRESH_MS

v_full = np.asarray(class3.class_data_b()['cycle'], dtype=float)/3.6  # m/s, all 4 phases
parts = {'Low':(0,589),'Medium':(590,1022),'High':(1023,1477),'ExtraHigh':(1478,1800),'COMBINED(full)':(0,1800)}

def consumption(v_ms, mass):
    a=speed_to_accel(v_ms)
    co2_tr=compute_co2_running(v_ms,a,mass,cd=VAN_CD,frontal_area_m2=VAN_FRONTAL_AREA_M2,
                               rolling_coeff=VAN_ROLLING_RESISTANCE,drivetrain_eff=VAN_DRIVETRAIN_EFF)
    idle_s=int(np.count_nonzero(v_ms<IDLE_THRESH_MS))
    co2_id=compute_co2_idle(idle_s, VAN_IDLE_FUEL_RATE_L_PER_S)
    dist=v_ms.sum()/1000.0
    Ltr=(co2_tr/2.65)/dist*100; Lid=(co2_id/2.65)/dist*100
    return Ltr, Lid, Ltr+Lid

mass=VAN_TARE_KG  # 1950, kerb-ish (official figure ~unladen)
print(f"Van Ford Transit Custom, massa {mass} kg, idle {VAN_IDLE_FUEL_RATE_L_PER_S*3600:.1f} L/h\n")
print(f"{'fase':16s}{'traction':>10s}{'idle':>8s}{'TOTALE':>9s}{'  (L/100km)'}")
for name,(a,b) in parts.items():
    Ltr,Lid,Lt=consumption(v_full[a:b+1],mass)
    print(f"{name:16s}{Ltr:10.2f}{Lid:8.2f}{Lt:9.2f}")
print(f"\n>>> UFFICIALE Ford WLTP combined = 6.8-6.9 L/100km  (target del confronto)")
