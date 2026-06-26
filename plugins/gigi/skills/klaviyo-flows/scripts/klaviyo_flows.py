# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31"]
# ///
"""klaviyo_flows.py — CONSTRUIEȘTE flow-urile email/SMS lipsă pe un brand: auditează gap-ul
față de cele 10 flow-uri standard de ecommerce, generează conținutul RO (în vocea brandului)
și CREEAZĂ template-urile de email via API. (API-ul Klaviyo de creare flow complet e limitat →
livrăm template-uri + spec de asamblare; flow-urile se cablează apoi în UI în câteva click-uri.)

Chei: KLAVIYO_<BRAND>_PRIVATE_KEY în KB (avem ESTEBAN, GT).
  uv run klaviyo_flows.py --brand GT --audit                 # ce flow-uri lipsesc
  uv run klaviyo_flows.py --brand GT --build welcome --apply # generează + creează template-urile
  uv run klaviyo_flows.py --brand GT --build all --apply
"""
import os, sys, json, argparse, subprocess
import requests
BASE="https://a.klaviyo.com/api"; REV="2024-10-15"
def kb(k):
    v=os.environ.get(k)
    if v: return v
    kbpy=os.path.join(os.path.dirname(__file__),"..","..","..","..","core","scripts","kb.py")
    return subprocess.run(["uv","run",kbpy,"secret-get",k],capture_output=True,text=True,timeout=60).stdout.strip()
def H(key): return {"Authorization":f"Klaviyo-API-Key {key}","revision":REV,"accept":"application/json","content-type":"application/json"}

VOICE={"ESTEBAN":"lux accesibil — elegant, aspirațional dar cald; 'experiență de designer la o fracțiune din cost'",
       "GT":"influencer energy — direct, prietenos, 'miroase scump dar nu e scump', George ca față"}
URL={"ESTEBAN":"https://esteban.ro","GT":"https://george-talent.ro"}

# cele 10 standard: cheie -> (nume, trigger, emailuri [(întârziere, subiect, corp-RO)])
DISPLAY={"ESTEBAN":"Maison d'Esteban","GT":"GT Parfumuri","NUBRA":"Nubra"}
def flows(brand):
    b=DISPLAY.get(brand,brand.title()); u=URL.get(brand,"")
    return {
     "welcome":("Welcome Series","Added to List",[
        ("imediat",f"Bun venit la {b}! Ai aici -10% 🎁",f"Mulțumim că ni te-ai alăturat! Folosește codul WELCOME10 pentru -10% la prima comandă. Descoperă bestsellerele pe {u}."),
        ("ziua 2",f"Povestea din spatele {b}","De ce {b}? Parfumuri inspirate de cele mai dorite arome, la un preț corect. Vezi cum alegem fiecare aromă."),
        ("ziua 4","Aromele care se vând cel mai des 🔥",f"Nu știi de unde să începi? Iată top 3 cele mai iubite. Codul WELCOME10 încă e valabil — comandă cu ramburs pe {u}.")]),
     "abandoned_cart":("Abandoned Cart","Checkout Started",[
        ("1 oră","Ai uitat ceva în coș 👀","Coșul tău te așteaptă. Finalizează comanda în 2 minute — plată ramburs, livrare rapidă."),
        ("24 ore","Încă te gândești? Uite ce zic clienții ⭐","Mii de comenzi livrate. Termină comanda acum, înainte să se epuizeze."),
        ("48 ore","Ultima șansă: -10% pe coșul tău","Îți păstrăm coșul + un -10% (cod CART10) dacă comanzi azi. După, expiră.")]),
     "browse_abandon":("Browse Abandonment","Viewed Product",[
        ("4 ore","Ai privit ceva frumos… 👀","Aroma pe care ai văzut-o încă e disponibilă. Vezi-o din nou + recomandări asemănătoare."),
        ("24 ore","Poate îți plac și astea","Pe baza a ce ai văzut, credem că ți-ar plăcea și acestea. Plată ramburs.")]),
     "post_purchase":("Post-Purchase","Placed Order",[
        ("imediat","Mulțumim pentru comandă! 🎉","Comanda ta e confirmată. O pregătim de livrare. Iată ce urmează + cum te contactăm."),
        ("ziua 5","Cum să te bucuri la maxim de aroma ta","Sfaturi: unde aplici, cum păstrezi, cât rezistă. Ai întrebări? Suntem aici."),
        ("ziua 14","Cum ți s-a părut? Lasă o părere ⭐","Spune-ne cum a fost — durează 1 minut și ajută enorm. + o recomandare specială pentru tine.")]),
     "winback":("Winback","Metric (no purchase 60d)",[
        ("60 zile","Ne e dor de tine 💌","A trecut ceva timp. Revino cu -15% (cod COMEBACK15) la aromele tale preferate."),
        ("75 zile","Ultima șansă — oferta expiră","COMEBACK15 expiră în 48h. Nu rata aromele care te-au cucerit.")]),
     "back_in_stock":("Back in Stock","Back in Stock",[
        ("imediat","A revenit pe stoc! 🙌","Aroma pe care o așteptai e din nou disponibilă. Comandă acum înainte să se epuizeze iar.")]),
     "birthday":("Birthday","Date property",[
        ("de ziua ta","La mulți ani de la noi! 🎂","Un cadou pentru ziua ta: -20% (cod BDAY20), valabil 7 zile. Răsfață-te.")]),
     "sunset":("Sunset / Re-engagement","Metric (disengaged 120d)",[
        ("120 zile","Mai vrei să auzi de noi?","N-am mai interacționat de ceva timp. Confirmă că vrei să rămâi — altfel îți respectăm liniștea.")]),
    }

