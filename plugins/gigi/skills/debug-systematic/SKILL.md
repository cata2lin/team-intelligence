---
name: debug-systematic
description: Depanare pe CAUZA-RĂDĂCINĂ, nu pe simptom — 4 faze (înțelege → compară cu ce merge → o ipoteză minimală → fix țintit + verificare) în loc de patch-uri ghicite la întâmplare. Dacă 3 încercări pică, pui la îndoială arhitectura/ipoteza, nu mai dai încă un patch. Taie ciclul „schimb ceva, rulez, tot nu merge" (cel mai mare consumator de tokeni la bug-uri). Merge la orice: cod, pipeline de date (profit/marketing), cont de ads care raportează aiurea, un query care dă 0, un script care „rulează la infinit". Use pentru „de ce nu merge X", „debug", „dă eroare", „e bug-uit", „dă 0/gol/greșit", „am încercat și tot pică", „nu înțeleg de ce".
argument-hint: "simptom → cauză-rădăcină (4 faze) → fix minimal + verificare, nu patch ghicit"
---

# debug-systematic
> Author: **Gigi**. Găsește CAUZA, nu ascunde simptomul. **Nu ghici patch-uri la rând.**

## De ce
Ad-hoc (schimbă-ceva-și-rulează) = ore + ~40% succes din prima. Sistematic = minute + mult mai sigur,
și mai ales **nu arde tokeni/tură pe încercări oarbe**. Regula: dacă te trezești dând al 3-lea patch fără
să înțelegi de ce, **oprește-te** — greșești faza.

## Cele 4 faze
1. **Înțelege exact simptomul.** Ce input → ce output greșit, vs ce aștepți. Reproduce-l MINIMAL
   (un caz mic, deterministic). Citește eroarea/ieșirea COMPLETĂ, nu prima linie. Nu presupune.
2. **Compară cu ceva ce MERGE.** Un caz vecin care funcționează (alt brand, altă lună, alt SKU, alt
   endpoint). Diferența dintre „merge" și „nu merge" e pista spre cauză. (Ex. dovedit: „FB=13 rânduri
   în coloana întreagă vs 0 în range-ul mărginit" → cauza „0 la tot" — vezi [[raport-zilnic2-optimizare]].)
3. **O ipoteză minimală, testabilă.** Formuleaz-o („cauza e X pentru că Y") și testeaz-o cu CEA MAI MICĂ
   probă posibilă (un SELECT, un print, un caz izolat) ÎNAINTE să schimbi ceva. Confirmă cauza, nu sări la fix.
4. **Fix țintit + verificare.** Repari CAUZA (nu pui un guard peste simptom), apoi rulezi verificarea
   reală și **citești ieșirea** — dovadă că s-a reparat ȘI că n-ai stricat cazul „care mergea" (faza 2).

## Dacă 3 fixuri pică
Nu mai da al 4-lea. **Ipoteza/arhitectura e greșită.** Lărgește: e datele? (sursa greșită — AWBprint vs
metrics vs Shopify, [[profit-data-sources-truth]]). E fereastra/monedă/TVA? ([[gads-verdict-currency-and-trend-bugs]]).
E un cache stale? Un range mărginit? Reia de la faza 1 cu ipoteza nouă.

## Note
- **Verdict extrem = unealta minte, nu realitatea** — când un număr pare absurd (CPA 3× peste, „0 la tot",
  „24k în Looker dar 7k în store"), suspectează întâi unitatea/fereastra/sursa/un calculated-field, nu
  concluzia. (Lecții: [[gads-verdict-currency-and-trend-bugs]], [[raport-zilnic2-optimizare]].)
- La bug-uri pe date, faza 2 (compară cu ce merge) e cea mai puternică — aproape mereu găsești cauza acolo.
- Regresie găsită → adaugă și verificarea care ar fi prins-o (test/lint). Ex. lint-ul din [[apps-script-push]].
- Extras din obra/superpowers (systematic-debugging + root-cause-tracing), adaptat la echipa Arona.
