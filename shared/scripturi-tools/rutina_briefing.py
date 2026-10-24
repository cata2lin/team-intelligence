#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generează briefingul de dimineață ARONA ca FIȘIER (fără mail).
latest.md + copie datată în /root/Scripturi/data/briefing/. Rulează 07:45/12:00/17:00 (cron).
Surse: gads_portfolio.py (ads, IDENTIC cu ce aprobă owner-ul) · AWBprint (mișcare stoc + viteză HA) ·
Order Hub container (neplecate blocate+hold) · scan loguri (joburi picate). CS = best-effort.
Alertele urgente din timpul zilei se scriu în data/briefing/alerts.md (prepend)."""
import os
import sys, re, sys, subprocess, datetime, pathlib
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE="/root/Scripturi"; OUT=pathlib.Path(HERE,"data/briefing"); OUT.mkdir(parents=True,exist_ok=True)
GADS=os.path.join(HERE,"team-intelligence/plugins/gigi/skills/google-ads-mcc/gads_portfolio.py")
UV="/root/.local/bin/uv"
TZ=datetime.timezone(datetime.timedelta(hours=3))  # Europe/Bucharest (EEST aprox; ok pt antet)
NOW=datetime.datetime.now(TZ)
# HA liquidation (plan Alex, deadline 05.12) — total refresh săptămânal din sheet; viteza zilnic din AWBprint
HA_TOTAL=177968; HA_COST=2496425; DEADLINE=datetime.date(2026,12,5)

def ro(n):  # mii cu punct, stil RO
    return f"{int(round(n)):,}".replace(",",".")

def sh(cmd, timeout=120):
    try:
        r=subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout, r.returncode
    except Exception as e:
        return f"ERR:{e}", 1

def pg(dsn_var, sql, timeout=60):
    """psql read-only prin .env; întoarce liste de tuple (split pe |)."""
    cmd=(f'set -a; . {HERE}/.env; set +a; '
         f'psql "${dsn_var}" -At -F"|" -c "{sql}"')
    out,rc=sh(cmd,timeout)
    if rc!=0: return None
    return [ln.split("|") for ln in out.strip().splitlines() if ln.strip()]

# ---------- 1. ADS (gads_portfolio, parse CPA7 + ținte embedded owner 20-oct) ----------
TGT={"Grandia":40,"GT Parfumuri":30,"Nubra":30,"Gento":45,"Ofertele":33,"Lab Noir":25,
     "Maison":25,"Bonhaus CZ":28,"Carpetto":40,"Casa":30,"ROSSI":27,"Nocturna":43,"Apreciat":30}
NUME={"Maison":"Esteban","GT Parfumuri":"George Talent","ROSSI":"ROSSI","Ofertele":"Ofertele Zilei"}
def ads_section():
    out,rc=sh(f'set -a; . {HERE}/.env; set +a; {UV} run {GADS}', timeout=180)
    if rc!=0 or "PORTOFOLIU" not in out:
        return ["- (ads indisponibil azi — verific gads_portfolio)"]
    rosu=[]; galben=[]
    for ln in out.splitlines():
        if "|" not in ln or "ȚINTĂ" in ln or "ROAS/venit" in ln: continue
        left,right=ln.split("|",1)
        nm=left[:16].strip()
        m=re.match(r"\s*(\d+)", right)
        if not m: continue
        cpa7=int(m.group(1))
        key=next((k for k in TGT if k.lower() in nm.lower()), None)
        if not key or cpa7<=0: continue
        t=TGT[key]
        if cpa7<=t: continue                       # 🟢 = la țintă, nu-l afișăm
        verd="🔴" if cpa7>t*1.25 else "🟡"
        disp=NUME.get(key,nm)
        rec=("tai bugetul ~25% pe campaniile peste țintă" if verd=="🔴" else "ține sub ochi, nu umbla la el")
        line=f"**{disp}** {verd} — CPA pe 7 zile **{cpa7} lei** (ținta {t}). *Recomand: {rec}.*"
        (rosu if verd=="🔴" else galben).append((cpa7,line))
    sel=[l for _,l in sorted(rosu,key=lambda x:-x[0])]+[l for _,l in sorted(galben,key=lambda x:-x[0])]
    sel=sel[:4]
    return [f"{i+1}. {s}" for i,s in enumerate(sel)] or ["- toate conturile la țintă ✅"]

# ---------- 2+3. STOC (AWBprint) ----------
LINII_SQL="""
WITH g AS (SELECT uid FROM stores WHERE name='grandia.ro'),
li AS (SELECT o.uid ouid, o.store_uid, o.frisbo_created_at d,
       l->'inventory_item'->>'sku' sku, lower(coalesce(l->'inventory_item'->>'title_1','')) t,
       (l->>'quantity')::numeric q
       FROM orders o, json_array_elements(o.line_items) l
       WHERE o.frisbo_created_at > now()-interval '31 days'),
