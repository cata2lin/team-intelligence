#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31", "psycopg2-binary>=2.9"]
# ///
"""
Lab Noir — rewrite the 5 published articles:
  - strip Romanian diacritics (a, i, s, t, e variants)
  - remove <hr> separators
  - shorter sentences, more scannable
  - SEO-tighter (keyword in title, lead, h2s, summary)
  - keep handles unchanged (URLs stable), keep author + tags + image
"""
from __future__ import annotations

import json
import os
import sys
import unicodedata
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).parent))
from kb_env import load_secrets_into_env  # noqa: E402
load_secrets_into_env()

DOMAIN  = os.environ["SHOPIFY_ARONA_LABNOIR_DOMAIN"]
VERSION = os.environ["SHOPIFY_ARONA_API_VERSION"]
CID = os.environ["SHOPIFY_ARONA_CLIENT_ID"]
CSE = os.environ["SHOPIFY_ARONA_CLIENT_SECRET"]
CDN = "https://cdn.shopify.com/s/files/1/0789/9427/6581/files"


def strip_diacritics(s: str) -> str:
    """Romanian: a-breve, i-circ, a-circ, s-comma, t-comma → a, i, a, s, t.
    Also normalizes em/en-dashes and curly quotes to ASCII for clean copy."""
    nfkd = unicodedata.normalize("NFKD", s)
    out = "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    return (out
            .replace("\u0219", "s").replace("\u021B", "t")
            .replace("\u0218", "S").replace("\u021A", "T")
            .replace("\u015F", "s").replace("\u0163", "t")
            .replace("\u015E", "S").replace("\u0162", "T")
            .replace("\u2014", "-").replace("\u2013", "-")
            .replace("\u2018", "'").replace("\u2019", "'")
            .replace("\u201C", '"').replace("\u201D", '"'))


def mint() -> str:
    r = requests.post(
        f"https://{DOMAIN}/admin/oauth/access_token",
        json={"client_id": CID, "client_secret": CSE, "grant_type": "client_credentials"},
        timeout=20)
    r.raise_for_status()
    return r.json()["access_token"]


def gql(token: str, query: str, variables: dict | None = None) -> dict:
    r = requests.post(
        f"https://{DOMAIN}/admin/api/{VERSION}/graphql.json",
        headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
        json={"query": query, "variables": variables or {}}, timeout=30)
    r.raise_for_status()
    out = r.json()
    if "errors" in out:
        raise RuntimeError(json.dumps(out["errors"], indent=2))
    return out["data"]


# ──────────────────────────────────────────────────────────────────────────────
# REWRITES (no diacritics, short sentences, SEO H2s, no <hr>)
# ──────────────────────────────────────────────────────────────────────────────

