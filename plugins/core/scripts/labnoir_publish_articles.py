#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31", "psycopg2-binary>=2.9"]
# ///
"""
Lab Noir — publish 5 editorial blog articles.

Tone: per projects/labnoir.ro/labnoir.md (parfumuri cu gust, reinterpretare,
laborator, descoperire). Original brand names of the inspirations are NEVER
mentioned; only profile / era / origin descriptors that an enthusiast can
recognize.

Each article: title, ~3500-4500 char HTML body, summary, tags, featured image
URL (from store Files), and a CTA link to the product + 1 cross-sell.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).parent))
from kb_env import load_secrets_into_env  # noqa: E402
load_secrets_into_env()

DOMAIN  = os.environ["SHOPIFY_ARONA_LABNOIR_DOMAIN"]
VERSION = os.environ["SHOPIFY_ARONA_API_VERSION"]
CLIENT_ID     = os.environ["SHOPIFY_ARONA_CLIENT_ID"]
CLIENT_SECRET = os.environ["SHOPIFY_ARONA_CLIENT_SECRET"]

BLOG_ID = "gid://shopify/Blog/101917556965"   # Jurnal Lab Noir
AUTHOR  = "ARONA SRL ADMIN"
CDN     = f"https://cdn.shopify.com/s/files/1/0789/9427/6581/files"

def mint() -> str:
    r = requests.post(
        f"https://{DOMAIN}/admin/oauth/access_token",
        json={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
              "grant_type": "client_credentials"},
        timeout=20)
    r.raise_for_status()
    return r.json()["access_token"]

def gql(token: str, query: str, variables: dict | None = None) -> dict:
    r = requests.post(
        f"https://{DOMAIN}/admin/api/{VERSION}/graphql.json",
        headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
        json={"query": query, "variables": variables or {}},
        timeout=30)
    r.raise_for_status()
    out = r.json()
    if "errors" in out:
        raise RuntimeError(json.dumps(out["errors"], indent=2))
    return out["data"]


# ──────────────────────────────────────────────────────────────────────────────
# ARTICLES
# ──────────────────────────────────────────────────────────────────────────────

ARTICLES: list[dict] = [
# ───── 1. BLEND No. 2 — oriental gourmand fumat (tutun + vanilie + cacao) ─────
{
    "title": "Tutun, vanilie și piele: anatomia unui oriental devenit cult",
    "handle": "tutun-vanilie-piele-anatomia-unui-oriental-devenit-cult",
    "summary": "Un profil olfactiv care a definit ultimele două decenii de "
               "parfumerie orientală: tutun de pipă, vanilie densă, lemn cald. "
               "Reinterpretat în Blend No. 2.",
    "tags": ["oriental", "gourmand", "vanilie", "tutun", "unisex", "seara"],
    "image_url": f"{CDN}/002_tabacco_vanille_01.jpg",
    "image_alt": "Frunze de tutun uscat și vanilie pe textură de hârtie — Blend No. 2 Lab Noir",
    "body": """\
<p>Există parfumuri pe care le miroși o singură dată și ți se pare că le-ai cunoscut dintotdeauna. Tutun uscat. Vanilie groasă. O urmă de cacao. Piele caldă, ca un fotoliu vechi într-un cabinet cu cărți.</p>

<p>Profilul este atât de bine construit încât a devenit un punct de referință în parfumeria orientală modernă. A fost lansat la mijlocul anilor 2000, în linia privată a unui designer american devenit între timp un personaj central al modei și al fragranței. Nu îl numim aici. Pasionații știu deja la ce ne referim. Pentru toți ceilalți, contează altceva: ce face profilul ăsta atât de memorabil.</p>

<hr>

<h2>Anatomia unui oriental</h2>

<p>În deschidere, frunza de tutun nu apare ca un fum agresiv. Apare ca o frunză uscată, dulce, ușor mierată — felul în care miroase o cutie de pipă deschisă pentru prima oară. Lângă ea, mirodeniile (un strop de cuișoare, un strop de coriandru) ridică imediat senzația de profunzime.</p>

