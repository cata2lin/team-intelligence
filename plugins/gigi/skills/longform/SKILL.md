---
name: longform
description: "Write COMPELLING long-form NARRATIVE articles — editorial features, reported stories, brand narratives, deep explainers-as-story — in the style of WIRED / The Atlantic / bikeportland, NOT SEO listicles or short marketing copy. Use when the user wants a real article that HOLDS a reader: a story with a hook, a narrative arc, scenes, characters and quotes, tension and stakes, facts woven into the telling, and a kicker that lands. Covers the craft (hooks, structure, scene-setting, dialogue, pacing, voice, transitions, endings), a repeatable process (angle → research/grounding → find-the-story → outline the arc → draft → adversarial revise → de-AI → line-edit), the anti-patterns that make writing read generic/AI, and a revision checklist. Composes with `library:brand` (voice), `gigi:ai-scrub` (de-AI gate, RO), `gigi:copy-editing` (polish). Works in RO or EN. Triggers: 'scrie un articol', 'write a long-form / feature article', 'narrative article', 'tell this as a story', 'make this an engaging article', 'articol de blog captivant', 'reportaj', 'brand story', 'longform', 'write it like WIRED'."
argument-hint: "topic/brief (+ optional: --brand <name>, target length, publication style)"
---

# longform — articole narative care se citesc până la capăt

Scrie **feature-uri narative** (poveste + reportaj + idee), nu listicle SEO și nu copy scurt. Referință de stil:
WIRED / The Atlantic / New Yorker / bikeportland — articolul are un **cârlig**, un **arc**, **scene**, **oameni și
citate**, **tensiune și mize**, faptele **țesute în poveste**, și un **final care aterizează**.

> **Nu confunda cu:** `core:articles` (blog SEO/brand pt magazinele de parfum, grounded pe produse reale) ·
> `gigi:copywriting` (copy scurt de pagină) · `gigi:seo-content` (optimizat pt căutare). Ăsta e **narativ, editorial**.
> **Deep-dive de craft** (tipuri de hook, structuri, scenă/citat/tensiune, voce, kicker, cu exemple): `references/craft.md` — **citește-l**.

## Procesul (repetabil — nu sări peste „găsește povestea")
1. **Unghiul + miza.** O propoziție: *cine/ce + tensiunea + de ce contează ACUM*. Fără unghi = doar informație.
2. **Research / grounding.** Fapte, cronologie, oameni, citate reale, detalii senzoriale. **Nu inventa** fapte,
   citate sau persoane. Dacă e branded, citește vocea (`library:brand` / `shared/apps/<brand>.md`). Marchează ce e
   necunoscut ca lacună de umplut, nu de fabricat.
3. **Găsește POVESTEA** (pasul pe care toți îl sar). Care e **through-line-ul** — o schimbare, un conflict, o
   întrebare care se ține până la final? „Un produs există" nu e poveste; „cineva a pariat tot pe ceva incert și…" e.
4. **Outline pe ARC**, nu pe subteme. Alege o structură din `references/craft.md` (cronologic-narativ, problemă→
   căutare→rezolvare, braided/paralel, profil). Marchează unde vine tensiunea și unde se eliberează.
5. **Draft rapid**, în voce. Scrie scenele, nu rezumatele. Lasă imperfect — revizuirea e unde se face calitatea.
6. **Revizuire adversarială** cu checklistul de mai jos + `references/craft.md`. Taie throat-clearing-ul, umple
   scenele, verifică că fiecare fapt e ancorat de un om/o scenă (nu info-dump).
7. **De-AI.** Treci prin **`gigi:ai-scrub`** (scoate watermark-uri Unicode + fraze-AI, blocklist RO). Obligatoriu pre-publicare.
8. **Line-edit final** (`gigi:copy-editing` pt polish): ritm de propoziție, verbe active, tăiat adverbe/clișee.

## Cele 8 pârghii de craft (detaliu + exemple în `references/craft.md`)
1. **Hook (primele 1-3 paragrafe).** Intră **in-media-res** — o scenă, un moment, o mizoare — NU o definiție sau
   „În lumea de azi…". Cârligul promite o tensiune, nu un subiect.
2. **Arc.** hook → contextul/miza → dezvoltare care urcă → turnură/climax → rezolvare → **kicker**. Fiecare secțiune
   împinge înainte; nu e o enciclopedie pe capitole.
3. **Scene.** Arată, nu spune: loc, moment, acțiune, detaliu senzorial concret. O scenă bună > trei paragrafe de explicație.
4. **Oameni & citate.** Poveștile au protagoniști. Citate reale, dialog, atribuire clară. Oamenii poartă ideile.
5. **Tensiune & mize.** De ce contează? Ce e în joc? Pune întrebarea devreme, ține-o deschisă, plătește-o la final.
6. **Fapte țesute.** Informația tehnică se livrează **la nevoie**, ancorată de o persoană sau scenă — niciodată info-dump.
7. **Voce & ritm.** Propoziții variate (scurte pt impact, lungi pt curgere). Verbe active, substantive concrete.
   Taie adverbele, hedging-ul, superlativele goale. Un narator cu punct de vedere, nu voce de Wikipedia.
8. **Kicker.** Finalul se **închide în cerc**, deschide o fereastră spre viitor, sau lasă o imagine care rezonează.
   Niciodată „În concluzie…" sau un rezumat al a ceea ce tocmai ai citit.

## ✅ Checklist de revizuire (treci fiecare)
- [ ] Aș citi mai departe după primul paragraf? (dacă nu → rescrie hook-ul, scoate throat-clearing-ul)
- [ ] Există o **poveste** (schimbare/conflict/întrebare), nu doar informație organizată?
- [ ] Cel puțin o **scenă** reală (loc+moment+acțiune), nu doar explicație?
- [ ] Oameni cu nume + **citate reale**? Ideile sunt purtate de cineva?
- [ ] Miza e clară în prima treime și plătită la final?
- [ ] Vreun **info-dump**? (dacă da → ancorează-l de o scenă/persoană sau taie-l)
- [ ] Ritm variat de propoziție; verbe active; zero adverbe/clișee inutile?
- [ ] Kicker care aterizează (nu „în concluzie")?
- [ ] Trecut prin **ai-scrub** (fără watermark-uri / fraze-AI)?
- [ ] Fapte/citate/persoane REALE (zero fabricat)?

## Anti-tipare (semnătura textului generic/AI — taie-le)
Deschidere-definiție („X este un…") · „În lumea de azi / era digitală" · rezumat-concluzie · listicle deghizat în
articol · adverbe-supă („cu adevărat, incredibil, extrem de") · superlative goale · em-dash-uri în exces ·
„merită menționat / este important de reținut" · propoziții toate de aceeași lungime · zero oameni, zero scene.

Related: `references/craft.md` · `gigi:ai-scrub` · `gigi:copy-editing` · `library:brand` · `core:articles` (blog SEO brand).
