#!/bin/bash
# cs_backlog.sh — motorul de draft CS pe backlogul Richpanel.
#
# ⚠️ CRONUL E PAUZAT din 29-iun-2026. Nu-l reporni fără să citești
#    shared/cs-ai-raspuns-documentatie.md §1 — măsurat: din 40 de drafturi scrise, 0 acceptate.
#
# Ce s-a schimbat pe 15-sep-2026:
#  - acoperă TOATE cele 7 canale, nu 5. Comentariile (facebook_feed_comment, instagram_comment)
#    erau EXCLUSE prin --no-comments, adică 59% din volumul CS și 515 tichete OPEN.
#  - --auto-hide: ascunde pe loc comentariile clasificate spam/abuz, pe TOATE magazinele,
#    inclusiv pe piețele unde NU răspundem (MD, CZ). Reversibil, dovedit end-to-end pe Graph.
#  - piețele excluse din răspuns: AI_SKIP_STORES în cs_auto_draft.py (MD, CZ).
set -uo pipefail
cd /root/Scripturi || exit 1
export PATH=/root/.local/bin:$PATH
export PYTHONUNBUFFERED=1
for k in RICHPANEL_MCP_TOKEN OPENAI_API_KEY DATABASE_URL_METRICS META_SYSTEM_TOKEN; do
  v=$(grep -m1 "^${k}=" /root/Scripturi/.env | cut -d= -f2-)
  [ -n "$v" ] && export "$k=$v"
done
# ⚠️ ANTHROPIC_API_KEY NU se exportă: cheia n-are credit (400 „credit balance too low"), iar
# llm() o preferă când există → ar pica TOATE apelurile. Verificat 15-sep-2026.
unset ANTHROPIC_API_KEY
export DRAFT_MODEL="${DRAFT_MODEL:-gpt-4o-mini}"

echo "===== CS BACKLOG $(date -Is) | model=$DRAFT_MODEL ====="
for ch in email email_from_widget facebook_message messenger instagram_message \
          facebook_feed_comment instagram_comment; do
  echo "--- canal: $ch"
  /root/.local/bin/uv run cs_auto_draft.py --channel "$ch" --limit 3000 --scan 6000 \
      --create-draft --ground --auto-hide --skip-tagged --tag ai-draft --sleep 0.5 || true
done
echo "===== gata $(date -Is) ====="
