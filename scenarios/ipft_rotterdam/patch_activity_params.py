import gzip, re

types = set()
with gzip.open("planExternalProcessed_lowerCase.xml.gz", "rt", encoding="utf-8") as f:
    for line in f:
        m = re.search(r'activity type="([^"]+)"', line)
        if m:
            types.add(m.group(1))

def secs_to_hms(s):
    s = int(s)
    return f"{s//3600:02d}:{(s%3600)//60:02d}:{s%60:02d}"

params = []
for t in sorted(types):
    parts = t.rsplit("_", 1)
    dur = secs_to_hms(parts[1]) if len(parts) == 2 and parts[1].isdigit() else "08:00:00"
    params.append(
        f'        <parameterset type="activityParams" >\n'
        f'            <param name="activityType" value="{t}"/>\n'
        f'            <param name="typicalDuration" value="{dur}"/>\n'
        f'        </parameterset>'
    )

block = "\n".join(params)

with open("config.xml", "r", encoding="utf-8") as f:
    config = f.read()

config = config.replace(
    '        </parameterset>\n    </module>\n    <module name="transitRouter">',
    f'{block}\n        </parameterset>\n    </module>\n    <module name="transitRouter">'
)

with open("config.xml", "w", encoding="utf-8") as f:
    f.write(config)

print(f"Added {len(types)} activity type params to config.xml")
