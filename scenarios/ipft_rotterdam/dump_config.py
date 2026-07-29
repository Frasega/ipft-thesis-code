import re

cfg = open(
    r"c:\Users\frare\OneDrive\Desktop\Tesi documents\matsim-example-project-master\scenarios\ipft_rotterdam\config.xml",
    encoding="utf-8",
).read()

for m in re.finditer(r'<module name="([^"]+)"\s*>', cfg):
    name = m.group(1)
    end = cfg.find("</module>", m.start())
    body = cfg[m.start() : end]
    if name == "planCalcScore":
        # too big: print only modeParams and the first few params, skip activityParams
        head = body[:1500]
        print("=" * 20, name, "(troncato, solo modeParams)")
        print(head)
        for mp in re.finditer(
            r'<parameterset type="modeParams"\s*>.*?</parameterset>', body, re.S
        ):
            print(mp.group(0))
    else:
        print("=" * 20, name)
        print(body[:3000])
