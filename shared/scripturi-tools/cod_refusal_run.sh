#!/bin/bash
# LightGBM refuz COD — wrapper cron. Uz: cod_refusal_run.sh [train|score]
#   train = reantrenează modelul (holdout rulant de 14 zile pt AUC de monitorizare)
#   score = scorează comenzile COD în așteptare -> data/cod_refusal_queue.csv
set -uo pipefail
cd /root/Scripturi || exit 1
set -a; . /root/Scripturi/.env; set +a
UV=/root/.local/bin/uv
KB=team-intelligence/plugins/core/scripts/kb.py
export DB="$("$UV" run "$KB" secret-get DATABASE_URL_AWBPRINT 2>/dev/null)"
if [ -z "$DB" ]; then echo "$(date '+%F %T') EROARE: DB secret gol"; exit 1; fi

STEP="${1:-score}"
if [ "$STEP" = "train" ]; then
  TF="$(date -d '14 days ago' +%F)"
  echo "$(date '+%F %T') === TRAIN (test-from $TF) ==="
  "$UV" run cod_refusal_model.py --test-from "$TF" 2>&1
  rc=$?
else
  # S1 închis (24-oct-2026): coada principală = Order Hub (neplecatele din OH + istoricul de refuz pe telefon din OH).
  # Varianta veche (AWBprint) rulează alături în data/cod_refusal_queue_awb.csv, ca plasă și comparație.
  # Întoarcere: COD_COADA_SURSA=awb în .env (coada principală redevine cea din AWBprint).
  echo "$(date '+%F %T') === SCORE (sursa ${COD_COADA_SURSA:-oh}) ==="
  if [ "${COD_COADA_SURSA:-oh}" = "awb" ]; then
    "$UV" run cod_refusal_score.py --top 30 2>&1
    rc=$?
    "$UV" run cod_refusal_score.py --top 30 --doar-oh --istoric-oh --out data/cod_refusal_queue_oh.csv 2>&1 || echo "coada OH: eroare"
  else
    "$UV" run cod_refusal_score.py --top 30 --doar-oh --istoric-oh 2>&1
    rc=$?
    "$UV" run cod_refusal_score.py --top 5 --out data/cod_refusal_queue_awb.csv 2>&1 >/dev/null || echo "coada AWBprint (plasă): eroare"
  fi
fi
echo "$(date '+%F %T') exit $rc ($STEP)"
exit $rc