<p>În inimă, vanilia preia controlul. Nu este vanilia liniară de cofetărie. Este o vanilie densă, „masculină", echilibrată cu o notă de cacao amar și cu fasole tonka. Aici parfumul devine o atmosferă, nu un miros — devine un decor.</p>

<p>În bază, lemnele dulci (cedru, lemn de trandafir) și o ambră fumurie țin compoziția pe piele ore în șir. Sillage-ul nu este zgomotos: este o prezență care îi face pe ceilalți să se apropie, nu să se întoarcă.</p>

<hr>

<h2>De ce am ținut să îl reinterpretăm</h2>

<p>Profilul original a influențat zeci de creații care au apărut după el. Vanilia tutunată a devenit un întreg gen olfactiv. Pentru noi, în laborator, întrebarea nu a fost „cât de fidel îl putem reproduce". A fost: <em>ce face acest parfum special, și cum păstrăm exact acel lucru</em>.</p>

<p>Am ținut tutunul cremos și vanilia densă. Am ajustat partea fumurie ca să fie mai puțin teatrală și mai purtabilă pe parcursul unei zile întregi. Am adăugat o urmă de fum de mesteacăn care dă caracter compoziției fără să o facă agresivă.</p>

<p>Rezultatul este un parfum care funcționează la fel de bine într-o seară de octombrie cu un palton greu, cât și într-o cămașă albă în decembrie, lângă un foc.</p>

<hr>

<h2>Când îl porți</h2>

<p>Este, prin natura lui, un parfum de toamnă-iarnă. Înflorește pe pielea caldă, în atmosfera rece. Vara devine prea dens.</p>

<p>Funcționează pe oricine apreciază profilurile profunde, indiferent de gen. Este unul dintre acele parfumuri care îți schimbă atitudinea în secunda în care îl pui — devii puțin mai liniștit, puțin mai atent la cum vorbești, puțin mai prezent.</p>

<hr>

<h2>De unde să începi</h2>

<p>Dacă explorezi pentru prima oară genul oriental gourmand, <a href="/products/labnoir-2"><strong>Blend No. 2</strong></a> este un punct de plecare onest. Îți arată ce poate face categoria fără să ceară o investiție inutil de mare.</p>

<p>Dacă deja iubești profilurile dulci-fumurii, îți recomandăm să încerci în paralel și <a href="/products/labnoir-3">Blend No. 3</a> — o reinterpretare a unui profil cu cognac și scorțișoară, dintr-o cu totul altă școală, dar din aceeași familie de seară.</p>

<p>Două flacoane de explorat. Două direcții ale aceleiași idei: parfumul ca atmosferă, nu ca declarație.</p>
""",
},

# ───── 2. BLEND No. 4 — citric solar mediteranean (Erba Pura) ─────
{
    "title": "Soare pe piele: profilul citric-solar care nu obosește niciodată",
    "handle": "soare-pe-piele-profilul-citric-solar-care-nu-oboseste-niciodata",
    "summary": "Lămâie de Sicilia, ambră dulce, fructe mediteraneene. Un "
               "profil unisex care funcționează din martie până în octombrie. "
               "Reinterpretat în Blend No. 4.",
    "tags": ["citric", "solar", "fructat", "ambra", "unisex", "vara"],
    "image_url": f"{CDN}/erba_pura_01.jpg",
    "image_alt": "Lămâi mediteraneene și textură de iarbă — Blend No. 4 Lab Noir",
    "body": """\
<p>Sunt parfumuri pe care nu le porți, le locuiești. Le pui dimineața, înainte de cafea, și rămân cu tine toată ziua fără să ceară atenție. Nu te transformă în altcineva. Te fac mai mult tine.</p>