cls AS (SELECT d,q, CASE WHEN sku ~ '^HA-[0-9]' THEN 'HA'
   WHEN sku ~ '^set-[0-9]+-lavete' OR sku ~ '^laveta' THEN 'Lavete'
   WHEN store_uid=(SELECT uid FROM g) THEN 'Grandia'
   WHEN t LIKE 'covor %' THEN 'Covoare' ELSE 'Alte' END linie FROM li)
SELECT linie,
 coalesce(round(sum(q) FILTER (WHERE d::date=(now()-interval '1 day')::date)),0),
 coalesce(round(sum(q) FILTER (WHERE d>now()-interval '7 days')),0),
 coalesce(round(sum(q) FILTER (WHERE d>now()-interval '14 days' AND d<=now()-interval '7 days')),0)
FROM cls WHERE linie<>'Alte' GROUP BY linie ORDER BY 3 DESC;"""
def stoc_section():
    rows=pg("DATABASE_URL_AWBPRINT", LINII_SQL.replace("\n"," "))
    if not rows: return None, ["- (mișcare stoc indisponibilă)"]
    d={r[0]:(int(r[1]),int(r[2]),int(r[3])) for r in rows if len(r)>=4}
    def fmt(n):
        ieri,s7,p7=d.get(n,(0,0,0))
        if p7>0:
            ch=(s7-p7)/p7*100; tr=f" (în creștere +{ch:.0f}%)" if ch>=10 else (f" (în scădere {ch:.0f}%)" if ch<=-10 else " (plat)")
        else: tr=""
        return f"{n} {ro(ieri)} / {ro(s7)}{tr}"
    line="- "+" · ".join(fmt(n) for n in ["HA","Grandia","Covoare","Lavete"])
    ha7=d.get("HA",(0,0,0))[1]
    return ha7, [line]

def ha_section(ha7):
    zile=(DEADLINE-NOW.date()).days
    vzi=ha7/7 if ha7 else 0
    nec=HA_TOTAL/zile if zile>0 else 0
    pct=vzi/nec*100 if nec else 0
    cost=("%.2f" % (HA_COST/1e6)).replace(".",",")
    return [f"~**{ro(vzi)} buc/zi** vs. necesar **{ro(nec)} buc/zi** (**{pct:.0f}%**) ca să golim până {DEADLINE.strftime('%d.%m')} · "
            f"**{cost} mil. lei** la cost în depozit · {zile} zile rămase."]

# ---------- 3b. Influenceri MD fără screenshot (scris de influenceri_md.py 06:30, scripturi-ca) ----------
def influenceri_section():
    import json
    f=OUT.parent/"influenceri_md_fara_screenshot.json"
    try:
        d=json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None
    try:
        gen=datetime.datetime.fromisoformat(d.get("generat","")); stale=(datetime.datetime.now()-gen).days>=2
    except Exception:
        stale=True
    if stale:
        return ["## INFLUENCERI MD","- ⚠️ lista nu s-a actualizat (verifică jobul influenceri_md)"]
    nc=d.get("fara_screenshot",0); npost=d.get("fara_dovada_postarii",0)
    if not nc and not npost:
        return None
    def rows(lst):
        return ["  - %s · %s · %s — [comandă](%s)"%(it.get("comanda","?"),it.get("data",""),it.get("client",""),it.get("link","")) for it in (lst or [])[:5]]
    out=["## %s"%d.get("titlu","Influenceri MD")]
    if nc:
        out.append("**Fără conversație (%d):**"%nc); out+=rows(d.get("primele"))
    if npost:
        out.append("**Fără dovada postării (%d):**"%npost); out+=rows(d.get("primele_post"))
    return out

# ---------- 3c. Creative Statice: notă provizorie + gardă pe lunile trecute Ofertele ----------
# snapshot 21-oct, înainte de a relega conturile — gardă pe lunile trecute (dublă-numărare)
GUARD_BASE={"Ofertele Zilei":{"aug":201321,"sep":214417,"oct118":130177},
            "George Talent":{"aug":208076,"sep":244194,"oct118":198114}}
def creative_check():
    out=["## Atribuire ads (conturi relegate)",
         "- ℹ️ Creative Statice: NEW SUPER SCALE (~189 USD/7z) provizoriu pe Ofertele Zilei — de confirmat de owner.",
         "- ℹ️ George Talent 2 (RON) relegat la George Talent (toate campaniile active = GT)."]
    for brand,base in GUARD_BASE.items():
        rows=pg("DATABASE_URL_METRICS",
            "SELECT coalesce(round(sum(spend_ron) FILTER (WHERE date BETWEEN '2026-08-01' AND '2026-08-31')),0),"
            "coalesce(round(sum(spend_ron) FILTER (WHERE date BETWEEN '2026-09-01' AND '2026-09-30')),0),"
            "coalesce(round(sum(spend_ron) FILTER (WHERE date BETWEEN '2026-10-01' AND '2026-10-18')),0) "
            "FROM cache.daily_ad_spend_ron s JOIN brands b ON b.id=s.brand_id WHERE b.name='%s'"%brand)
        if not (rows and len(rows[0])>=3): continue
        try:
            aug,sep,oct118=(int(float(x)) for x in rows[0][:3])
            mis=[]
            for et,cur_,b in (("aug",aug,base["aug"]),("sep",sep,base["sep"]),("1-18 oct",oct118,base["oct118"])):
                if abs(cur_-b)>max(500,0.01*b): mis.append("%s %s→%s"%(et,ro(b),ro(cur_)))
            if mis:
                out.append("- 🔴 ALERTĂ %s: lunile trecute s-au mișcat (dublă-numărare?): %s → verifică Mapping."%(brand," · ".join(mis)))
        except Exception:
            pass
    return out

# ---------- 3d. Confirmare scalare bugete (din scale_status.json, scris de scale_0800.py) ----------
def scale_confirm():
    import json
    f=OUT.parent/"scale_status.json"
    try:
        d=json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None
    if d.get("date")!=NOW.strftime("%Y-%m-%d"):
        return None
    marks=" · ".join("%s %s"%(a.get("nume","?").split(" (")[0], "✓" if a.get("ok") else "✗") for a in d.get("applied",[]))
    return ["## Scalare bugete (08:00)","- %s aplicată la %s: %s"%("✅" if d.get("ok") else "🔴", d.get("ts",""), marks)]

# ---------- 4. OH coadă (rută HTTPS /api/cs/sumar-coada, 73) ----------
def oh_section():
    import json, urllib.request
    tok=os.environ.get("OH_CS_SB_TOKEN","").strip()
    if not tok:
        out,_=sh(f'{UV} run {HERE}/team-intelligence/plugins/core/scripts/kb.py secret-get OH_CS_SB_TOKEN', timeout=40)
        tok=(out or "").strip().splitlines()[-1].strip() if out else ""
    if not tok:
        return ["- coada OH indisponibilă (lipsă token)"]
    try:
        req=urllib.request.Request("https://orderhub.arona.ro/api/cs/sumar-coada?zile=14&zile_anulate=3",
                                    headers={"Authorization":"Bearer "+tok,"Accept":"application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            d=json.loads(r.read().decode("utf-8"))
        if not d.get("ok"):
            return ["- coada OH: răspuns invalid"]
        return ["Blocate (neplecate de peste 14 zile): **%s** · pe hold: **%s** · anulate după print (3 zile): **%s**."
                % (d.get("neplecate_vechi","?"), d.get("hold","?"), d.get("anulate_dupa_print","?"))]
    except Exception:
        return ["- coada OH indisponibilă (verific ruta /api/cs/sumar-coada)"]

# ---------- 4a. Garda AWB (rută OH /api/cs/garda-awb): lipsă din OH + fără AWB fără motiv legitim ----------
def garda_awb_section():
    try:
        sys.path.insert(0, HERE)
        from garda_awb_briefing import linii_briefing
        return linii_briefing()
    except Exception as e:
        return ["- Garda AWB indisponibilă (%s)" % type(e).__name__]

# ---------- 4a4. Sursa COGS in engine-ul de brand (S2, comutator in profitability.db) ----------
def cogs_engine_section():
    try:
        sys.path.insert(0, HERE)
        from cogs_engine_briefing import linii
        return linii()
    except Exception as e:
        return ["- Sursa COGS (engine de brand): necitita (%s)" % type(e).__name__]

# ---------- 4a3. Sursa COGS in P&L (S2, comutator in baza AWBprint) ----------
def cogs_sursa_section():
    try:
        sys.path.insert(0, HERE)
        from cogs_sursa_briefing import linii
        return linii()
    except Exception as e:
        return ["- Sursa COGS: necitita (%s)" % type(e).__name__]

# ---------- 4a2. Garda FACTURI (rută OH /api/cs/garda-factura): tag factura-oh fără factură ----------
def garda_factura_section():
    try:
        sys.path.insert(0, HERE)
        from garda_factura_briefing import linii_briefing
        return linii_briefing()
    except Exception as e:
        return ["- Garda facturi indisponibilă (%s)" % type(e).__name__]

# ---------- 4a3. Coada SmartBill „în așteptare” (rută OH /api/cs/smartbill-asteptare): facturi/stornouri refuzate temporar ----------
def smartbill_asteptare_section():
    try:
        sys.path.insert(0, HERE)
        from smartbill_asteptare_briefing import linii_briefing
        return linii_briefing()
    except Exception as e:
        return ["- SmartBill în așteptare: indisponibil (%s)" % type(e).__name__]

# ---------- 4b. Produs unic (rută OH /api/produs/umbra/zi, scripturi-12) ----------
def produs_unic_section():
    import json, urllib.request
    tok=os.environ.get("OH_CS_SB_TOKEN","").strip()
    if not tok:
        out,_=sh(f'{UV} run {HERE}/team-intelligence/plugins/core/scripts/kb.py secret-get OH_CS_SB_TOKEN', timeout=40)
        tok=(out or "").strip().splitlines()[-1].strip() if out else ""
    if not tok:
        return None
    try:
        req=urllib.request.Request("https://orderhub.arona.ro/api/produs/umbra/zi",
                                    headers={"Authorization":"Bearer "+tok,"Accept":"application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            d=json.loads(r.read().decode("utf-8"))
        if not d.get("ok"):
            return None
        Y=d.get("coduri",0); X=d.get("rezolvate",0); Z=d.get("diferente",0)
        pref="🔴 " if (isinstance(Z,(int,float)) and Z>0) else ""
        return ["## Produs unic","- %sProdus unic: %s/%s coduri vândute ieri rezolvate, %s diferențe"%(pref,X,Y,Z)]
    except Exception:
        return None


# ---------- 4c. Umbra S4: scanerul vs Order Hub live (ops/oh_umbra_raport.py) ----------
def depozit_config_section():
    """S6 grupul 1 (scripturi-12, 24-oct-2026): config-ul depozitului copiat în OH, diferențele de ieri. Roșu = tura nu
    mai rulează / tabele necitite."""
    import json, urllib.request
    tok=os.environ.get("OH_CS_SB_TOKEN","").strip()
    if not tok:
        out,_=sh(f'{UV} run {HERE}/team-intelligence/plugins/core/scripts/kb.py secret-get OH_CS_SB_TOKEN', timeout=40)
        tok=(out or "").strip().splitlines()[-1].strip() if out else ""
    if not tok:
        return None
    try:
        req=urllib.request.Request("https://orderhub.arona.ro/api/depozit-config/umbra",
                                    headers={"Authorization":"Bearer "+tok,"Accept":"application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            d=json.loads(r.read().decode("utf-8"))
        if not d.get("ok"):
            return None
        t=d.get("total") or {}
        pref="🔴 " if d.get("rosu") else ""
        schimb=", ".join("%s %s/%s/%s"%(k,v.get("noi"),v.get("schimbate"),v.get("disparute"))
                         for k,v in (d.get("tabele") or {}).items() if (v.get("noi") or v.get("schimbate") or v.get("disparute")))
        return ["## Depozit config (S6, umbră)",
                "- %sConfig depozit în OH: ieri %s noi / %s schimbate / %s dispărute%s; comutator %s"%(
                    pref,t.get("noi",0),t.get("schimbate",0),t.get("disparute",0),
                    (" ("+schimb+")") if schimb else "", d.get("comutator"))]
    except Exception:
        return None


def miscare_section():
    """Umbra funcției unice de mișcare pe raft (S6, scripturi-12, 24-oct-2026): ce ar fi scris altfel, ieri."""
    try:
        sys.path.insert(0, HERE)
        import stoc_raft as _sr
        r = _sr.raport(1)
        if not r.get("verificari"):
            return ["## Mișcare pe raft (S6, umbră)", "- Funcția unică (mod %s): nicio mișcare verificată ieri" % _sr.mod()]
        pref = "🔴 " if r.get("cu_diferente") else ""
        fel = ", ".join("%s %d" % (k, v) for k, v in (r.get("pe_fel") or {}).items())
        return ["## Mișcare pe raft (S6, umbră)", "- %sFuncția unică (mod %s): %d mișcări verificate, %d cu diferențe%s"
                % (pref, _sr.mod(), r["verificari"], r["cu_diferente"], (" (" + fel + ")") if fel else "")]
    except Exception:
        return None


def depozit_loturi_section():
    """S6 grupul 3 (scripturi-12, 24-oct-2026): culesul și loturile copiate în OH, ieri + contraproba AWB-urilor."""
    import json, urllib.request
    tok = os.environ.get("OH_CS_SB_TOKEN", "").strip()
    if not tok:
        out, _ = sh(f'{UV} run {HERE}/team-intelligence/plugins/core/scripts/kb.py secret-get OH_CS_SB_TOKEN', timeout=40)
        tok = (out or "").strip().splitlines()[-1].strip() if out else ""
    if not tok:
        return None
    try:
        req = urllib.request.Request("https://orderhub.arona.ro/api/depozit-config/loturi/umbra",
                                     headers={"Authorization": "Bearer " + tok, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read().decode("utf-8"))
        if not d.get("ok"):
            return None
        t = d.get("total") or {}
        cp = d.get("contraproba") or {}
        pref = "🔴 " if d.get("rosu") else ""
        return ["## Cules și loturi (S6, umbră)",
                "- %sLoturi în OH: ieri %s noi / %s schimbate; %s loturi, %s AWB (%s anulate după print), %s%s AWB necunoscute în OH; comutator %s" % (
                    pref, t.get("noi", 0), t.get("schimbate", 0), cp.get("loturi", "?"), cp.get("awb", "?"),
                    cp.get("awb_anulate_dupa_print", "?"), "🔴 " if cp.get("awb_necunoscute_oh") else "",
                    cp.get("awb_necunoscute_oh", "?"), d.get("comutator"))]
    except Exception:
        return None


def umbra_s4_section():
    """O linie cu cate etichete/comenzi anulate s-ar fi prins daca scanerul citea OH live."""
    try:
        sys.path.insert(0, HERE)
        from ops.oh_umbra_raport import linie
        return ["## Umbra S4 (scaner -> Order Hub)", linie(1)]
    except Exception:
        return None


# ---------- 4c1b. Rata de livrare pe canal + mixul de spend pe magazin (livrare_pe_canal.py, luni 06:25) ----------
def livrare_canal_section():
    """Prima linie (alarma / starea pe canal) zilnic; tabelele complete din fișierul săptămânal."""
    try:
        f = OUT / "livrare-pe-canal.md"
        if not f.exists():
            return None
        linii = f.read_text(encoding="utf-8").splitlines()
        stare = next((l for l in linii if l.startswith(("🔴", "🟢"))), None)
        if not stare:
            return None
        return ["## Livrare pe canal (Orders, săptămânal)", stare, "Tabelele (canal × lună, magazin × lună cu % TikTok / Meta / Google): data/briefing/livrare-pe-canal.md"]
    except Exception:
        return None


# ---------- 4c2. Umbra S8: punerile pe raft legate de un colet intors (ops/retur_raft_umbra.py) ----------
def umbra_s8_section():
    """O linie: cate bucati puse pe raft fara retur scanat s-ar fi legat de un colet intors din OH."""
    try:
        sys.path.insert(0, HERE)
        from ops.retur_raft_umbra import linie
        out = ["## Umbra S8 (raft -> retur)", linie(1)]
        try:
            from ops.s8_frisbo_recuperare import linie as linie_frisbo
            out.append(linie_frisbo())
        except Exception:
            pass
        return out
    except Exception:
        return None


# ---------- 4d. Garda de COGS aberant (ops/cogs_garda.py) ----------
def cogs_garda_section():
    """O linie: cate produse au cost care CONTRAZICE costul de achizitie platit (nu procentul COGS).

    Pragul pe procent nu merge la noi: lavetele au 2,5% COGS real, parfumurile 14,2% (cost 9,00 pe
    reteta). Semnalul util e contrazicerea costului din `produs_cost` (Order Hub), nu marja.
    """
    try:
        sys.path.insert(0, HERE)
        from ops.cogs_garda import linie
        return ["## Garda COGS", linie(30)]
    except Exception:
        return None


# ---------- 4e. Pretul automat Grandia (grandia_pret_automat.py, umbra din 23-oct) ----------
def pret_automat_section():
    """O linie: ce ar schimba azi pretul automat (umbra) / ce a schimbat (activ). Fisierul il scrie jobul de la 04:40."""
    try:
        import json as _j
        d=_j.load(open(HERE+"/data/briefing/pret_automat.json"))
        pa=d.get("pe_actiune") or {}
        if d.get("mod")=="oprit":
            return ["## Pret automat Grandia", "- OPRIT din comutator (prc_auto_config.mod)"]
        top=", ".join("%s %s->%s"%(x["sku"],x["vechi"],x["nou"]) for x in (d.get("top") or [])[:3])
        return ["## Pret automat Grandia (%s, %s)"%(d.get("mod"), d.get("zi")),
                "- propune: %d scumpiri, %d ieftiniri · intai promovare %d · intai reclama %d · la podea %d%s"%(
                    pa.get("scumpire",0), pa.get("ieftinire",0), pa.get("intai_promovare",0), pa.get("intai_reclama",0),
                    pa.get("la_podea",0), (" · ex: "+top) if top else "")] + _pret_masurate_linii(d)
    except Exception:
        return None


def _pret_masurate_linii(d):
    """Pasii aplicati: viteza inainte/dupa la 7 si 14 zile (din grandia_pret_automat, cheia 'masurate')."""
    m=d.get("masurate") or []
    gata=[x for x in m if "7z" in x]
    if not m:
        return []
    out=["- pasi aplicati: %d (masurati la 7 zile: %d)"%(len(m), len(gata))]
    for x in gata:
        k="14z" if "14z" in x else "7z"; v=x[k]
        semn="OPRIT urcarea" if v.get("oprit_urcarea") else ("a stricat" if v.get("a_stricat") else "a tinut")
        out.append("  - %s %s: %s buc inainte -> %s dupa (%s) %s"%(x["sku"], x["pret"], v["buc_inainte"], v["buc_dupa"], k, semn))
    return out


def status_oh_section():
    """O linie: sursa statusului livrat/refuzat in P&L (OH din 22-oct) + efectul masurat."""
    try:
        import sqlite3 as _sq
        cx=_sq.connect("/root/Scripturi/data/profitability.db")
        v=(cx.execute("SELECT value FROM profit_settings WHERE key=\'status_sursa\'").fetchone() or ["awbprint"])[0]
        cx.close()
        if str(v).lower()=="oh":
            return ["## Status livrare in P&L = Order Hub (din 22-oct)",
                    "- Motorul de profit ia livrat/refuzat din OH (colete), nu din AWBprint. Efect masurat iul-oct: +~477k lei profit recunoscut (engine-ul sub-numara livrarile; EST domina, ~98% luna curenta).",
                    "- deliverability + breakeven acum pe OH (brandref refresh-at, 13 branduri); COD ramane pe AWBprint (impact RO ~zero). Rollback: profit_settings.status_sursa=awbprint + rebuild brand_pnl_real."]
        return None
    except Exception:
        return None


# ---------- 4g. Grandia: listarile cu esantion mic, de urmarit (scripturi-d9, 23-oct) ----------
URMARIRE_FIS = "/root/Scripturi/data/grandia_urmarire.json"


def grandia_urmarire_section():
    """Listarile puse pe Grandia FARA dovada de refuz (sub 20 de colete pe tot istoricul).

    DE CE (decizia Orchestratorului, 23-oct): pragul de 22% respinge un refuz DOVEDIT; pe un produs cu
    8 colete in toata viata lui nu exista dovada, nici intr-un sens nici in celalalt. Asa ca se listeaza,
    dar se urmaresc: cand unul ajunge la 20 de colete pe tot istoricul SI refuzul trece de 22%, se ascunde
    de pe Grandia. Pana atunci linia nu apare in briefing — un produs care inca strange date nu e o sarcina.
    """
    import json, sqlite3
    try:
        lista = json.load(open(URMARIRE_FIS, encoding="utf-8"))
    except Exception:
        return None
    skus = [str(x.get("sku") or "").strip().upper() for x in (lista.get("skus") or []) if x.get("sku")]
    if not skus:
        return None
    try:
        con = sqlite3.connect("file:/root/Scripturi/data/profitability.db?mode=ro", uri=True, timeout=20)
        q = ("SELECT skus, status_category, COUNT(*) FROM profit_orders "
             "WHERE skus IN (%s) AND status_category IN ('Refuzata','Livrata') GROUP BY skus, status_category"
             % ",".join("?" * len(skus)))
        randuri = con.execute(q, skus).fetchall()
        con.close()
    except Exception:
        return None
    d = {}
    for sku, cat, n in randuri:
        v = d.setdefault(str(sku).strip().upper(), {"liv": 0, "ref": 0})
        v["ref" if cat == "Refuzata" else "liv"] += n
    rele = []
    for sku in skus:
        v = d.get(sku) or {"liv": 0, "ref": 0}
        trimise = v["liv"] + v["ref"]
        if trimise < 20:
            continue                              # inca nu avem dovada: nu e nimic de facut
        pct = v["ref"] * 100.0 / trimise
        if pct > 22:
            rele.append((sku, pct, trimise))
    if not rele:
        return None
    linii = ["## Grandia — produse de ASCUNS (refuzul s-a dovedit)"]
    for sku, pct, n in sorted(rele, key=lambda z: -z[1]):
        linii.append("- 🔴 `%s` a ajuns la %d colete, refuz %.1f%% (peste 22%%) — scoate-l de pe Grandia." % (sku, n, pct))
    return linii


SCOATERI_FIS = "/root/Scripturi/data/garda_scoateri.json"


def scoateri_orfane_section():
    """Marfa stearsa de pe un raft ieri, care nu a reaparut nicaieri in 72 de ore.

    DE CE (23-oct): HA-0021 — 204 buc mutate pe E31, apoi scoase, si nimic dupa; marfa nu s-a
    vandut si nu s-a mutat, a disparut doar din scaner. Lista e de VERIFICAT, nu un verdict:
    raftul poate fi golit legitim sau marfa casata. Cine cere numaratoarea e ownerul, nu garda —
    depozitului nu-i extindem singuri lista de cautat.
    """
    import json
    try:
        d = json.load(open(SCOATERI_FIS, encoding="utf-8"))
    except Exception:
        return None
    noi = d.get("noi") or []
    if not noi:
        return None
    linii = ["## Stoc — marfa scoasa de pe raft si neregasita (%s)" % d.get("zi", "ieri")]
    for x in noi[:8]:
        linii.append("- 🔎 `%s` — %d buc scoase de pe %s (%s) la %s de %s; nu au reaparut pe alt raft in 72h."
                     % (x["sku"], x["buc"], x["raft"], x["statie"], x["ora"], x["cine"]))
    if len(noi) > 8:
        linii.append("- … inca %d cazuri in `data/garda_scoateri.json`." % (len(noi) - 8))
    linii.append("- Daca vrei verificare pe raft, spune TU ce coduri se cer depozitului.")
    return linii


# ---------- 5. sănătate ----------
def jobs_section():
    return ["- `data_health` rulează 09:15 și semnalează problemele de date (token mort, date vechi, cron picat) — aici, nu pe mail.",
            "- ℹ️ Verificarea de deploy (09:45) poate arăta câteva fișiere «diferă pe server»: sunt fix-urile de azi (owner fără mail), încă necomise în git-ul central. E normal, nu un defect."]


# ---------- 4f. Culese dar nepredate (OH /api/cs/culese-nepredate, scripturi-73) ----------
def culese_nepredate_section():
    """Coletele CULESE de om pe care curierul nu le-a scanat niciodata (23-oct: 28, cel mai vechi de 27 zile).

    Garda de dinainte (`predare_partiala`) vede doar cazul in care un FRATE AL ACELEIASI COMENZI a plecat;
    astea sunt comenzi cu un singur colet, ai caror frati sunt din acelasi LOT DE CULES. Trendyol & co. sunt
    excluse in OH (acolo scanarea nu ajunge la noi). Intoarce None la orice eroare: briefingul nu cade.
    """
    import json, urllib.request
    tok = os.environ.get("OH_CS_SB_TOKEN", "").strip()
    if not tok:
        out, _ = sh(f'{UV} run {HERE}/team-intelligence/plugins/core/scripts/kb.py secret-get OH_CS_SB_TOKEN',
                    timeout=40)
        tok = (out or "").strip().splitlines()[-1].strip() if out else ""
    if not tok:
        return None
    try:
        req = urllib.request.Request("https://orderhub.arona.ro/api/cs/culese-nepredate",
                                     headers={"Authorization": "Bearer " + tok, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=25) as r:
            d = json.loads(r.read().decode("utf-8"))
        rand = (d.get("rand_briefing") or "").strip()
        if not rand:
            return None                      # nimic de spus = niciun rand in briefing
        linii = ["## Colete culese dar nepredate", "- 🔴 " + rand]
        # cele mai vechi 5, ca omul sa stie de unde sa inceapa cautarea pe raft
        colete = sorted(d.get("colete") or [], key=lambda c: -(c.get("zile") or 0))[:5]
        for c in colete:
            linii.append("  - `%s` %s (%s) · %s · cules de %s · %s zile" % (
                c.get("awb"), c.get("comanda"), c.get("magazin"), c.get("statie"),
                c.get("cules_de") or "?", c.get("zile")))
        if (d.get("total") or 0) > len(colete):
            linii.append("  - *(lista completa: orderhub.arona.ro/api/cs/culese-nepredate)*")
        return linii
    except Exception:
        return None


# ---------- 4g. Printate neculese (OH /api/cs/printate-neculese, 24-oct) ----------
def printate_neculese_section():
    """Etichetele PRINTATE pe care nu le-a cules nimeni si pe care curierul nu le-a vazut, de peste o zi lucratoare.

    De ce (24-oct): o comanda eMAG platita statea „printata" din 21-oct, neculeasa; briefingul avea doar „culese dar
    nepredate", iar `picking_neculese` pornea din loturile scanerului (7 etichete fara lot). Sursa e Orders, pe toate
    magazinele. Intoarce None la orice eroare: briefingul nu cade.
    """
    import json, urllib.request
    tok = os.environ.get("OH_CS_SB_TOKEN", "").strip()
    if not tok:
        out, _ = sh(f'{UV} run {HERE}/team-intelligence/plugins/core/scripts/kb.py secret-get OH_CS_SB_TOKEN',
                    timeout=40)
        tok = (out or "").strip().splitlines()[-1].strip() if out else ""
    if not tok:
        return None
    try:
        req = urllib.request.Request("https://orderhub.arona.ro/api/cs/printate-neculese",
                                     headers={"Authorization": "Bearer " + tok, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=25) as r:
            d = json.loads(r.read().decode("utf-8"))
        rand = (d.get("rand_briefing") or "").strip()
        if not rand:
            return None
        linii = ["## Etichete printate neculese", "- 🔴 " + rand]
        # platitele intai (bani deja incasati), apoi cele mai vechi
        colete = sorted(d.get("colete") or [], key=lambda c: (c.get("plata") != "paid", -(c.get("zile") or 0)))[:5]
        for c in colete:
            linii.append("  - `%s` %s (%s) · %s · printat %s zile%s%s" % (
                c.get("awb"), c.get("comanda"), c.get("magazin"), c.get("statie"), c.get("zile"),
                " · PLATITA" if c.get("plata") == "paid" else "",
                " · marcata din curier" if c.get("printat_in") == "curier" else ""))
        if (d.get("total") or 0) > len(colete):
            linii.append("  - *(lista completa: orderhub.arona.ro/api/cs/printate-neculese)*")
        return linii
    except Exception:
        return None


# ---------- alerts (prepend) ----------
def grandia_trepte_section():
    # A/B trepte Grandia: CPA azi + cheltuit/buget pe campaniile de test + SN (control). Fail-safe.
    q = ("SELECT campaign.id, campaign_budget.amount_micros, metrics.cost_micros, metrics.conversions "
         "FROM campaign WHERE campaign.id IN (24385897610,24385897826,23794566678) AND segments.date DURING TODAY")
    cmd = ("set -a; . %s/.env; set +a; "
           "export DATABASE_URL_METRICS=$(grep -m1 ^DATABASE_URL_METRICS= %s/.env|cut -d= -f2-); "
           "%s run %s/gads.py report --customer 9069610821 --format json --query \"%s\"") % (HERE, HERE, UV, HERE, q)
    out, rc = sh(cmd, timeout=120)
    if rc != 0 or "campaign" not in out:
        return []
    import json as _j
    try:
        d = _j.loads(out[out.find("["):out.rfind("]") + 1])
    except Exception:
        return []
    NM = {"24385897610": "IEFTINE STRICT (cpa_mic)", "24385897826": "SCUMP (cpa_mare)", "23794566678": "Shopping-New (control)"}
    order = {"24385897610": 0, "24385897826": 1, "23794566678": 2}
    lines = ["## GRANDIA TREPTE (A/B - CPA azi pe treapta)"]
    for x in sorted(d, key=lambda r: order.get(r["campaign"]["id"], 9)):
        c = x["campaign"]; m = x.get("metrics", {}); b = x.get("campaignBudget", {})
        cost = int(m.get("costMicros", 0)) / 1e6; conv = float(m.get("conversions", 0) or 0)
        bud = int(b.get("amountMicros", 0)) / 1e6
        cpa = cost / conv if conv else 0; pct = int(100 * cost / bud) if bud else 0
        warn = " \U0001F7E1 sub 1/2 buget" if (c["id"] == "24385897610" and bud and pct < 50) else ""
        lines.append("- **%s**: CPA azi %.0f lei - %.0f/%.0f lei (%d%%) - %.0f conv%s" % (NM.get(c["id"], c["id"]), cpa, cost, bud, pct, conv, warn))
    return lines


def alerts_block():
    f=OUT/"alerts.md"
    try:
        txt=f.read_text(encoding="utf-8").strip() if f.exists() else ""
    except Exception:
        txt=""
    if txt:
        return "## 🚨 URGENTE (de rezolvat ACUM)\n"+txt
    return "> 🚨 URGENTE: — niciuna acum —"

def main():
    ads=ads_section(); ha7,stoc=stoc_section(); oh=oh_section()+garda_awb_section()+garda_factura_section()+smartbill_asteptare_section()+cogs_sursa_section()+cogs_engine_section(); jobs=jobs_section()
    ha=ha_section(ha7 or 0)
    infl=influenceri_section(); infl_md=("\n".join(infl)+"\n\n") if infl else ""
    cre=creative_check(); cre_md=("\n".join(cre)+"\n\n") if cre else ""
    sc=scale_confirm(); sc_md=("\n".join(sc)+"\n\n") if sc else ""
    pu=produs_unic_section(); pu_md=("\n".join(pu)+"\n\n") if pu else ""
    u4=umbra_s4_section(); u4_md=("\n".join(u4)+"\n\n") if u4 else ""
    dcf=depozit_config_section(); u4_md+=("\n".join(dcf)+"\n\n") if dcf else ""
    msc=miscare_section(); u4_md+=("\n".join(msc)+"\n\n") if msc else ""
    dlt=depozit_loturi_section(); u4_md+=("\n".join(dlt)+"\n\n") if dlt else ""
    s8=umbra_s8_section(); u4_md+=("\n".join(s8)+"\n\n") if s8 else ""
    gc=cogs_garda_section(); gc_md=("\n".join(gc)+"\n\n") if gc else ""
    so=status_oh_section(); so_md=("\n".join(so)+"\n\n") if so else ""
    pa=pret_automat_section(); pa_md=("\n".join(pa)+"\n\n") if pa else ""
    cnp=culese_nepredate_section(); cnp_md=("\n".join(cnp)+"\n\n") if cnp else ""
    pnc=printate_neculese_section(); cnp_md=(("\n".join(pnc)+"\n\n") if pnc else "")+cnp_md
    gu=grandia_urmarire_section(); gu_md=("\n".join(gu)+"\n\n") if gu else ""
    gt=grandia_trepte_section(); gt_md=("\n".join(gt)+"\n\n") if gt else ""
    so2=scoateri_orfane_section(); so2_md=("\n".join(so2)+"\n\n") if so2 else ""
    lc=livrare_canal_section(); so2_md+=("\n".join(lc)+"\n\n") if lc else ""
    md=f"""# ☀️ De făcut — briefing ARONA
