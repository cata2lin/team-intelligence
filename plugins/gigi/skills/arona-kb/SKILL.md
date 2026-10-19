---
name: arona-kb
description: "MCP-ul arona-kb — citiri din baza de cunoștințe a echipei (SharedClaude, KB_DATABASE_URL), READ ONLY: cine a folosit ce skill / MCP și cât, comparația ÎNAINTE vs DUPĂ un moment (ex. skill_router, cutover 19-oct 04:14 UTC), ce skill-uri LIPSESC (cererile skill_gap grupate), activitatea recentă pe om, resursele și lecțiile. Use when: 'cine a folosit ce skill', 'câte skill-uri pe sesiune', 'a crescut folosirea skill-urilor după skill_router', 'comparația de pe 26-oct', 'ce skill-uri lipsesc', 'goluri skill_gap', 'ce a făcut echipa azi', 'ce a lucrat anne ieri', 'lecții despre Trendyol', 'resurse KB'. Triggers: kb, knowledge base, skill usage, skill_gap, goluri, activitate echipă, resurse, lecții, comparație cutover."
category: dev-tools
version: 1.0.0
---

# arona-kb — MCP peste baza de cunoștințe (SharedClaude)

Server: `mcp_server.py` (FastMCP, stdio, `psycopg2`). Register:
`claude mcp add --scope user arona-kb -- uv run <abs path>/plugins/gigi/skills/arona-kb/mcp_server.py`

Conexiunea: `KB_DATABASE_URL` din mediul sesiunii; dacă lipsește, serverul îl citește din profilul `zsh -lic`
(fără să-l printeze). Fiecare query = tranzacție **READ ONLY** + `statement_timeout 60s`.
**Tabelul `secrets` nu e citit de niciun tool** (nici valori, nici nume). Scrierile în KB rămân la `kb.py`
(`core:knowledge-base`) și la hook-uri.

## Tool-uri
| Tool | Ce întoarce |
|---|---|
| `kb_utilizare_skilluri(de_la, pana_la="", pe_om=false)` | sesiuni, evenimente skill/sesiune, % sesiuni cu skill SAU MCP `arona-*`, top skill-uri, top MCP; `pe_om` = defalcare pe handle |
| `kb_comparatie(cutover, zile=7)` | ÎNAINTE `[cutover−zile, cutover)` vs DUPĂ `[cutover, cutover+zile)` (tăiat la acum) + Δ |
| `kb_goluri(zile=7, min_cuvinte=6)` | cererile `skill_gap` fără skill potrivit, fără cele scurte, duplicatele grupate (număr, oameni, ultima dată) |
| `kb_activitate(zile=1, om="")` | pe om × tip de eveniment + ultimele evenimente notabile (fără zgomotul mcp/db) |
| `kb_resurse(cauta="", categorie="", limita=40)` | tabelul `resources` (url, doc, lesson, app…) |

Datele: `'2026-10-12'` = ziua întreagă, ora României (`pana_la` inclusă); `'2026-10-19T04:14Z'` = moment exact;
fără fus orar = ora României.

## Definițiile (ca să poți reproduce cifrele)
- **sesiune** = `session_uid` distinct, nenul, cu orice eveniment în fereastră (inclusiv doar `session`).
- **evenimente skill** = `entity_type='skill'` (orice action: `used`, `read`…).
- **arona MCP** = `entity_type='mcp' AND entity_name LIKE 'arona-%'` (serverele user-scope; cele din plugin apar `plugin_core_…`).
- **% sesiuni cu skill sau arona** = sesiuni cu ≥1 din cele două / toate sesiunile.

## Comparația din 26-oct (skill_router)
`kb_comparatie(cutover="2026-10-19T04:14:04Z", zile=7)` — cutover = merge PR #618.
Baseline ÎNAINTE (12→19 oct, măsurat 19-oct): **153 sesiuni · 103 skill = 0,67/sesiune · 63 sesiuni = 41%**.
(Cifra 152 / 0,68 venea dintr-o fereastră tăiată la 06:20 UTC; cu 06:20Z tool-ul o reproduce exact.)
⚠ Din PR #625 hook-ul numără ca skill și citirile din cache și `read_skill`/`run_skill` din second-brain: o parte
din creșterea DUPĂ e instrumentare, nu comportament. Uită-te și la „doar skill" vs „doar arona".

## Capcane
- `skill_gap` e scris la FIECARE prompt fără potrivire, deci și la replici de conversație („da", „hai ca le-am
  dovedit"). `min_cuvinte` taie doar o parte; citește lista, nu număra golurile ca cereri reale.
- Cifrele zilei curente sunt parțiale: hook-ul Stop scrie la sfârșitul fiecărui tur.