<p>Profilul pe care îl reinterpretăm aici este unul dintre acelea. Apărut într-o casă italiană din Milano, devenit cult printre pasionații care caută citrice „adulte" — adică citrice care nu se evaporă în 30 de minute. Nu îi rostim numele. Cei care îl cunosc l-au recunoscut deja. Pentru ceilalți: ascultă cum funcționează.</p>

<hr>

<h2>De ce este special un citric solar</h2>

<p>Cele mai multe parfumuri citrice mor repede. Bergamotul, lămâia, mandarina — toate sunt molecule volatile, frumoase la deschidere și absente după o oră. Marii parfumieri italieni au rezolvat problema asta într-un mod elegant: au pus citricele într-un pat de ambră dulce și fructe mediteraneene, care le țin pe piele ore în șir.</p>

<p>Profilul rezultat are trei straturi clare. La început — lămâia siciliană, mandarina, un strop de bergamot. Pielea reacționează aproape vizibil: parfumul „strălucește" pe încheietură.</p>

<p>În inimă apar fructele moi — pere coapte, smochine, o piersică foarte discretă. Aici parfumul se rotunjește. De aici începe partea care îl face memorabil.</p>

<p>În bază, ambră dulce, mosc alb și o urmă de iarbă uscată în soare. Această bază este motivul pentru care profilul rezistă opt-zece ore pe pielea caldă fără să devină dulce-greu.</p>

<hr>

<h2>Pentru cine este</h2>

<p>Profilul este profund unisex. Funcționează la fel de bine pe o cămașă de in albă într-o cafenea, ca pe o rochie de vară lângă mare. Este genul de parfum pe care îl porți la birou luni dimineață și apoi sâmbătă seara la o terasă, fără să simți că trebuie să schimbi.</p>

<p>Dacă explorezi parfumeria de nișă pentru prima dată și nu știi unde să începi, citricele solare sunt cea mai onestă rampă de lansare. Sunt purtabile aproape de oricine. Sunt complimente garantate fără efort. Și sunt suficient de complexe încât să rămâi atent la ele și după luni de purtat zilnic.</p>

<hr>

<h2>Ce am ajustat în laborator</h2>

<p>Profilul original are un punct delicat: în primele 5–10 minute, deschiderea poate fi puțin prea acidă pe pielea uscată. În laboratorul nostru am ajustat partea de vârf cu o picătură de neroli, care moderează aciditatea fără să taie din strălucire.</p>

<p>Am ținut intenționat partea de fructe moi exact așa cum este. Aici stă magia: pera coaptă pe care nu o numești, dar pe care creierul o recunoaște imediat ce o miroși. Este motivul pentru care oamenii spun „miroși a vacanță" când porți acest profil.</p>

<hr>

<h2>De unde să începi</h2>

<p>Dacă vrei un singur parfum care să acopere toată primăvara și toată vara, <a href="/products/labnoir-4"><strong>Blend No. 4</strong></a> este o alegere pe care nu o regreți. Loturi mici, producție manuală, sticlă de 50 ml — perfectă pentru a o avea în geantă.</p>

<p>Dacă cauți un contrast, în aceeași gamă unisex de zi încearcă și <a href="/products/labnoir-30">Blend No. 30</a>: un profil mai mineral, cu lavandă și ambroxan, complet diferit ca atmosferă, dar la fel de purtabil.</p>

<p>Două direcții pentru sezonul cald. Două moduri de a mirosi a tine însuți.</p>
""",
},

# ───── 3. BLEND No. 3 — cognac + scorțișoară + lemn dulce (Angel's Share) ─────
{
    "title": "Cota îngerilor: parfumul care miroase a butoi de stejar și seară lungă",
    "handle": "cota-ingerilor-parfumul-care-miroase-a-butoi-de-stejar",
    "summary": "Cognac, scorțișoară, fasole tonka, lemn dulce. Un profil "
               "narativ inspirat de o tradiție din pivnițele franceze. "
               "Reinterpretat în Blend No. 3.",
    "tags": ["gourmand", "cognac", "lemnos", "scortisoara", "unisex", "seara"],
    "image_url": f"{CDN}/003_angels_share.jpg",
    "image_alt": "Pahar de cognac și fasole tonka pe lemn — Blend No. 3 Lab Noir",
    "body": """\
