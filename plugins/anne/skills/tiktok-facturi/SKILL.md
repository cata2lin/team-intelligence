---
name: tiktok-facturi
description: Descarcă facturile TikTok Ads (Business Center → Payment management → Invoices — alimentări, consum, note de credit) ca PDF pentru TOATE Business Center-urile Arona (ARONA SRL, NVSDG, NVSDGG, ROSSI…), pe o lună dată, într-un folder pe Desktop (subfolder per BC + folder TOATE + recap.csv cu status Paid / NoNeedToPay / NotaCredit). Merge prin Chrome-ul separat logat în business.tiktok.com (CDP), fiindcă PDF-urile de factură NU sunt în Marketing API. Use pentru „scoate facturile TikTok din <lună>", „facturi TikTok pentru contabilitate", „invoices Business Center", „facturile de ads TikTok". Triggers: facturi tiktok, tiktok invoices, business center invoices, facturi ads tiktok, BDUK.
argument-hint: <YYYY-MM> [--bc "ARONA SRL,NVSDG"] [--out folder] [--port 9223]
---

# tiktok-facturi — facturile TikTok Ads ale tuturor Business Center-urilor, ca PDF

> Autor: **anne**. Făcut pe 2026-09-10 după ce am scos facturile din august 2026 (55 PDF pe ARONA SRL + NVSDG + NVSDGG).
> Frate cu `anne:shopify-facturi` (același mecanism: Chrome separat pe CDP 9223).

## Ce face
Ia live lista Business Center-urilor la care are acces contul logat, apoi pe fiecare citește
lista de facturi (API-ul intern `query_invoice_list`, paginat câte 100), filtrează pe `send_date`
în luna cerută și generează PDF-ul fiecărei facturi (`download/create` → `download/query` → URL semnat). Scrie:
- `<out>/<BC>/<data>_<serie>_<suma><moneda>_<status>.pdf`
- `<out>/TOATE/<BC>_<…>.pdf` — toate la un loc (ce vrea contabilitatea)
- `<out>/recap.csv` — bc, serie (BDUK…), invoice_id, dată, sumă, monedă, status, cont (bg_name), advertiser ids

Implicit `<out>` = `Desktop/facturi tiktok <YYYY-MM>`. Re-rularea sare peste ce e deja în recap.

## Cum se rulează (2 pași)
1. **Chrome separat, logat în TikTok** — NU închide/reporni Chrome-ul principal al utilizatorului:
   ```powershell
   & "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9223 --user-data-dir="$env:LOCALAPPDATA\Temp\claude-chrome-profile" --no-first-run https://ads.tiktok.com/i18n/login
   ```
   Utilizatorul se loghează (contul `gheorghe.beschea@overheat.agency` are Admin/Finance Manager pe BC-urile Arona).
2. ```bash
   uv run scripts/tiktok_facturi.py 2026-08
   uv run scripts/tiktok_facturi.py 2026-08 --bc "ARONA SRL,NVSDG,NVSDGG"
   ```

## Ce să aștepți (sep-2026)
| Business Center | Facturi |
|---|---|
| **ARONA SRL** (7265803690030743553) | cele mai multe (~35/lună): alimentări, consum, note de credit |
| **NVSDG** (7378983274447962113, agenție) | ~12/lună |
| **NVSDGG** (7507183052231753746) | ~9/lună |
| ROSSI 2, ARONA SRL 2/3, Apreciat.ro | facturi vechi, nimic recent |
| Rossi Nails, ROSSI | zero facturi |
| ARONA SRL 4 | API dă „system error" (0 conturi de ads) — ignoră |
| Sellers Alley - BC | agenție externă, „insufficient permission" — normal |

**Statusuri** (verificate pe pagină): `display_status 2` = **Paid**; `4` = **NoNeedToPay** (alimentări acoperite din
sold — apar de obicei în pereche cu o factură Paid de aceeași sumă); `classify 10` / serie `…-CN` = **NotaCredit**.
Toate sunt incluse; contabilitatea decide ce folosește. Seria facturilor = `BDUK…`, monedă RON.

## Capcane
- Facturile NU sunt în TikTok Marketing API (doar tranzacții/sold) → obligatoriu browser logat.
- `download/create` are nevoie de `pa_id` — vine în fiecare rând din listă (`inv["pa_id"]`), nu-l hardcoda.
- URL-ul de PDF e semnat și expiră în ~1h; se descarcă imediat cu `context.request.get` (poartă cookie-urile).
- Pagina are un survey modal „Invoice User Satisfaction" — scriptul apasă Cancel.
- Lista e sortată descrescător pe `send_date`; paginarea se oprește când ultima pagină trece sub luna cerută.

## Logare KB
`kb.py log --type skill --action used --name anne:tiktok-facturi --summary "facturi TikTok <lună>: N PDF, M BC-uri"`.
