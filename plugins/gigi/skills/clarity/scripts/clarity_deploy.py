# /// script
# requires-python=">=3.10"
# dependencies=["httpx","pg8000","pypdf"]
# ///
"""Injecteaza / scoate snippet-ul Microsoft Clarity in layout/theme.liquid (inainte de </head>) pe magazinele ARONA.
ID-urile per magazin (prefix -> Clarity project id) = clarity_ids.json (langa script). Idempotent. Dry-run by default.

  uv run clarity_deploy.py                  # DRY-RUN injectare (verifica </head>, deja-Clarity, accesibilitate)
  uv run clarity_deploy.py --apply          # injecteaza (sare daca exista deja clarity.ms/tag)
  uv run clarity_deploy.py --apply --only EST,GT
  uv run clarity_deploy.py --apply --remove # SCOATE blocul Clarity de pe toate (reversibil)
"""
import importlib.util, httpx, json, argparse, os, re
# load_shopify_tokens din skill-ul xconnector (acelasi loader de tokenuri Shopify Admin)
_C=["/root/Scripturi/team-intelligence/plugins/gigi/skills/xconnector/xconnector.py",
    os.path.join(os.path.dirname(__file__), "..", "..", "xconnector", "xconnector.py"),
    "/Users/gheorghebeschea/Downloads/Scripturi/team-intelligence/plugins/gigi/skills/xconnector/xconnector.py"]
_XC=next((p for p in _C if os.path.exists(p)), _C[1])
spec=importlib.util.spec_from_file_location("xc", _XC); spec.loader.exec_module(xc:=importlib.util.module_from_spec(spec))

ap=argparse.ArgumentParser()
ap.add_argument("--apply", action="store_true")
ap.add_argument("--only", default="", help="doar anumite prefixe, CSV (ex EST,GT)")
ap.add_argument("--remove", action="store_true", help="scoate blocul Clarity in loc sa-l injecteze")
a=ap.parse_args()

CLARITY=json.load(open(os.path.join(os.path.dirname(__file__), "clarity_ids.json")))
toks={t.get("prefix","").upper():(t["shopDomain"], t["adminToken"]) for t in xc.load_shopify_tokens()}
def snip(pid): return ('  <!-- Microsoft Clarity -->\n  <script type="text/javascript">\n'
  '    (function(c,l,a,r,i,t,y){c[a]=c[a]||function(){(c[a].q=c[a].q||[]).push(arguments)};\n'
  '    t=l.createElement(r);t.async=1;t.src="https://www.clarity.ms/tag/"+i;\n'
  '    y=l.getElementsByTagName(r)[0];y.parentNode.insertBefore(t,y);})(window,document,"clarity","script","%s");\n'
  '  </script>\n  <!-- End Microsoft Clarity -->\n') % pid
BLOCK_RE=re.compile(r"[ \t]*<!-- Microsoft Clarity -->.*?<!-- End Microsoft Clarity -->\n?", re.S)
API="2024-01"
only=set(x.strip().upper() for x in a.only.split(",") if x.strip())
stat=dict(injected=0, removed=0, already=0, nohead=0, err=0)
for pref,pid in CLARITY.items():
    if only and pref not in only: continue
    if pref not in toks: print("  ⚠ %-6s fara token Shopify"%pref); stat["err"]+=1; continue
    dom,tok=toks[pref]; h={"X-Shopify-Access-Token":tok}
    try:
        th=httpx.get(f"https://{dom}/admin/api/{API}/themes.json", headers=h, timeout=30).json()
        main=next((t for t in th.get("themes",[]) if t.get("role")=="main"), None)
        if not main: print("  ⚠ %-6s fara main theme"%pref); stat["err"]+=1; continue
        ga=httpx.get(f"https://{dom}/admin/api/{API}/themes/{main['id']}/assets.json", headers=h, params={"asset[key]":"layout/theme.liquid"}, timeout=30).json()
        body=(ga.get("asset") or {}).get("value")
        if body is None: print("  ⚠ %-6s fara theme.liquid"%pref); stat["err"]+=1; continue
        has=("clarity.ms/tag" in body)
        if a.remove:
            if not has: print("  • %-6s n-are Clarity → skip"%pref); continue
            new=BLOCK_RE.sub("", body)
            if not a.apply: print("  → %-6s AR scoate Clarity (%d→%d ch)"%(pref,len(body),len(new))); stat["removed"]+=1; continue
            r=httpx.put(f"https://{dom}/admin/api/{API}/themes/{main['id']}/assets.json", headers=h, json={"asset":{"key":"layout/theme.liquid","value":new}}, timeout=40)
            print(("  ✅ %-6s scos" if r.status_code==200 else "  ❌ %-6s PUT %s")%(pref, "" if r.status_code==200 else r.status_code)); stat["removed" if r.status_code==200 else "err"]+=1
            continue
        if has: print("  • %-6s deja are Clarity → skip"%pref); stat["already"]+=1; continue
        if "</head>" not in body: print("  ⚠ %-6s NU are </head> (head in snippet?)"%pref); stat["nohead"]+=1; continue
        new=body.replace("</head>", snip(pid)+"</head>", 1)
        if not a.apply: print("  → %-6s AR injecta (%s, %d→%d ch) id=%s"%(pref,main.get("name"),len(body),len(new),pid)); stat["injected"]+=1; continue
        r=httpx.put(f"https://{dom}/admin/api/{API}/themes/{main['id']}/assets.json", headers=h, json={"asset":{"key":"layout/theme.liquid","value":new}}, timeout=40)
        print(("  ✅ %-6s injectat (id %s)" if r.status_code==200 else "  ❌ %-6s PUT %s: %s")%((pref,pid) if r.status_code==200 else (pref,r.status_code,r.text[:90]))); stat["injected" if r.status_code==200 else "err"]+=1
    except Exception as e:
        print("  ❌ %-6s %s"%(pref,str(e)[:90])); stat["err"]+=1
print("\nSTAT:", json.dumps(stat,ensure_ascii=False), "·", ("REMOVE " if a.remove else "INJECT "), "APPLY" if a.apply else "DRY-RUN")