STD_NAMES={"welcome":["welcome"],"abandoned_cart":["cart","checkout","abandon"],"browse_abandon":["site abandon","browse","viewed"],
 "post_purchase":["post","thank","placed order","after purchase"],"winback":["winback","win back"],"back_in_stock":["back in stock","stock"],
 "birthday":["birthday","ziua"],"sunset":["sunset","re-engage","disengag"]}

def get_flows(key):
    r=requests.get(f"{BASE}/flows/?fields[flow]=name,status,trigger_type",headers=H(key),timeout=40)
    return [(f["attributes"].get("name","").lower(), f["attributes"].get("status")) for f in r.json().get("data",[])] if r.status_code==200 else []

def make_template(key, name, html):
    body={"data":{"type":"template","attributes":{"name":name,"editor_type":"CODE","html":html}}}
    return requests.post(f"{BASE}/templates/",headers=H(key),json=body,timeout=40)

def email_html(brand, subj, body):
    u=URL.get(brand,"#")
    return f"""<!doctype html><html><body style="font-family:Arial,sans-serif;max-width:600px;margin:auto;padding:24px;color:#222">
<h2>{subj}</h2><p style="font-size:16px;line-height:1.6">{body}</p>
<p><a href="{u}" style="background:#111;color:#fff;padding:12px 28px;text-decoration:none;border-radius:6px;display:inline-block">Comandă acum</a></p>
<p style="color:#888;font-size:12px">{DISPLAY.get(brand,brand.title())} · plată ramburs · livrare rapidă în toată țara</p></body></html>"""

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--brand",required=True); ap.add_argument("--audit",action="store_true")
    ap.add_argument("--build"); ap.add_argument("--apply",action="store_true"); a=ap.parse_args()
    brand=a.brand.upper(); key=kb(f"KLAVIYO_{brand}_PRIVATE_KEY")
    if not key: sys.exit(f"lipsește KLAVIYO_{brand}_PRIVATE_KEY în KB")
    F=flows(brand); existing=get_flows(key)
    def has(fkey):
        return any(any(t in nm for t in STD_NAMES.get(fkey,[fkey])) and st=="live" for nm,st in existing)
    if a.audit or not a.build:
        print(f"\n=== {brand}: audit flow-uri standard ({len(existing)} existente) ===")
        for fkey,(nm,trig,emails) in F.items():
            print(f"  {'✅ LIVE' if has(fkey) else '🔴 LIPSĂ':8} {nm:22} ({len(emails)} emailuri, trigger: {trig})")
        miss=[k for k in F if not has(k)]
        print(f"\n{len(miss)} flow-uri de construit: {', '.join(miss)}")
        print(f"→ construiește: --build <{'|'.join(F)}|all> --apply")
        return
    targets=list(F) if a.build=="all" else [a.build]
    for fkey in targets:
        if fkey not in F: print(f"  necunoscut: {fkey}"); continue
        nm,trig,emails=F[fkey]
        print(f"\n▸ {nm} (trigger: {trig}) — {len(emails)} emailuri:")
        for i,(delay,subj,body) in enumerate(emails,1):
            tname=f"[{brand.title()}] {nm} — email {i} ({delay})"
            print(f"   {i}. {delay:10} | {subj}")
            if a.apply:
                r=make_template(key, tname, email_html(brand,subj,body))
                print(f"      template: {'✓ creat '+r.json()['data']['id'] if r.status_code==201 else '✗ '+r.text[:120]}")
    print(f"\nTemplate-urile sunt create în Klaviyo. Asamblare flow (UI, ~2 min/flow): Flows → Create →")
    print("trigger de mai sus → adaugă email-urile cu template-urile + întârzierile. Sau via API flows (complex).")

if __name__=="__main__":
    main()
