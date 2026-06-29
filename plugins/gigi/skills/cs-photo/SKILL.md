---
name: cs-photo
description: Modulul CANONIC de „vedere" a pozelor unui tichet Richpanel — folosit ca CLI ȘI importat de alte scripturi CS (ex. cs-draft-reply). Vede DOUĂ poze, ambele pentru context. (a) Poza pe care o lasă CLIENTUL (atașament în mesaj): MCP-ul taie bytes-ii dar dă URL-ul (S3 public richpanel-data) → descarcă + descrie vizual (defect/spart, dovadă livrare AWB/SMS, etichetă, screenshot) — util la validarea retur/defect/livrare înainte de refund/retrimitere. (b) Poza RECLAMEI/POSTĂRII pe care comentează clientul (tichete FB/IG comment): fără token de pagină, prin HTTP GET cu UA facebookexternalhit → og:image → descrie ce PRODUS e în reclamă (esențial la magazinele deals unde nu știi produsul din întrebarea „ce preț?/dimensiuni?"). REGISTRU: fiecare postare descrisă o dată se salvează (post_id→produs) și se refolosește; cele noi se completează incremental. Read-only. Folosește pentru „ce poze a trimis clientul", „la ce produs se referă comentariul", „vezi reclama comentată", „validează reclamația din poză".
---

# cs-photo — vede pozele unui tichet (poza clientului + poza reclamei comentate)

Modul **canonic + reutilizabil**: îl rulezi ca CLI, dar e și **importat de alte scripturi CS**
(`cs-draft-reply/cs_auto_draft.py` îl referențiază ca să „vadă" pozele în draft). Vede **două** tipuri de poze:

### (a) Poza pe care o LASĂ CLIENTUL (atașament)
MCP-ul Richpanel taie bytes-ii inline, dar lasă `attachments[].url` pe bucket-ul **public** S3
`richpanel-data` (HTTP 200, fără auth). Scriptul le descarcă + le **descrie vizual** (gpt-4o-mini / Claude):
defect/spart (ce e rupt/lipsă), dovadă de livrare (AWB/SMS/email curier), etichetă, captură de ecran.

### (b) Poza RECLAMEI/POSTĂRII pe care comentează clientul (tichete FB/IG comment)
Calea Graph cu token de pagină e moartă pe majoritatea paginilor (n-avem acces). Ocolire **fără token**:
HTTP GET pe URL-ul postării cu UA `facebookexternalhit/1.1` → Facebook servește `og:image` + `og:title` →
descarcă poza → o **descrie** (ce PRODUS se promovează). Așa știm la ce se referă „Ce preț are?/Dimensiunile?/
Sunt turcești?" — **esențial la magazinele deals** (Casa Ofertelor, Grandia, Magdeal…), unde reclama poate fi
orice produs. La parfumuri (GT/Esteban/Nubra) produsul e mereu parfum → valoare mai mică, dar e cache-uit (ieftin).

### REGISTRU (post_id → ce e), completat incremental
Fiecare postare descrisă o dată se **salvează** într-un SQLite (`FB_POST_DB`, default lângă script). Când apare
un comentariu pe o postare **știută** → se refolosește (zero cost); pe una **nouă** → se descrie + se adaugă.
Pune `FB_POST_DB` pe o cale partajată (NAS) pt un registru de echipă.

## CLI
```bash
uv run cs_photo.py --conv 277664              # poze client + (dacă e comentariu) reclama comentată
uv run cs_photo.py --conv 277744 --json
uv run cs_photo.py --conv 277664 --save ./poze
uv run cs_photo.py --conv 274972 --no-describe   # doar descarcă/listează
uv run cs_photo.py --conv 277744 --no-ad          # sări rezolvarea reclamei
uv run cs_photo.py --registry-list                # ce postări avem salvate
uv run cs_photo.py --registry-build --scan 200     # populează registrul din comentariile recente (incremental)
```

## Importat din alt script (modul canonic)
```python
import sys, os
sys.path.insert(0, os.path.join(HERE, "..", "cs-photo"))   # layout skill; pe VPS pune cs_photo.py lângă script
import cs_photo as csp
blk_client = csp.client_photos_block(msgs, ctx)   # text cu pozele clientului (sau '')
blk_ad     = csp.ad_block(ticket)                  # text cu reclama comentată (sau '')
```
`cs-draft-reply` îl folosește exact așa: injectează **ambele** blocuri în context → draftul ține cont de
poza clientului (defect/dovadă) ȘI de produsul din reclama comentată.

## La ce e bun
- **Retur/defect** — vezi dacă produsul e chiar deteriorat (ex. #274972 scaun rupt + ambalaj distrus).
- **Dispută livrare** — vezi dovada (ex. #277664 SMS/email DPD colet blocat la depozit).
- **Comentariu presale** — vezi produsul din reclamă (ex. #277709 „dimensiunile?" → bancă de hol cu depozitare).

## Necesită
- `RICHPANEL_MCP_TOKEN` (atașamente + listă comentarii), `OPENAI_API_KEY` sau `ANTHROPIC_API_KEY` (descriere vizuală).
- `VISION_MODEL` (default `gpt-4o-mini`), `FB_POST_DB` (cale registru, default lângă script).

## Note
- **Read-only** — nu scrie nimic în Richpanel; reclama se ia public (og:image), fără token de pagină.
- IG comments: rezolvarea reclamei e best-effort (URL FB); dacă nu vine og:image → produs gol (fără eroare).
- Registrul (`fb_post_registry.sqlite`) e gitignored (date locale).