ARTICLES = [
# 1) Blend No. 2 — tutun + vanilie ──────────────────────────────────────────────
{
    "id": "gid://shopify/Article/607589433573",
    "title": "Parfum cu tutun si vanilie: anatomia unui oriental devenit cult",
    "summary": "Tutun de pipa, vanilie densa, lemn cald. Profilul oriental "
               "care a definit ultimele doua decenii. Reinterpretat in Blend No. 2.",
    "tags": ["oriental", "gourmand", "vanilie", "tutun", "unisex", "seara"],
    "image_alt": "Frunze de tutun uscat si vanilie — Blend No. 2 Lab Noir",
    "body": """\
<p>Sunt parfumuri pe care le miroses o singura data si ti se par cunoscute. Tutun uscat. Vanilie groasa. Cacao. Piele calda, ca un fotoliu vechi intr-un cabinet cu carti.</p>

<p>Profilul a devenit un punct de referinta in parfumeria orientala moderna. A fost lansat la mijlocul anilor 2000, in linia privata a unui designer american. Pasionatii stiu deja despre ce vorbim. Pentru toti ceilalti, conteaza cum este construit.</p>

<h2>Note de top: tutun uscat si mirodenii</h2>

<p>In deschidere, tutunul nu apare ca un fum agresiv. Este o frunza uscata, dulce, usor mierata. Genul de miros pe care il are o cutie de pipa deschisa pentru prima oara.</p>

<p>Langa tutun, mirodeniile dau profunzime. Un strop de cuisoare. Un strop de coriandru. Imediat compozitia capata adancime.</p>

<h2>Note de inima: vanilie densa si cacao</h2>

<p>Vanilia preia controlul. Nu este vanilia de cofetarie. Este o vanilie densa, masculina, cu un strop de cacao amar si fasole tonka.</p>

<p>Aici parfumul devine atmosfera. Devine un decor, nu un miros. Asta il face memorabil.</p>

<h2>Note de baza: lemn dulce si ambra fumurie</h2>

<p>Cedrul, lemnul de trandafir si o ambra fumurie tin compozitia pe piele ore in sir. Sillage-ul nu este zgomotos. Este o prezenta. Te face sa fii observat de cei care se apropie, nu de toata camera.</p>

<h2>Ce am ajustat in laborator</h2>

<p>Profilul original a influentat zeci de creatii care au aparut dupa el. Vanilia tutunata a devenit un gen olfactiv intreg.</p>

<p>In laboratorul nostru am tinut tutunul cremos si vanilia densa. Am ajustat partea fumurie sa fie mai purtabila pe parcursul unei zile intregi. Am adaugat o urma de fum de mesteacan, care da caracter compozitiei fara sa o faca obositoare.</p>

<h2>Cand porti acest parfum</h2>

<p>Este, prin natura lui, un parfum de toamna-iarna. Inflorente pe pielea calda, in atmosfera rece. Vara devine prea dens.</p>

<p>Functioneaza pe oricine apreciaza profilurile profunde, indiferent de gen. Te face mai linistit, mai atent, mai prezent in secunda in care il pui.</p>

<h2>De unde sa incepi</h2>

<p>Daca explorezi pentru prima oara genul oriental gourmand, <a href="/products/labnoir-2"><strong>Blend No. 2</strong></a> este un punct de plecare onest. 50 ml, lot mic, productie manuala.</p>

<p>Daca iubesti deja profilurile dulci-fumurii, incearca in paralel si <a href="/products/labnoir-3">Blend No. 3</a>: o reinterpretare cu cognac si scortisoara, din aceeasi familie de seara.</p>
""",
},

# 2) Blend No. 4 — citric solar ─────────────────────────────────────────────────
{
    "id": "gid://shopify/Article/607589466341",
    "title": "Parfum citric solar pentru vara: profilul mediteranean unisex",
    "summary": "Lamaie de Sicilia, ambra dulce, fructe mediteraneene. Un "
               "profil unisex care functioneaza din martie pana in octombrie. "
               "Reinterpretat in Blend No. 4.",
    "tags": ["citric", "solar", "fructat", "ambra", "unisex", "vara"],
    "image_alt": "Lamai mediteraneene si textura de iarba — Blend No. 4 Lab Noir",
    "body": """\
<p>Sunt parfumuri pe care nu le porti, le locuiesti. Le pui dimineata, inainte de cafea. Raman cu tine toata ziua, fara sa ceara atentie. Te fac mai mult tine.</p>

<p>Profilul pe care il reinterpretam aici este unul dintre acelea. A aparut intr-o casa italiana din Milano. A devenit cult printre pasionatii care cauta citrice adulte. Adica citrice care nu se evapora in 30 de minute.</p>

<h2>De ce este special un parfum citric solar</h2>

<p>Cele mai multe parfumuri citrice mor repede. Bergamotul, lamaia si mandarina sunt molecule volatile. Sunt frumoase la deschidere si dispar dupa o ora.</p>

<p>Marii parfumieri italieni au rezolvat problema asta. Au pus citricele intr-un pat de ambra dulce si fructe mediteraneene. Aceste note tin parfumul pe piele ore in sir.</p>

<h2>Note de top: lamaie siciliana si bergamot</h2>

<p>La inceput, lamaia siciliana, mandarina si un strop de bergamot. Pielea reactioneaza imediat. Parfumul straluceste pe incheietura.</p>

<h2>Note de inima: pere coapte si smochine</h2>

<p>In inima apar fructele moi. Pere coapte. Smochine. O piersica foarte discreta. Aici parfumul se rotunjeste.</p>

<p>De aici incepe partea care il face memorabil. Cand cineva spune <em>miroses a vacanta</em>, vorbeste despre nota asta de fructe coapte in soare.</p>

<h2>Note de baza: ambra dulce si mosc alb</h2>

<p>In baza, ambra dulce, mosc alb si o urma de iarba uscata. Aceasta baza este motivul pentru care profilul rezista opt-zece ore pe pielea calda. Si nu devine niciodata dulce-greu.</p>

<h2>Pentru cine este</h2>

<p>Profilul este profund unisex. Il porti la birou luni dimineata. Il porti sambata seara la o terasa. Nu trebuie sa schimbi nimic.</p>

<p>Daca explorezi parfumeria de nisa pentru prima data, citricele solare sunt cea mai onesta rampa de lansare. Sunt purtabile pe oricine. Sunt complimente garantate.</p>

<h2>Ce am ajustat in laborator</h2>

<p>Profilul original are un punct delicat. In primele 5–10 minute, deschiderea poate fi prea acida pe pielea uscata. Am ajustat partea de varf cu o picatura de neroli. Aciditatea se modereaza, stralucirea ramane.</p>

<p>Am tinut intentionat partea de fructe moi exact asa cum este. Aici sta magia: pera coapta pe care nu o numesti, dar pe care creierul o recunoaste.</p>

<h2>De unde sa incepi</h2>

<p>Daca vrei un singur parfum pentru toata primavara si toata vara, <a href="/products/labnoir-4"><strong>Blend No. 4</strong></a> este o alegere pe care nu o regreti. Lot mic, productie manuala, sticla de 50 ml.</p>

<p>Daca vrei un contrast in aceeasi gama unisex, incearca si <a href="/products/labnoir-30">Blend No. 30</a>. Profil mai mineral, cu lavanda si ambroxan. Complet diferit ca atmosfera, la fel de purtabil.</p>
""",
},

# 3) Blend No. 3 — cognac + scortisoara ─────────────────────────────────────────
{
    "id": "gid://shopify/Article/607589499109",
    "title": "Cota ingerilor: parfumul cu cognac, scortisoara si lemn dulce",
    "summary": "Cognac, scortisoara, fasole tonka, lemn de stejar. Un profil "
               "narativ inspirat de pivnitele franceze. Reinterpretat in "
               "Blend No. 3.",
    "tags": ["gourmand", "cognac", "lemnos", "scortisoara", "unisex", "seara"],
    "image_alt": "Pahar de cognac si fasole tonka pe lemn — Blend No. 3 Lab Noir",
    "body": """\
<p>In pivnitele de cognac din vestul Frantei exista un fenomen. Producatorii il numesc <em>la part des anges</em>. Cota ingerilor.</p>

<p>Este cantitatea de alcool care se evapora din butoaiele de stejar in fiecare an. Doi-trei procente. In cateva zeci de ani, jumatate dintr-un butoi devine aer. Producatorii traiesc cu pierderea asta ca si cum ar fi un ritual.</p>

<p>Un parfumier francez celebru a transformat imaginea asta intr-un parfum. Profilul a devenit un punct de referinta pentru gourmand-urile cu caracter. Iar narativul din spate este unul dintre cele mai bune din parfumeria moderna.</p>

<h2>Note de top: cognac proaspat si scortisoara</h2>

<p>Deschiderea este imediat recognoscibila. Cognac proaspat, usor intepator. Un strop de fum. Un strop de scortisoara.</p>

<p>Nu este o imitatie de bautura. Este senzatia de a ridica un pahar la nivelul nasului intr-o sufragerie calda.</p>

<h2>Note de inima: fasole tonka si vanilie</h2>

<p>Tonka si o vanilie discreta rotunjesc partea alcoolica. O transforma intr-o caldura caramelizata.</p>

<p>Aici parfumul devine prietenos. Acolo unde alte gourmand-uri sunt agresiv-dulci, acesta ramane adult.</p>

<h2>Note de baza: lemn de stejar si mosc cremos</h2>

<p>In baza, stejarul si moscul cremos. Stejarul este detaliul tehnic. Tine profilul langa o pivnita, nu langa o vitrina de cofetarie.</p>

<h2>De ce functioneaza</h2>

<p>Exista parfumuri care iti spun o poveste. Exista parfumuri care iti construiesc o camera in jurul tau. Acest profil face al doilea lucru.</p>

<p>Cand il pui, atmosfera din jurul tau se schimba subtil. Devine mai calda. Mai privata. Mai linistita.</p>

<p>Functioneaza superb seara. Nu este un parfum de ofensiva. Construieste intimitate. Il simt cei care se apropie.</p>

<h2>Ce am reinterpretat</h2>

<p>Am tinut nucleul intact: cognac, scortisoara, tonka. Am ajustat partea de stejar pentru mai mult contur lemnos pe parcursul orelor. Am redus usor nota fumurie din varf, care in versiunea originala poate fi prea pronuntata pentru pielile sensibile.</p>

<p>Rezultatul: un parfum mai purtabil pe parcursul unei seri intregi, fara sa piarda caracterul.</p>

<h2>Cand porti acest parfum</h2>

<p>Toamna. Iarna. Inceput de primavara. Seara tarzie. Masa lunga. Conversatii cu oameni care conteaza.</p>

<p>Functioneaza pe oricine, indiferent de gen. O femeie cu un trench negru. Un barbat cu un pulover de casmir. Aceeasi atmosfera.</p>

<h2>De unde sa incepi</h2>

<p><a href="/products/labnoir-3"><strong>Blend No. 3</strong></a> este un punct de intrare onest in categoria gourmand-urilor cu narativ. 50 ml, lot mic, productie manuala in laboratorul nostru.</p>

<p>Daca te atrage profilul, incearca in paralel si <a href="/products/labnoir-2">Blend No. 2</a>. Tot oriental, tot de seara. Pe directie de tutun si vanilie densa, in loc de cognac.</p>
""",
},

# 4) Blend No. 7 — vanilie cremoasa moderna ─────────────────────────────────────
{
    "id": "gid://shopify/Article/607589531877",
    "title": "Parfum cu vanilie cremoasa si lemn alb: skin scent modern",
    "summary": "Nu e vanilia clasica. Este o vanilie texturata: lapte cald, "
               "lemn alb, ambra transparenta, piele calda. Reinterpretata in "
               "Blend No. 7.",
    "tags": ["vanilie", "ambra", "gourmand", "modern", "feminin", "skin scent"],
    "image_alt": "Vanilie, ambra si lemn alb — Blend No. 7 Lab Noir",
    "body": """\
<p>Intreaba pe oricine ce miroase a vanilie. O sa primesti acelasi raspuns. Prajitura. Inghetata. Cofetarie. Pentru majoritatea oamenilor, vanilia este un miros copilaresc, dulce si previzibil.</p>

<p>Dar exista o intreaga scoala de parfumieri care lucreaza vanilia altfel. Pentru ei, vanilia nu este un gust. Este o textura. Este pielea calda dupa dus. Este laptele caldut intr-un pahar inalt. Este interiorul unei dimineti tarzii.</p>

<p>Profilul pe care il reinterpretam este, in ultimii ani, exemplul cel mai bun al acestei scoli. A fost lansat in linia privata a unei mari case americane, sub un nume deliberat provocator. Pasionatii stiu.</p>

<h2>Vanilia ca textura, nu ca gust</h2>

<p>In deschidere, profilul nu este dulce. Este moale.</p>

<p>Apare un mosc cremos. O urma de lapte cald. O senzatie de bumbac proaspat. Creierul tau nu identifica imediat <em>vanilie</em>. Identifica <em>cald</em>. Aici sta magia compozitiei.</p>

<h2>Note de inima: vanilie boaba si lemn alb</h2>

<p>Treptat, in inima, intra vanilia adevarata. Nu vanilia de cofetarie. O vanilie boaba, usor afumata, cu textura de fasole tonka.</p>

<p>Langa ea apare un lemn alb. Casmir. Sandal foarte transparent. Verticalitate.</p>

<h2>Note de baza: ambra moale si mosc laptos</h2>

<p>In baza, ambra moale si moscul laptos creeaza ceea ce parfumierii numesc <em>skin scent</em>. Un parfum care, dupa cateva ore, miroase a piele proprie. Doar mai buna.</p>

<p>Este motivul pentru care oamenii care il poarta primesc constant intrebarea <em>ce parfum ai?</em>.</p>

<h2>De ce functioneaza atat de bine</h2>

<p>Profilul nu este foarte proeminent. Nu cucereste o camera. Cucereste un metru si jumatate.</p>

<p>Sillage-ul este intentionat intim. Tu, un mic nor de senzualitate. Si oricine se apropie suficient cat sa te auda vorbind.</p>

<p>Asta il face mai puternic. Nu vanilia adolescenta de la mall. Vanilia adulta pe care o miroses pe cineva important pentru tine.</p>

<h2>Ce am ajustat in laborator</h2>

<p>Am tinut nucleul aproape identic: vanilie, mosc laptos, lemn alb. Am amplificat usor partea de tonka pentru mai multa caldura pe timp rece. Am echilibrat moscul ca sa nu domine pe pielile foarte calde.</p>

<p>Rezultatul functioneaza in orice sezon. Vara devine senzatie de pat racoros dupa dus. Iarna devine un strat invizibil de bumbac sub palton.</p>

<h2>Pentru cine este</h2>

<p>Predominant feminin, dar nu exclusiv. Functioneaza pe orice piele care apreciaza textura in locul declaratiei.</p>

<p>Nu este un parfum care striga. Este un parfum care sopteste.</p>

<p>Functioneaza in orice context. Dimineata devreme. Intalniri lungi. Seri linistite. Weekend in pijamale si carti. Il porti pentru tine, nu pentru ceilalti.</p>

<h2>De unde sa incepi</h2>

<p><a href="/products/labnoir-7"><strong>Blend No. 7</strong></a> este o reinterpretare onesta a unei vanilii moderne. Lot mic, 50 ml, productie manuala. Daca nu ai purtat niciodata un <em>skin scent</em>, este parfumul cu care recomandam sa incepi.</p>

<p>Daca vrei sa explorezi ramura mai dulce-fructata a aceleiasi familii, incearca si <a href="/products/labnoir-91">Blend No. 91</a>. Vanilie cu cirese negre si migdale amare. Tot textura. Pe directie de cofetarie eleganta.</p>
""",
},

# 5) Blend No. 71 — miere + portocala ──────────────────────────────────────────
{
    "id": "gid://shopify/Article/607589564645",
    "title": "Parfum cu miere si portocala amara: profilul floral-mierat de seara",
    "summary": "Miere calda, portocala amara, gardenia, caramel. Un profil "
               "floral-mierat semnat de o casa pariziana. Reinterpretat in "
               "Blend No. 71.",
    "tags": ["floral", "miere", "fructat", "feminin", "gourmand", "seara"],
    "image_alt": "Miere calda, gardenia si portocala amara — Blend No. 71 Lab Noir",
    "body": """\
<p>Sunt parfumuri pe care le pui cand vrei sa fii observata. Si sunt parfumuri pe care le pui cand <em>stii</em> ca o sa fii observata. Si nu iti pasa.</p>

<p>Profilul pe care il reinterpretam apartine clar celei de-a doua categorii. A fost lansat de o casa pariziana de moda. Cunoscuta pentru flacoane sculpturale si pentru o estetica deliberat provocatoare. Pasionatii il recunosc imediat dupa descriere.</p>

<h2>De ce un parfum cu miere?</h2>

<p>Mierea este una dintre cele mai dificile note in parfumerie. Foarte usor aluneca in zona <em>prea dulce</em>, <em>prea greu</em>, <em>prea adolescent</em>.</p>

<p>Dar un parfumier bun poate face din miere unul dintre cele mai senzuale ingrediente. Acest profil este dovada.</p>

<h2>Note de top: mandarina, fructe rosii si bujor</h2>

<p>Deschiderea este vibranta. Mandarina. Fructe rosii. Un strop de bujor proaspat.</p>

<p>Nu este o deschidere de fata cuminte. Are deja o intentie.</p>

<h2>Note de inima: miere, portocala amara si gardenia</h2>

<p>Mierea apare in inima. Este temperata din doua directii.</p>

<p>Portocala amara taie dulceata cu o aciditate aproape parfumata. Gardenia, o floare alba cu textura cremoasa, are o nota verde subtila care pastreaza profilul adult.</p>

<p>Asa primeste mierea echilibru. Asa devine senzuala si nu obositoare.</p>

<h2>Note de baza: pacholi, vanilie si ambra calda</h2>

<p>Spre seara, in baza apar pacholi-ul cremos, vanilia si o ambra calda. Sillage-ul ramane consistent. Dar nu devine niciodata greu.</p>

<p>Este un parfum construit pentru a fi purtat de la apus pana la cinci dimineata.</p>

<h2>Ce am reinterpretat</h2>

<p>In versiunea originala, mierea poate fi prea pronuntata in primele 30 de minute. Pe pielile sensibile devine obositoare.</p>

<p>In laboratorul nostru am ajustat varful cu o picatura suplimentara de mandarina verde. Compozitia castiga aer.</p>

<p>Am tinut intentionat gardenia si portocala amara exact asa cum sunt. Sunt cele doua coloane vertebrale ale profilului.</p>

<h2>Cum se poarta pe piele</h2>

<p>In prima ora, parfumul este vibrant. Fructat. Floral. Usor capricios. Intri intr-o camera si oamenii ridica privirea fara sa isi dea seama de ce.</p>

<p>Dupa doua-trei ore, lucrurile se aseaza. Mierea se rotunjeste. Gardenia devine cremoasa. Portocala amara lasa in urma o tenta caramelizata.</p>

<p>Aici incepe faza pe care o iubim cel mai mult. Mai putin spectacol. Mai mult prezenta.</p>

<h2>Pentru cine este</h2>

<p>Predominant feminin. Functioneaza cel mai bine pe pielea calda, in lumina scazuta, in atmosfera cu intentie.</p>

<p>Nu este un parfum de birou. Este un parfum de inceput de seara. Momentul in care iti pui o pereche de cercei. Si te uiti o ultima data in oglinda inainte sa iesi.</p>

<h2>De unde sa incepi</h2>

<p><a href="/products/labnoir-71"><strong>Blend No. 71</strong></a> este o reinterpretare in lot mic, 50 ml, a profilului floral-mierat. Pentru cei care colectioneaza parfumuri cu personalitate, nu cu ambiguitate.</p>

<p>Daca te atrage directia dulce-feminina dar vrei un contrast mai cremos si modern, incearca si <a href="/products/labnoir-73">Blend No. 73</a>. Tubercul, cacao si iasomie. Alta casa, aceeasi familie de seara.</p>
""",
},
]


