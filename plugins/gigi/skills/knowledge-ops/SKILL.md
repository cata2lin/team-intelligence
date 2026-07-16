---
name: knowledge-ops
description: Cum înțelegi, faci diagnostic și EXTINZI sistemul de cunoaștere al echipei — reproductibil de oricine. Cele două straturi (Second Brain = server/echipă cu căutare semantică + skill-uri-ca-tool-uri + graf de entități; graful de memorie = fișiere .claude locale + [[wikilinks]] hub-and-spoke, oglindit în Obsidian), diagnosticul de sănătate (note/legături/orfani/clustere/monstru), metoda de cercetare pe 3 surse fuzionate (Claude fan-out + prompturi ChatGPT/Gemini + verificare de hype), și extinderea SIGURĂ (auto-linking cu poartă de precizie, igienă de clustere, puntea personal→echipă). Use pentru „cum funcționează SB / memoria", „extinde graful de cunoaștere", „fă-mi memoria mai utilă", „leagă notele", „auto-link semantic", „diagnostic graf memorie", „mută cunoașterea personală la echipă", „knowledge ops", „cercetare pe 3 surse".
argument-hint: "audit (graph_health) → cercetare 3-surse → extinde (auto-link/clustere/personal→echipă)"
---

# knowledge-ops
> Author: **Gigi**. Metoda completă de a face sistemul de cunoaștere mai UTIL — util întâi, frumos ca efect.

## Cele două straturi (nu-s același lucru)
- **Second Brain (server, echipă):** ~380 documente (căutare hibridă semantică) + ~290 skill-uri expuse ca
  tool-uri apelabile (fiecare operație cu `preview`/`apply`) + graful LUI de entități. MCP: `overview`,
  `find_skills`, `search_knowledge`, `publish_skill`, `remember`. Toți îl folosesc.
- **Graful de memorie (local, al tău):** note `.md` în `~/.claude/projects/<slug>/memory/` (1 fapt/fișier)
  legate `[[wikilink]]`, oglindite în Obsidian. Skill **[[memory-graph]]** le leagă (hub-and-spoke) +
  colorează + oglindește. **NU e în SB** (tier personal = 0) — de aici puntea de mai jos.

## Pasul 1 — Diagnostic (unealtă, nu tablou)
```bash
uv run scripts/graph_health.py                 # note/legături/ORFANI/clustere/MONSTRU/ancore + verdict
```
Plus, pentru stratul de echipă: MCP `second-brain overview` (câte docs/skills/entități). Rulează periodic:
**orfani în creștere** (cunoaștere ruptă) sau **un cluster > ~20%** (recall slab) = de reparat.

## Pasul 2 — Cercetează îmbunătățiri pe 3 surse (metoda fuziunii)
Cum am cercetat orice îmbunătățire majoră (eficiență, sistem de cunoaștere):
1. **Claude fan-out** — 2-3 subagenți paraleli (Haiku pe extracție), fiecare pe o felie; întorc CONCLUZII,
   nu pagini (vezi [[token-diet]]). Asta e „ramura Claude" — deja rulată de tine.
2. **ChatGPT + Gemini** — dă userului un prompt de Deep Research identic pentru amândouă, ancorat în ce
   AVEM deja (ca să găsească GOLURI, nu duplicate). Vezi și [[deep-research]].
3. **Fuzionează cele 3** — convergența (toate 3 → încredere mare) vs adăugiri unice.
4. **⚠️ Verifică hype-ul** — Gemini inventează repo-uri/arXiv plauzibile (ex. „ContextSniper 2607", „Atlas/
   CausalRAG2"). Separă MĂSURAT de REVENDICAT; nu adopta procente self-report pe încredere; testează pe caz real.

## Pasul 3 — Extinde SIGUR
### a) Auto-linking cu poartă de precizie ([[memory-graph]] `suggest_links.py`)
Găsește note↔note reale cross-cluster pe care hub-and-spoke le rată. **Regula de aur:** NU lega pe cosine
singur (semantic pur ≈ random: 24,9% vs 23,9% F1; cu gate → 31,6%). Embeddings = candidați; **poarta** (LLM/
tu) aprobă doar relații SPECIFICE (același sistem/produs/bug), nu „aceeași arie largă" (aia o face hub-ul).
⚠️ Prag: 0.80 e prea strict pt RO → coboară la **0.72** + gate mai dur (altfel ratezi legături reale, ex.
`gads-*`↔`google-ads-mcc`).

### b) Igienă de clustere
Cluster monstru (>20%) → **întâi** cross-links reale (a), care-l deconcentrează organic. Re-hub în
`clusters.json` DOAR dacă tema e reală și **de echipă** — NU împinge taxonomia unui venture privat în
config-ul partajat (rămâne local).

### c) Puntea personal → echipă (cel mai mare force-multiplier)
Memoria ta (tier personal SB = 0) nu ajunge la echipă. Promovează selectiv lecțiile reutilizabile în SB:
- **CE promovezi:** reguli/capcane tehnice, procese (ex. reguli de profit, capcane de verdict). **NU**
  preferințe personale / note de feedback.
- **Dedup:** `search_knowledge` întâi — dacă echipa are deja faptul, îmbină + citează, nu dubla.
- **Proveniență + staleness:** autor + dată; când un fapt se schimbă, marchează vechiul „stale", nu-l șterge.
- Publică cu `second-brain publish` / `remember`.

## Reguli de aur
1. **Doar relații reale** — un link cosmetic strică recall-ul (recall-ul aduce vecinii în context).
2. **Util întâi** — graful e unealtă de igienă; frumusețea vine ca efect.
3. **Nu polua config-ul de echipă** cu taxonomie personală; nu edita fișiere partajate auto-generate
   (ex. catalogul din CLAUDE.team.md se regenerează din DB — vezi [[efficiency-skills-adoption]]).
4. **Honesty-check** pe cercetare: măsurat ≠ revendicat.

## Tool-uri conexe
[[memory-graph]] (link/mirror/suggest_links) · [[deep-research]] · [[token-diet]] · [[brainstorming]] · [[plan-first]].
