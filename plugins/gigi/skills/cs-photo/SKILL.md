---
name: cs-photo
description: VEDE pozele atașate de client într-un tichet Richpanel — MCP-ul taie bytes-ii imaginilor, dar dă URL-ul atașamentului (bucket public S3 richpanel-data); scriptul le descarcă și le DESCRIE cu un model vizual (produs defect/spart? dovadă de livrare AWB/SMS/curier? etichetă? captură de ecran?). Util la validarea reclamațiilor de retur/defect/livrare ÎNAINTE de a aproba refund/retrimitere — CS „vede" ce a trimis clientul fără să deschidă manual fiecare atașament. Read-only, nu scrie nimic în Richpanel. Folosește pentru „ce poze a trimis clientul", „vezi atașamentele de la tichetul X", „e chiar spart produsul din poză", „validează reclamația cu poza".
---

# cs-photo — vezi pozele clientului dintr-un tichet Richpanel

MCP-ul Richpanel **taie** bytes-ii imaginilor inline (`evidence_policy: inline images omitted`),
**dar** lasă în metadate `attachments[].url` — link-uri pe bucket-ul **public** S3 `richpanel-data`.
Scriptul scoate URL-urile, descarcă imaginile (HTTP 200, fără auth) și le **descrie cu un model vizual**
(gpt-4o-mini / Claude), ca CS să „vadă" ce a trimis clientul fără să deschidă fiecare atașament în UI.

## Cum rulezi
```bash
uv run cs_photo.py --conv 277664                 # descarcă + descrie pozele clientului
uv run cs_photo.py --conv 274972 --json          # ieșire structurată (integrare)
uv run cs_photo.py --conv 277664 --save ./poze   # + salvează imaginile local
uv run cs_photo.py --conv 274972 --no-describe    # doar descarcă/listează (fără LLM)
uv run cs_photo.py --conv 277664 --all            # descrie și pozele trimise de AGENT
```

## Ce face
1. `get_conversation` (MCP) → scoate `attachments[].url` din mesaje, **dedup pe nume fișier** (același
   atașament se repetă în thread-ul de email), marcat CLIENT / AGENT / AI.
2. Descarcă fiecare imagine (URL **percent-encodat** — pozele WhatsApp au spații în nume, altfel `urllib` crapă).
3. Le **descrie** cu un model vizual, în contextul subiectului tichetului: produs defect/spart (ce e rupt/lipsă),
   dovadă de livrare (AWB/SMS/email curier + status), etichetă/colet, captură de ecran.

## La ce e bun
- **Retur/defect** — vezi dacă produsul e CHIAR deteriorat în poză înainte să aprobi retrimitere/refund (ex. #274972: scaun cu structură ruptă + ambalaj distrus → retur justificat).
- **Dispută livrare** — vezi dovada clientului (ex. #277664: SMS/email DPD că coletul e blocat la depozitul Mogoșoaia, deși s-a cerut livrare la adresă).
- **Validare reclamație** — separă reclamațiile reale (cu dovadă foto) de cele fără.

## Necesită
- `RICHPANEL_MCP_TOKEN` (KB/env) — pt URL-urile atașamentelor.
- `OPENAI_API_KEY` (sau `ANTHROPIC_API_KEY`) — pt descrierea vizuală (`--no-describe` o sare).
- Override model: `VISION_MODEL` (default `gpt-4o-mini`).

## Note
- **Read-only** — nu scrie nimic în Richpanel.
- URL-urile S3 sunt publice (nu conțin token); nu le posta în chat dacă thread-ul are date sensibile în poză.
- Skill-ul `gigi:cs-draft-reply` (flow-ul `cs_auto_draft.py`) folosește ACEEAȘI logică intern (`--photos`,
  implicit pornit) ca draftul să țină cont de conținutul pozelor.
