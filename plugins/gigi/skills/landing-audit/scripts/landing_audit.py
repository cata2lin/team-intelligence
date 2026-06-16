# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31", "beautifulsoup4>=4.12"]
# ///
"""Landing Page / Product Page Audit — fetch the page (mobile UA), score the CRO essentials
(offer, price, CTA, trust, reviews, FAQ, urgency, structured data, mobile/tech) and optionally
Core Web Vitals via PageSpeed Insights. Prints a scored checklist + prioritised fixes. Read-only.

    uv run landing_audit.py --url https://esteban.ro/products/esteban-essential-barbati-35
    uv run landing_audit.py --url https://belasil.ro/products/... --speed     # + PSI mobile (slow)

Tune the RO trust/urgency keyword lists with --trust / --urgency. The HTML heuristics suit Shopify
themes; for the *visual* above-the-fold judgement, complement with the chrome-devtools MCP
(screenshot the mobile viewport) — see SKILL.md.
"""
import os, sys, argparse, re, json
import requests
from bs4 import BeautifulSoup

UA="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"

def jsonld(soup):
    out=[]
    for s in soup.find_all("script",{"type":"application/ld+json"}):
        try:
            d=json.loads(s.string or "{}"); out+= d if isinstance(d,list) else [d]
        except Exception: pass
    return out