<p>În pivnițele de cognac din vestul Franței, există un fenomen pe care producătorii îl numesc <em>la part des anges</em> — cota îngerilor. Este cantitatea de alcool care se evaporă din butoaiele de stejar în fiecare an, urcând tăcut prin lemn în atmosfera răcoroasă a pivniței.</p>

<p>Doi sau trei procente pe an. În câteva zeci de ani, jumătate dintr-un butoi devine aer. Producătorii au învățat să trăiască cu această pierdere ca și cum ar fi un ritual — cota îngerilor este prețul pe care îl plătești ca să obții ceva profund.</p>

<p>Un parfumier francez celebru pentru un brand cu coroană pe sticlă a transformat această imagine într-un parfum. Nu îl numim aici. Dar profilul a devenit între timp un punct de referință pentru gourmand-urile cu caracter, iar ideea narativă din spate este una dintre cele mai bune din parfumeria modernă.</p>

<hr>

<h2>Anatomia profilului</h2>

<p>Deschiderea este imediat recognoscibilă: cognac proaspăt, ușor înțepător, cu o notă de fum și un strop de scorțișoară. Nu este o imitație de băutură — este senzația de a ridica un pahar la nivelul nasului într-o sufragerie caldă.</p>

<p>În inimă apar fasolea tonka și o vanilie discretă, care rotunjesc partea alcoolică și o transformă într-o căldură caramelizată. Aici parfumul devine prietenos. Acolo unde alte gourmand-uri sunt agresiv-dulci, acesta rămâne adult — există o demnitate în compoziție.</p>

<p>În bază, lemn de stejar și un mosc cremos. Stejarul este detaliul tehnic care face profilul să nu cadă în zona de „prăjitură". Îl ține lângă o pivniță, nu lângă o vitrină de cofetărie.</p>

<hr>

<h2>De ce funcționează</h2>

<p>Există parfumuri care iți spun o poveste, și parfumuri care îți construiesc o cameră în jurul tău. Acest profil face al doilea lucru. În momentul în care îl pui, atmosfera din jurul tău se schimbă subtil — devine mai caldă, mai privată, mai liniștită.</p>

<p>Este motivul pentru care funcționează atât de bine seara. Nu este un parfum de ofensivă: nu vrea să fie observat de la trei metri. Este un parfum care construiește intimitate — îl simt cei care se apropie.</p>

<hr>

<h2>Ce am reinterpretat</h2>

<p>În laborator am ținut nucleul intact: cognac–scorțișoară–tonka. Am ajustat ușor partea de stejar, ca să dea mai mult contur lemnos compoziției pe parcursul orelor. Am redus puțin nota fumurie din vârf, care în versiunea originală poate fi prea pronunțată pentru pielile sensibile.</p>

<p>Rezultatul este un parfum mai purtabil pe parcursul unei seri întregi, fără să piardă caracterul — încă povestește aceeași poveste, doar la un volum puțin mai uman.</p>

<hr>

<h2>Când îl porți</h2>

<p>Toamnă, iarnă, început de primăvară. Seară târzie, masă lungă, conversații cu oameni care contează. Nu este un parfum pentru job-ul de luni dimineață. Este un parfum pentru momentele când vrei să fii prezent.</p>

<p>Funcționează pe oricine, indiferent de gen — este unul dintre acele profile pe care le poate purta o femeie cu un trench negru la fel de natural ca un bărbat cu un pulover de cașmir.</p>

<hr>

<h2>De unde să începi</h2>

<p><a href="/products/labnoir-3"><strong>Blend No. 3</strong></a> este un punct de intrare onest în categoria gourmand-urilor cu narativ. 50 ml, lot mic, producție manuală în laboratorul nostru.</p>