*Generat: {NOW.strftime('%Y-%m-%d %H:%M')} (ora României) · se reîmprospătează 07:45 / 12:00 / 17:00*

{alerts_block()}

## DE DECIS — reclame (CPA vs ținta ta; recomandarea mea lângă fiecare)
{chr(10).join(ads)}

{gt_md}## LICHIDARE HA (vs planul Alex, deadline 05.12)
{chr(10).join(ha)}

## MIȘCARE STOC (bucăți plasate: ieri / 7 zile)
{chr(10).join(stoc)}

## COMENZI (Order Hub)
{chr(10).join(oh)}

{pu_md}{u4_md}{gc_md}{so2_md}{so_md}{pa_md}{cnp_md}{gu_md}{infl_md}{sc_md}{cre_md}## SĂNĂTATE (joburi)
{chr(10).join(jobs)}

*Sursă: ads=gads_portfolio · coadă=Order Hub (/api/cs/sumar-coadă) · viteză stoc=comenzi plasate (AWBprint, interimar — se mută pe OH) · tichete CS = de cablat.*
"""
    (OUT/"latest.md").write_text(md,encoding="utf-8")
    (OUT/f"{NOW.strftime('%Y-%m-%d')}.md").write_text(md,encoding="utf-8")
    print("BRIEFING_OK", NOW.strftime('%Y-%m-%d %H:%M'))

if __name__=="__main__":
    main()
