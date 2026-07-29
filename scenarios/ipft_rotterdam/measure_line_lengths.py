import sys, gzip, re
from pathlib import Path
HERE=Path(__file__).parent
sys.path.insert(0, str(HERE.parent.parent/"python_pipeline"))
from parse_events import load_link_attributes

# link lengths from network
net = HERE/"networkWithRideAndBike.xml.gz"
lengths,_ = load_link_attributes(str(net))
print(f"network: {len(lengths)} links")

sched = gzip.open(HERE/"ptSchedule36Hour.xml.gz","rt",encoding="utf-8").read()
# stop facility -> name
fac={}
for m in re.finditer(r'<stopFacility ([^>]+)>', sched):
    a=dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
    if "id" in a: fac[a["id"]]=(a.get("name","?"),a.get("linkRefId","?"))

def routes_of(lineid):
    block=re.search(rf'<transitLine id="{lineid}".*?</transitLine>', sched, re.S).group(0)
    return re.findall(r'<transitRoute id="([^"]+)">(.*?)</transitRoute>', block, re.S)

def route_len_km(body):
    # link sequence inside <route> ... <link refId="..."/>
    rt=re.search(r'<route>(.*?)</route>', body, re.S)
    links=re.findall(r'<link refId="([^"]+)"', rt.group(1)) if rt else []
    tot=sum(lengths.get(l,0.0) for l in links)
    return tot/1000.0, len(links)

for lineid,label in [("99437","Linea 44 (Centraal<->Zuidplein)"),
                     ("99431","Linea 99431 (Meijersplein<->Centraal)")]:
    print(f"\n=== {label}  [{lineid}] ===")
    for rid,body in routes_of(lineid):
        stops=re.findall(r'<stop refId="([^"]+)"', body)
        ndep=body.count("<departure ")
        km,nl=route_len_km(body)
        f0=fac.get(stops[0],("?",))[0]; f1=fac.get(stops[-1],("?",))[0]
        print(f"  route {rid}: {len(stops)} stops, {ndep} dep, {nl} link, LUNGHEZZA={km:.2f} km")
        print(f"     {f0}  ->  {f1}")
