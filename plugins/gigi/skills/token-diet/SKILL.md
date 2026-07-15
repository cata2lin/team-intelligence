---
name: token-diet
description: Cum să lucrezi eficient pe tokeni — mai puțin cost, context curat, fără să pierzi calitate. Practicile cu cel mai mare levier, ordonate: deleagă citirile grele pe subagenți (Explore) care întorc concluzii, nu payload-uri; rulează modelul IEFTIN (Haiku) pe subtaskurile mecanice și Opus doar la sinteză; filtrează output-ul tool-urilor (top-N + count, nu dump de 30-50k); memorie externă (plan/rezultate pe disc, nu reciti, nu repeta conținut); prompt-caching cu prefix stabil DAR cu pragul de rentabilitate; context mereu-încărcat mic. Use pentru „consumă prea mulți tokeni", „cum reduc costul/contextul", „lucrează mai eficient", „economisește tokeni", „context management", „prea scump", „se umple contextul", „token diet", sau înainte de un task lung/fan-out cu multe citiri.
argument-hint: "aplică practicile de eficiență pe tokeni la task-ul curent (deleagă/rutează/filtrează/extern)"
---

# token-diet
> Author: **Gigi**. Mai puțini tokeni, aceeași calitate. Cel mai scump token e cel cheltuit pe muncă greșită.

## Practicile, pe levier (aplică-le pe rând)
1. **Deleagă citirile grele pe subagenți.** Un grep/log/dump de fișiere poate fi 30-50k tokeni/tură.
   Dă-l pe **Explore** (read-only) sau un subagent — el absoarbe payload-ul în contextul LUI și-ți întoarce
   **concluzia**, nu conținutul. ⚠️ NU pentru task-uri mici (startup-ul subagentului costă mai mult decât
   economia). Regula: deleagă doar când payload-ul evitat > overhead.
2. **Rutează modelul ieftin pe munca mecanică — dar cu GRIJĂ unde.** ⚠️ Haiku e mai slab; pus pe task
   greșit dă răspunsuri proaste greu de prins. Granița:
   - **Haiku DA** (mecanic, deterministic, verificabil): rezumă un grep/log, extrage un câmp/valoare,
     listează fișiere, convertește format, filtrează/numără, caută un string. Adică lucruri unde răspunsul
     e ușor de verificat și o greșeală se vede imediat.
   - **Haiku NICIODATĂ** (judecată / cost): verdicte pe **bani/profit** (scale/cut, CPA, breakeven),
     răspunsuri **către clienți** (CS), decizii de **securitate/mutații**, sinteză, planificare, orice
     unde un răspuns greșit e scump sau greu de prins. Astea rămân pe **modelul sesiunii (Opus)**.
   - **Când nu ești sigur → NU ruta pe Haiku** (moștenește modelul sesiunii). Economia nu merită un
     verdict greșit. Pe Agent tool: `model: "haiku"` DOAR la subagenții mecanici de mai sus.
   Un fan-out unde toate citirile mecanice sunt pe Opus = risipă; unde sinteza e pe Haiku = risc.
3. **Filtrează output-ul ÎNAINTE să intre în context.** SQL/AWB/grep/log → `head`/`wc -l`/top-N + count,
   nu dump brut. Un grep pe tot repo-ul ≈ 30k tokeni; un test verbose ≈ 50k. Întoarce „primele + numărul",
   nu tot. Citește **interval de linii** (`file:line`), nu fișiere întregi; grep înainte de read.
4. **Memorie externă, nu în context.** Plan + rezultate intermediare pe disc ([[plan-first]]); NU reciti
   un fișier deja citit; NU repeta conținutul unui fișier în chat. Retrieval on-demand, nu „îndeși tot".
5. **Prompt-caching cu prefix stabil** — pune stabilul întâi (system/tool-defs/referințe), dinamicul la
   urmă. **DAR** capcana reală: **sub ~1,4 citiri per scriere, caching-ul costă MAI MULT** (penalty 25% pe
   write) — nu-l forța pe apeluri rare. TTL 5 min; un singur caracter schimbat înainte de breakpoint
   invalidează tot prefixul.
6. **Context mereu-încărcat mic.** Ce se încarcă la FIECARE tură (CLAUDE.md, cataloage) se plătește de
   toți, mereu. Ține-l dens; mută detaliul în retrieval (`find_skills`). Lecție: catalogul echipei
   compactat −44% ([[efficiency-skills-adoption]]).
7. **Ingestie ieftină de documente/cod.** PDF/DOCX/XLSX → markdown cu `gigi:markitdown` (−40…90%) ÎNAINTE
   de a le da modelului; repo întreg → `gigi:repo-pack` (Repomix `--compress`, ~70%).
8. **Output concis.** Răspuns la obiect, structuri/JSON unde e cazul, fără să reciti ce tocmai ai citit.

## Note
- Cel mai mare câștig NU e output terse, ci **evitarea rework-ului**: [[brainstorming]] (design înainte),
  [[plan-first]] (pași verificabili), [[debug-systematic]] (cauză, nu patch-uri ghicite).
- Ce am respins pe încredere: Magic Compact / ContextSniper (numere self-report; ContextSniper e research pe
  program-repair, nu ops). Vezi [[efficiency-skills-adoption]] pentru sursele fuzionate + cifre.
- Fan-out de deep-research: rutează sub-apelurile pe Haiku, prefix stabil, filtrează payload-urile. Vezi [[deep-research-focused-vs-workflow]].