ARTICLE_UPDATE = """
mutation articleUpdate($id: ID!, $article: ArticleUpdateInput!) {
  articleUpdate(id: $id, article: $article) {
    article {
      id
      handle
      title
      isPublished
      tags
    }
    userErrors { field message code }
  }
}
"""


def main():
    token = mint()
    print(f"token minted ({token[:14]}...)\n")
    for i, a in enumerate(ARTICLES, start=1):
        title = strip_diacritics(a["title"])
        summary = strip_diacritics(a["summary"])
        body = strip_diacritics(a["body"])
        tags = [strip_diacritics(t) for t in a["tags"]]
        image_alt = strip_diacritics(a["image_alt"])

        # sanity: no leftover diacritics, no <hr>
        leftovers = [c for c in (title + summary + body) if ord(c) > 127]
        assert not leftovers, f"non-ASCII chars left: {set(leftovers)}"
        assert "<hr" not in body.lower(), "<hr> still present"

        print(f"[{i}/5] {title}  ({len(body)} chars)")
        variables = {
            "id": a["id"],
            "article": {
                "title":   title,
                "body":    body,
                "summary": summary,
                "tags":    tags,
                "image":   {"altText": image_alt},  # keep same image, only refresh alt
            },
        }
        data = gql(token, ARTICLE_UPDATE, variables)
        result = data["articleUpdate"]
        if result["userErrors"]:
            print("  USER ERRORS:")
            for e in result["userErrors"]:
                print(f"    {e['field']} [{e.get('code')}]: {e['message']}")
            sys.exit(1)
        art = result["article"]
        print(f"  ok  /jurnal/{art['handle']}  published={art['isPublished']}")
    print("\nALL 5 ARTICLES UPDATED")


if __name__ == "__main__":
    main()
