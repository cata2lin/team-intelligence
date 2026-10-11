#!/bin/bash
# cs_backlog.sh — motorul de draft CS pe backlogul Richpanel.
#
# PORNIT 16-sep-2026, decizie de owner („pornim si analizam dupa").
# OPRIT 17-sep-2026 (decizie owner, vezi crontab pe VPS): create_draft ADAUGA, nu exista delete prin
# API, iar pe pietele straine nimeni nu trimitea drafturile. Fisierul ramane versiunea de rollback.
#
# CE FACE: scrie DRAFTURI in Richpanel. NU trimite nimic — un agent CS trebuie sa apese
# trimite. Masurat pe 321 de tichete straine: 228 gata de trimis, 6 mai rele decat tacerea
# (inainte de reparatiile de azi: 120 si 123).
#
# DOUA COMUTATOARE LASATE DELIBERAT OPRITE, se aprind DOAR cu decizia ownerului:
#  --auto-hide   ascunde pe loc comentarii pe Facebook. E singura actiune care ATINGE
#                clientul automat; restul e draft-only. Lipsa lui nu strica nimic.
#  --include-skipped  raspunde si pe MD si CZ (excluse azi prin AI_SKIP_STORES).
#
# --comments PORNIT: canalul public e cel mai curat din masuratoare (2 drafturi rele din
# 251 = 0,8%; pe BG public 1 din 176, de la 84 din 176). Si e pornit IMPREUNA cu DM-ul,
# fiindca un draft public care spune „scrieti-ne in privat" e o promisiune: daca nu
# raspundem si in DM, e mai rau decat tacerea.
set -uo pipefail
cd /root/Scripturi || exit 1
export PATH=/root/.local/bin:$PATH
export PYTHONUNBUFFERED=1
for k in RICHPANEL_MCP_TOKEN OPENAI_API_KEY DATABASE_URL_METRICS META_SYSTEM_TOKEN; do
  v=$(grep -m1 "^${k}=" /root/Scripturi/.env | cut -d= -f2-)
  [ -n "$v" ] && export "$k=$v"
done
# 16-sep 11:20: OpenAI a ramas FARA CREDIT in timpul primei rulari (HTTP 429 „no credits
# remaining"). Anthropic ARE credit (verificat: /v1/models raspunde 200). Deci pornim pe
# Anthropic; llm() o prefera oricum cand cheia exista, si are failover automat pe OpenAI
# daca se alimenteaza la loc.
v=$(grep -m1 "^ANTHROPIC_API_KEY=" /root/Scripturi/.env | cut -d= -f2-)
[ -n "$v" ] && export ANTHROPIC_API_KEY="$v"
# ⚠️ CELE DOUA BRATE CITESC VARIABILE DIFERITE — de aia a murit lotul de aseara:
#   _llm_anthropic -> ANTHROPIC_MODEL (implicit claude-sonnet-4-6)
#   _llm_openai    -> DRAFT_MODEL     (implicit gpt-5-mini)
# Pusesem DRAFT_MODEL=claude-haiku-... Anthropic l-a IGNORAT (de aia rularea manuala a mers),
# iar cand Anthropic a ramas fara credit si failover-ul a comutat pe OpenAI, OpenAI a primit
# un nume de model Claude -> HTTP 404 pe TOT restul lotului. 1030 de tichete au asteptat degeaba.
export ANTHROPIC_MODEL="${ANTHROPIC_MODEL:-claude-haiku-4-5-20251001}"
export DRAFT_MODEL="${DRAFT_MODEL:-gpt-4o-mini}"
# registrul de incidente: fara el, recunoasterea incidentului BG (145 de comenzi) nu se aprinde
export CS_INCIDENTS_FILE=/root/Scripturi/data/cs_incidents.json
export PROFIT_DB=/root/Scripturi/data/profitability.db

echo "===== CS BACKLOG $(date -Is) | openai=$DRAFT_MODEL anthropic=$ANTHROPIC_MODEL | incidente=$CS_INCIDENTS_FILE ====="
# O SINGURA rulare, FARA --channel: garda din motor refuza drafturi publice cand DM-ul nu e
# in aceeasi rulare, iar bucla pe canale il excludea de fiecare data. Masurat pe Messenger BG:
# 45 din 47 de fire fara raspuns — de aia un „scrieti-ne in privat" public fara DM e o promisiune goala.
/root/.local/bin/uv run cs_auto_draft.py --limit 3000 --scan 12000 \
    --create-draft --ground --comments --skip-tagged --tag ai-draft --sleep 0.5 || true
echo "===== gata $(date -Is) ====="