<p>Dacă te atrage profilul ăsta, încearcă și <a href="/products/labnoir-2">Blend No. 2</a>: tot oriental, tot de seară, dar pe direcție de tutun și vanilie densă în loc de cognac. Două moduri diferite de a construi căldura.</p>
""",
},

# ───── 4. BLEND No. 7 — vanilie cremoasă modernă (Vanilla Sex) ─────
{
    "title": "O vanilie care nu seamănă cu o vanilie: piele caldă, lemn alb, ambră lăptoasă",
    "handle": "o-vanilie-care-nu-seamana-cu-o-vanilie",
    "summary": "Nu e vanilia clasică. Este o vanilie texturată — lapte, lemn "
               "alb, ambră transparentă, piele caldă. Reinterpretată în "
               "Blend No. 7.",
    "tags": ["vanilie", "ambra", "gourmand", "modern", "feminin", "lapte"],
    "image_url": f"{CDN}/007_vanilla_sex_1.jpg",
    "image_alt": "Vanilie, ambră și lemn alb — Blend No. 7 Lab Noir",
    "body": """\
<p>Întreabă pe oricine ce miroase a vanilie și o să primești același răspuns: prăjitură, înghețată, cofetărie. Este reflexul standard. Pentru majoritatea oamenilor, vanilia este un miros copilăresc, dulce, recognoscibil și cam plictisitor.</p>

<p>Dar există o întreagă școală de parfumieri care lucrează vanilia în alt mod. Pentru ei, vanilia nu este un gust. Este o textură. Este pielea caldă după duș. Este laptele călduț într-un pahar înalt. Este interiorul unei dimineți târzii.</p>

<p>Profilul pe care îl reinterpretăm aici este, fără îndoială, exemplul cel mai bun din ultimii ani al acestei școli. A fost lansat recent în linia privată a unei mari case americane, sub un nume deliberat provocator. Nu îl rostim. Pasionații știu. Restul vor afla acum cum funcționează.</p>

<hr>

<h2>Vanilia ca textură, nu ca gust</h2>

<p>În deschidere, profilul nu este dulce. Este moale. Apare un mosc cremos, o urmă de lapte cald, o senzație de bumbac proaspăt. Creierul tău nu identifică imediat „vanilie" — identifică „cald". Asta este partea genială a compoziției.</p>

<p>Treptat, în inimă, intră vanilia adevărată — dar nu vanilia de cofetărie. Este o vanilie boabă, ușor afumată, cu o textură de fasole tonka. Lângă ea apare un lemn alb (cașmir, sandal foarte transparent), care dă verticalitate compoziției.</p>

<p>În bază, ambra moale și moscul lăptos creează ceea ce parfumierii numesc <em>skin scent</em> — un parfum care, după câteva ore, miroase a piele proprie, doar mai bună. Este motivul pentru care oamenii care îl poartă primesc constant întrebarea „ce parfum ai?".</p>

<hr>

<h2>De ce funcționează atât de bine</h2>

<p>Există un secret în compoziție: profilul nu este foarte „proeminent". Nu cucerește o cameră. Cucerește un metru și jumătate. Sillage-ul este intenționat intim — ești tu, un mic nor de senzualitate, și oricine se apropie suficient ca să te audă vorbind.</p>

<p>Este motivul pentru care profilul a devenit atât de iubit: nu este vanilia adolescentă pe care o miroși la mall. Este vanilia adultă pe care o miroși pe cineva important pentru tine.</p>

<hr>

<h2>Ce am ajustat în laborator</h2>

<p>Am ținut nucleul aproape identic: vanilie–mosc lăptos–lemn alb. Am amplificat ușor partea de tonka, care în formula noastră dă mai multă căldură pielii pe timp rece. Am echilibrat moscul ca să nu domine compoziția pe pielile foarte calde.</p>

