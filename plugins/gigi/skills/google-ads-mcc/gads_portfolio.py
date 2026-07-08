# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Portfolio Google Ads report across all MCC accounts (30d + 7d): spend, conv, value, CPA, ROAS."""
import subprocess, json, os, sys

GADS = "/Users/gheorghebeschea/.claude/plugins/marketplaces/team-intelligence/plugins/gigi/skills/google-ads-mcc/gads.py"
ACCTS = [
    ("9069610821","Grandia","RON",69),  # (id, name, currency, target Google CPA)
    ("5031005158","GT","RON",15),
    ("7585902074","Nubra","RON",15),
    ("7566352958","Belasil","RON",18),
    ("4778636466","Ofertele","RON",20),
    ("8148962111","Gento","RON",20),
    ("4069952156","Carpetto","RON",20),
    ("8287989891","Rossi","RON",18),
    ("6858257397","BonhausPL","PLN",None),
    ("3141935298","BonhausCZ","CZK",None),
]
Q = ("SELECT metrics.cost_micros, metrics.conversions, metrics.conversions_value, "
     "metrics.clicks, metrics.impressions FROM customer WHERE segments.date DURING {r}")

def pull(cid, rng):
    env = dict(os.environ)
    out = subprocess.run(["uv","run",GADS,"report","--customer",cid,"--query",Q.format(r=rng),
                          "--range",rng,"--format","json"], capture_output=True, text=True, env=env)
    txt = out.stdout
    i = txt.find("[")
    if i < 0: return None
    try:
        d = json.loads(txt[i:])
        if not d: return dict(cost=0,conv=0,val=0,clicks=0,impr=0)
        m = d[0]["metrics"]
        return dict(cost=int(m.get("costMicros",0))/1e6, conv=float(m.get("conversions",0)),
                    val=float(m.get("conversionsValue",0)), clicks=int(m.get("clicks",0)),
                    impr=int(m.get("impressions",0)))
    except Exception as e:
        return None

print(f"{'Cont':11}{'Cur':4}{'Spend30':>10}{'Conv30':>8}{'CPA30':>8}{'ROAS30':>8}{'Val30':>10} | {'Spend7':>9}{'Conv7':>7}{'CPA7':>7}{'ROAS7':>7}  target")
print("="*116)
rows=[]
for cid,name,cur,tcpa in ACCTS:
    d30 = pull(cid,"LAST_30_DAYS"); d7 = pull(cid,"LAST_7_DAYS")
    if d30 is None: print(f"{name:11}{cur:4}  ERROR"); continue
    cpa30 = d30["cost"]/d30["conv"] if d30["conv"] else 0
    roas30 = d30["val"]/d30["cost"] if d30["cost"] else 0
    cpa7 = d7["cost"]/d7["conv"] if d7 and d7["conv"] else 0
    roas7 = d7["val"]/d7["cost"] if d7 and d7["cost"] else 0
    rows.append((name,cur,d30,cpa30,roas30,d7,cpa7,roas7,tcpa))
    tflag = f"CPA≤{tcpa}" if tcpa else "—"
    print(f"{name:11}{cur:4}{d30['cost']:>10.0f}{d30['conv']:>8.0f}{cpa30:>8.0f}{roas30:>8.2f}{d30['val']:>10.0f} | "
          f"{d7['cost'] if d7 else 0:>9.0f}{d7['conv'] if d7 else 0:>7.0f}{cpa7:>7.0f}{roas7:>7.2f}  {tflag}")
print("="*116)
# RON-only totals
ron=[r for r in rows if r[1]=="RON"]
tc=sum(r[2]["cost"] for r in ron); tconv=sum(r[2]["conv"] for r in ron); tval=sum(r[2]["val"] for r in ron)
print(f"TOTAL RON (8 conturi): spend {tc:,.0f}  conv {tconv:.0f}  CPA {tc/tconv if tconv else 0:.0f}  ROAS {tval/tc if tc else 0:.2f}  val {tval:,.0f}")