def has_type(blocks,t):
    for b in blocks:
        ty=b.get("@type","");
        if (t==ty) or (isinstance(ty,list) and t in ty): return b
        for g in (b.get("@graph") or []):
            gy=g.get("@type","")
            if t==gy or (isinstance(gy,list) and t in gy): return g
    return None

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--url",required=True)
    ap.add_argument("--speed",action="store_true",help="run PageSpeed Insights (mobile) — adds ~15s")
    ap.add_argument("--trust",default="transport gratuit,retur,garan, ramburs,plata la livrare,securizat,livrare 24,livrare in")
    ap.add_argument("--urgency",default="stoc limitat,ultimele,doar azi,se termina,countdown,oferta expira")
    a=ap.parse_args()
    r=requests.get(a.url,headers={"User-Agent":UA},timeout=30)
    html=r.text; low=html.lower(); soup=BeautifulSoup(html,"html.parser")
    blocks=jsonld(soup)
    text=soup.get_text(" ",strip=True).lower()
    checks=[]  # (group, ok, label, fix)
    def chk(group,ok,label,fix=""): checks.append((group,bool(ok),label,fix))

    # OFFER / PRICE / CTA
    h1=soup.find("h1")
    chk("OFERTĂ",h1 and h1.get_text(strip=True),"H1 / titlu produs prezent","adaugă un H1 clar cu numele produsului")
    price=re.search(r'(\d[\d.\s]*)\s*(lei|ron|€)',text) or has_type(blocks,"Product")
    chk("OFERTĂ",price,"preț vizibil","arată prețul clar above-the-fold")
    cta=re.search(r'(adaug[aă] [iî]n co[sș]|cump[aă]r[aă]|comand[aă]|add to cart|buy now)',text)
    chk("OFERTĂ",cta,"CTA de cumpărare prezent","buton clar „Adaugă în coș/Cumpără acum”")
    chk("OFERTĂ",re.search(r'2\s*\+\s*1|3\s*\+|gratis|reducere|-\d+%|economis',text),"ofertă/promo comunicată","comunică oferta (2+1, % reducere) sus")

    # TRUST
    tk=[t.strip() for t in a.trust.split(",") if t.strip()]
    hits=[t for t in tk if t in text]
    chk("TRUST",len(hits)>=2,f"semnale de încredere ({len(hits)}: {', '.join(hits[:4])})","adaugă transport gratuit / retur / garanție / plata ramburs")
    # REVIEWS
    agg=has_type(blocks,"AggregateRating") or (has_type(blocks,"Product") or {}).get("aggregateRating")
    nrev=None
    if isinstance(agg,dict): nrev=agg.get("reviewCount") or agg.get("ratingCount")
    rev_text=re.search(r'(\d+)\s*(recenzii|review|p[aă]reri|evalu[aă]ri)',text)
    chk("REVIEWS",agg or rev_text,"recenzii prezente"+(f" ({nrev} în schema)" if nrev else ""),"adaugă recenzii + AggregateRating (rich results stele)")
    # FAQ / OBJECTIONS
    faq=has_type(blocks,"FAQPage") or ("faq" in low) or ("[i]ntreb[aă]ri frecvente" in text) or ("întrebări frecvente" in text)
    chk("OBIECȚII",faq,"secțiune FAQ / întrebări","adaugă FAQ (livrare, retur, autenticitate, persistență) + FAQPage schema")
    # URGENCY
    uk=[u.strip() for u in a.urgency.split(",") if u.strip()]
    chk("URGENȚĂ",any(u in text for u in uk),"urgență/scarcity","adaugă stoc limitat / countdown la ofertă (cu măsură)")
    # STRUCTURED DATA
    prod=has_type(blocks,"Product")
    chk("SEO/RICH",prod,"Product schema (JSON-LD)","adaugă Product + Offer JSON-LD")
    chk("SEO/RICH",prod and (prod.get("offers")),"Offer (preț/disponibilitate) în schema","include offers{price,availability} pt rich results")
    # MOBILE / TECH
    vp=soup.find("meta",{"name":"viewport"})
    chk("MOBIL/TECH",vp,"viewport meta (mobile)","adaugă <meta name=viewport>")
    imgs=soup.find_all("img"); lazy=[i for i in imgs if (i.get("loading")=="lazy")]
    chk("MOBIL/TECH",len(imgs)==0 or len(lazy)>=len(imgs)*0.5,f"lazy-loading imagini ({len(lazy)}/{len(imgs)})","lazy-load imaginile sub fold (viteză mobil)")
    md=soup.find("meta",{"name":"description"})
    chk("SEO/RICH",md and md.get("content"),"meta description","adaugă meta description orientată pe ofertă")

    # PageSpeed Insights (optional)
    psi=None
    if a.speed:
        try:
            params={"url":a.url,"strategy":"mobile","category":"performance"}
            key=os.environ.get("PSI_API_KEY") or os.environ.get("GADS_GOOGLE_API_KEY")
            if key: params["key"]=key
            pr=requests.get("https://www.googleapis.com/pagespeedonline/v5/runPagespeed",params=params,timeout=60).json()
            if "error" in pr: psi={"err":pr["error"].get("message","?")[:90]}
            elif "lighthouseResult" not in pr: psi={"err":"fără lighthouseResult"}
            else:
                lh=pr["lighthouseResult"]; aud=lh.get("audits",{})
                psi={"score":round((lh.get("categories",{}).get("performance",{}).get("score") or 0)*100),
                     "lcp":aud.get("largest-contentful-paint",{}).get("displayValue"),
                     "cls":aud.get("cumulative-layout-shift",{}).get("displayValue"),
                     "tbt":aud.get("total-blocking-time",{}).get("displayValue")}
        except Exception as e: psi={"err":str(e)[:80]}

    # report
    print(f"\n=== Landing Audit · {a.url} ===")
    groups={}
    for g,ok,lab,fix in checks: groups.setdefault(g,[]).append((ok,lab,fix))
    passed=sum(1 for _,ok,_,_ in checks if ok); total=len(checks)
    for g,items in groups.items():
        print(f"\n  [{g}]")
        for ok,lab,fix in items: print(f"    {'✓' if ok else '✗'} {lab}"+("" if ok else f"   → {fix}"))
    if psi:
        if psi.get("err"): print(f"\n  [VITEZĂ] PSI eroare: {psi['err']}")
        else: print(f"\n  [VITEZĂ mobil] scor {psi['score']}/100 · LCP {psi['lcp']} · CLS {psi['cls']} · TBT {psi['tbt']}")
    print(f"\n  SCOR CRO: {passed}/{total}")
    fixes=[fix for _,ok,_,fix in checks if not ok and fix]
    if fixes:
        print("  PRIORITĂȚI:")
        for f in fixes[:8]: print(f"    • {f}")

if __name__=="__main__":
    main()