<p>Rezultatul este un parfum care funcționează în orice sezon — vara devine o senzație de pat răcoros după duș, iarna devine un strat invizibil de bumbac sub palton.</p>

<hr>

<h2>Pentru cine este</h2>

<p>Predominant feminin, dar nu exclusiv. Funcționează superb pe orice piele care apreciază textura în locul declarației. Nu este un parfum care strigă. Este un parfum care șoptește — și asta îl face mai puternic.</p>

<p>Funcționează în orice context: dimineață devreme, întâlniri lungi, seri liniștite, weekenduri petrecute în pijamale și cărți. Este unul dintre acele parfumuri pe care le porți pentru tine, nu pentru ceilalți.</p>

<hr>

<h2>De unde să începi</h2>

<p><a href="/products/labnoir-7"><strong>Blend No. 7</strong></a> este o reinterpretare onestă a unei vanilii moderne, în lot mic, 50 ml. Dacă nu ai purtat niciodată un <em>skin scent</em>, este parfumul cu care recomandăm să începi.</p>

<p>Dacă, după ce îl probezi, vrei să explorezi ramura mai dulce-fructată a aceleiași familii, încearcă și <a href="/products/labnoir-91">Blend No. 91</a> — o vanilie cu cireșe negre și migdale amare. Tot textură, dar pe direcție de cofetărie elegantă în loc de bumbac.</p>
""",
},

# ───── 5. BLEND No. 71 — miere + portocală amară + gardenia (Scandal) ─────
{
    "title": "Miere și portocală amară: parfumul-bijuterie pentru serile lungi",
    "handle": "miere-si-portocala-amara-parfumul-bijuterie",
    "summary": "Miere caldă, portocală amară, gardenia, caramel. Un profil "
               "floral-mierat semnat de o casă pariziană controversată. "
               "Reinterpretat în Blend No. 71.",
    "tags": ["floral", "miere", "fructat", "feminin", "gourmand", "seara"],
    "image_url": f"{CDN}/scandal71.jpg",
    "image_alt": "Miere caldă, gardenia și portocală amară — Blend No. 71 Lab Noir",
    "body": """\
<p>Sunt parfumuri pe care le pui când vrei să fii observată. Și sunt parfumuri pe care le pui când <em>știi</em> că o să fii observată — și nu îți pasă.</p>

<p>Profilul pe care îl reinterpretăm aici aparține clar celei de-a doua categorii. A fost lansat de o casă pariziană de modă cunoscută pentru flacoane sculpturale și pentru o estetică deliberat provocatoare. Nu îi rostim numele. Pasionații îl recunosc imediat după descriere. Pentru toți ceilalți: contează cum este construit.</p>

<hr>

<h2>De ce un parfum cu miere?</h2>

<p>Mierea este una dintre cele mai dificile note în parfumerie. Foarte ușor poate aluneca în zona de „prea dulce", „prea greu", „prea adolescentă". Dar un parfumier bun poate face din miere unul dintre cele mai senzuale ingrediente posibile.</p>

<p>În compoziția pe care o reinterpretăm, mierea apare în inimă și este temperată din două direcții. Pe de o parte de portocala amară, care taie din dulceață cu o aciditate aproape parfumată. Pe de altă parte de gardenia — o floare albă cu textură cremoasă, dar cu o notă verde subtilă care păstrează profilul „adult".</p>

<p>În deschidere, parfumul te primește cu mandarină, fructe roșii și un strop de bujor proaspăt. Nu este o deschidere „de fată cuminte". Are deja o intenție.</p>

<hr>

<h2>Cum se poartă pe piele</h2>

<p>În prima oră, parfumul este vibrant — fructat, floral, ușor capricios. Este momentul în care intri într-o cameră și oamenii ridică privirea fără să își dea seama de ce.</p>

<p>După două-trei ore, lucrurile se așază. Mierea se rotunjește, gardenia devine cremoasă, portocala amară lasă în urmă o tentă caramelizată. Parfumul intră în faza pe care o iubim cel mai mult: mai puțin „spectacol", mai mult „prezență".</p>

<p>Spre seară, în bază apar pacholi-ul cremos, vanilia și o ambră caldă. Sillage-ul rămâne consistent fără să devină greu. Este un parfum construit pentru a fi purtat de la apus până la cinci dimineața.</p>

<hr>

<h2>Ce am reinterpretat</h2>

<p>În versiunea originală, mierea poate fi pentru unele piei prea pronunțată în primele 30 de minute. În laboratorul nostru am ajustat partea de vârf cu o picătură suplimentară de mandarină verde, care dă un strop de aer compoziției. Am ținut intenționat gardenia și portocala amară exact așa cum sunt — sunt cele două coloane vertebrale ale profilului.</p>

<p>Rezultatul este un parfum mai aerat în prima oră, dar identic în construcția lui de profunzime.</p>

<hr>

<h2>Pentru cine este</h2>

<p>Este un parfum predominant feminin, deși îl știm pe câțiva bărbați care îl poartă cu mult caracter. Funcționează cel mai bine pe pielea caldă, în lumină scăzută, în atmosferă cu intenție.</p>

<p>Nu este un parfum de birou. Este un parfum de început de seară — momentul în care îți pui o pereche de cercei și te uiți o ultimă dată în oglindă înainte să ieși.</p>

<hr>

<h2>De unde să începi</h2>

<p><a href="/products/labnoir-71"><strong>Blend No. 71</strong></a> este o reinterpretare în lot mic, 50 ml, a acestui profil floral-mierat — pentru cei care colecționează parfumuri cu personalitate, nu cu ambiguitate.</p>

<p>Dacă te atrage direcția dulce-feminină, dar vrei un contrast mai cremos și mai modern, încearcă în paralel și <a href="/products/labnoir-73">Blend No. 73</a> — o reinterpretare cu tubercul, cacao și iasomie, dintr-o cu totul altă casă, dar din aceeași familie de seară.</p>

<p>Două flacoane. Două moduri de a intra într-o cameră.</p>
""",
},
]


# ──────────────────────────────────────────────────────────────────────────────
# MUTATION
# ──────────────────────────────────────────────────────────────────────────────

ARTICLE_CREATE = """
mutation articleCreate($article: ArticleCreateInput!) {
  articleCreate(article: $article) {
    article {
      id
      handle
      title
      isPublished
      publishedAt
      tags
      blog { handle }
      image { url }
    }
    userErrors { field message code }
  }
}
"""

def main():
    token = mint()
    print(f"token minted ({token[:14]}...)\n")
    for i, a in enumerate(ARTICLES, start=1):
        body_chars = len(a["body"])
        print(f"[{i}/5] {a['title']}  ({body_chars} chars HTML)")
        variables = {
            "article": {
                "blogId":   BLOG_ID,
                "title":    a["title"],
                "handle":   a["handle"],
                "body":     a["body"],
                "summary":  a["summary"],
                "tags":     a["tags"],
                "author":   {"name": AUTHOR},
                "isPublished": True,
                "image":    {"url": a["image_url"], "altText": a["image_alt"]},
            }
        }
        data = gql(token, ARTICLE_CREATE, variables)
        result = data["articleCreate"]
        if result["userErrors"]:
            print("  USER ERRORS:")
            for e in result["userErrors"]:
                print(f"    {e['field']} [{e.get('code')}]: {e['message']}")
            sys.exit(1)
        art = result["article"]
        print(f"  ✓ {art['id']}  /{art['blog']['handle']}/{art['handle']}")
        print(f"    published={art['isPublished']}  at={art['publishedAt']}")
        print(f"    image={(art.get('image') or {}).get('url','-')}")
    print("\nALL 5 ARTICLES PUBLISHED")


if __name__ == "__main__":
    main()
