#!/usr/bin/env bash
# Verificare lunară că backup-ul criptat Order Hub chiar se RESTAUREAZĂ.
#
# Pe scurt: ia cel mai nou backup de pe hostul OH, îl copiază cu prioritate minimă,
# verifică sha256, îl decriptează direct în psql într-un Postgres 16 de unică folosință
# (VM colima separat, port doar pe 127.0.0.1), compară tabele/indexuri/FK și numărătorile
# tabelelor cheie cu prod-ul (citire READ ONLY), verifică 3 comenzi recente, apoi ȘTERGE tot.
# Nu scrie nimic în prod și nu atinge containerele orderhub.
#
# Rulare:     bash oh_backup_restore_check.sh            (≈25 min, ~20 GB liber pe disc)
#             bash oh_backup_restore_check.sh --preflight (doar verificările ușoare, fără copiere)
# Programare: launchd ~/Library/LaunchAgents/ro.arona.oh-backup-restore-check.plist (1 ale lunii, 14:00);
#             copia rulată de launchd: ~/Library/Application Support/oh-backup-restore-check/ (în afara ~/Downloads).
# Eșec:       notificare macOS + email (implicit DRAFT în Gmail; OH_RESTORE_MAIL_SEND=1 îl trimite).
#             Emailul folosește ~/Downloads/Scripturi/gmail_sa.py → sub launchd merge DOAR dacă /bin/bash
#             are Full Disk Access; altfel rămân notificarea + logul.
# Ieșire:     0 = backup restaurat și verificat; 1 = eșec (vezi logul din logs/).
set -Eeuo pipefail
export PATH="/usr/local/bin:/opt/homebrew/bin:$HOME/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

OH_HOST="${OH_HOST:-m23726.contaboserver.net}"
OH_BACKUP_DIR="${OH_BACKUP_DIR:-/root/orderhub-backups}"
OH_PASS_FILE="${OH_PASS_FILE:-/root/.orderhub_backup.pass}"
OH_DB="${OH_DB:-order_hub}"
PG_IMAGE="${PG_IMAGE:-postgres:16}"
PROFILE="ohrestore"
SCRIPTURI="${SCRIPTURI:-$HOME/Downloads/Scripturi}"
# launchd nu are voie în ~/Downloads (TCC: "Operation not permitted") → logul stă în ~/Library/Logs;
# ~/Downloads/Scripturi/logs/oh_backup_restore_check e un symlink spre el.
LOG_DIR="${LOG_DIR:-$HOME/Library/Logs/oh-backup-restore-check}"
MAIL_TO="${OH_RESTORE_MAIL_TO:-gheorghe.beschea@overheat.agency}"
MAIL_SEND="${OH_RESTORE_MAIL_SEND:-0}"
BWLIMIT_KBPS="${BWLIMIT_KBPS:-8000}"     # ~8 MB/s: nu sufocă hostul OH
MAX_BACKUP_AGE_H=36
MIN_FREE_GB=40
ORDERS_TOLERANCE=50                       # comenzi create înainte de backup: diferență maximă acceptată
PREFLIGHT=0; [ "${1:-}" = "--preflight" ] && PREFLIGHT=1

mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/oh_backup_restore_check_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG") 2>&1

WORK=""; CNAME=""; PREV_CTX=""; VM_STARTED=0; FAIL_REASON=""; T_START=$(date +%s)
log()  { echo "$(date '+%F %T') $*"; }
fail() { FAIL_REASON="$*"; log "EȘEC: $*"; exit 1; }

LOCK="/tmp/oh_backup_restore_check.lock"
if ! mkdir "$LOCK" 2>/dev/null; then echo "rulează deja ($LOCK)"; exit 0; fi

notify_failure() {
  local subject="[OH backup] restore check EȘUAT $(date +%F)"
  local body; body="$(printf 'Verificarea lunară a backup-ului Order Hub a eșuat.\n\nMotiv: %s\nLog: %s\n\nUltimele linii:\n%s\n' \
    "${FAIL_REASON:-necunoscut (vezi logul)}" "$LOG" "$(tail -40 "$LOG" 2>/dev/null)")"
  osascript -e "display notification \"${FAIL_REASON:-vezi logul}\" with title \"OH backup restore check EȘUAT\"" >/dev/null 2>&1 || true
  local send_flag=(); [ "$MAIL_SEND" = "1" ] && send_flag=(--send)
  (cd "$SCRIPTURI" && uv run gmail_sa.py compose --to "$MAIL_TO" --subject "$subject" \
      --body "$body" "${send_flag[@]+"${send_flag[@]}"}") >/dev/null 2>&1 \
    && log "email ($([ "$MAIL_SEND" = "1" ] && echo trimis || echo draft)) către $MAIL_TO" \
    || log "nu am putut crea emailul de alertă (sub launchd: /bin/bash fără Full Disk Access la ~/Downloads?)"
}

cleanup() {
  local rc=$?
  set +e
  [ -n "$CNAME" ] && docker --context "colima-$PROFILE" rm -f -v "$CNAME" >/dev/null 2>&1
  if [ "$VM_STARTED" = "1" ]; then
    colima delete "$PROFILE" -f >/dev/null 2>&1
    docker context rm -f "colima-$PROFILE" >/dev/null 2>&1
  fi
  [ -n "$PREV_CTX" ] && docker context use "$PREV_CTX" >/dev/null 2>&1
  [ -n "$WORK" ] && rm -rf "$WORK"
  rmdir "$LOCK" 2>/dev/null
  log "curățenie făcută (container, VM $PROFILE, copia backup-ului) — durată totală $(( ($(date +%s)-T_START)/60 )) min"
  if [ $rc -ne 0 ]; then
    [ -z "$FAIL_REASON" ] && FAIL_REASON="eroare neprevăzută (rc=$rc), linia ${ERR_LINE:-?}"
    log "REZULTAT: EȘEC — $FAIL_REASON"
    notify_failure
  else
    log "REZULTAT: OK"
  fi
  exit $rc
}
trap 'ERR_LINE=$LINENO' ERR
trap cleanup EXIT

prod_sql() {  # SQL pe stdin → prod, doar citire
  { echo "SET default_transaction_read_only=on;"; cat; } \
    | ssh -o BatchMode=yes -o ConnectTimeout=20 "$OH_HOST" \
        "nice -n19 runuser -u postgres -- psql -d $OH_DB -At -F'|' -v ON_ERROR_STOP=1" | grep -v '^SET$'
}
test_sql() {  # SQL pe stdin → Postgres-ul de test
  docker --context "colima-$PROFILE" exec -i "$CNAME" psql -U postgres -d "$OH_DB" -At -F'|' -v ON_ERROR_STOP=1
}

# ---------- 1. verificări ușoare ----------
log "start (log: $LOG)"
for b in colima docker gpg ssh rsync shasum uv; do command -v "$b" >/dev/null || fail "lipsește $b"; done
ssh -o BatchMode=yes -o ConnectTimeout=20 "$OH_HOST" true || fail "nu ajung prin ssh la $OH_HOST"

LATEST="$(ssh -o BatchMode=yes "$OH_HOST" "ls -1t $OH_BACKUP_DIR/orderhub-*.sql.gpg 2>/dev/null | head -1")"
[ -n "$LATEST" ] || fail "niciun backup în $OH_BACKUP_DIR"
read -r SIZE MTIME < <(ssh -o BatchMode=yes "$OH_HOST" "stat -c '%s %Y' '$LATEST'")
AGE_H=$(( ($(date +%s) - MTIME) / 3600 ))
STAMP="$(basename "$LATEST" .sql.gpg)"; STAMP="${STAMP#orderhub-}"          # 20261020T011501Z
BACKUP_TS="${STAMP:0:4}-${STAMP:4:2}-${STAMP:6:2} ${STAMP:9:2}:${STAMP:11:2}:${STAMP:13:2}+00"
log "backup: $LATEST ($(( SIZE/1024/1024 )) MB, vechime ${AGE_H}h, făcut la $BACKUP_TS)"
[ "$AGE_H" -le "$MAX_BACKUP_AGE_H" ] || fail "cel mai nou backup are ${AGE_H}h (> ${MAX_BACKUP_AGE_H}h) — cronul de backup nu mai merge"
[ "$SIZE" -gt 100000000 ] || fail "backup suspect de mic: $SIZE bytes"

FREE_GB=$(df -g "$HOME" | awk 'NR==2{print $4}')
[ "$FREE_GB" -ge "$MIN_FREE_GB" ] || fail "doar ${FREE_GB} GB liberi pe Mac (trebuie ${MIN_FREE_GB})"

PROD_VER="$(prod_sql <<<"select current_setting('server_version_num')::int/10000;")"
log "prod Postgres major $PROD_VER, imagine test $PG_IMAGE"
[ "$PG_IMAGE" = "postgres:$PROD_VER" ] || fail "versiunea prod ($PROD_VER) ≠ imaginea de test ($PG_IMAGE): actualizează PG_IMAGE"

if [ "$PREFLIGHT" = "1" ]; then log "preflight OK (fără copiere/restaurare)"; exit 0; fi

# ---------- 2. copiere cu prioritate minimă + checksum ----------
WORK="$(mktemp -d "${TMPDIR:-/tmp}/ohrestore.XXXXXX")"; chmod 700 "$WORK"
T0=$(date +%s)
rsync -q --bwlimit="$BWLIMIT_KBPS" --rsync-path="ionice -c3 nice -n19 rsync" \
  -e "ssh -o BatchMode=yes" "$OH_HOST:$LATEST" "$WORK/oh.sql.gpg" || fail "copierea backup-ului a eșuat"
log "copiat în $(( $(date +%s)-T0 )) s"
REMOTE_SHA="$(ssh -o BatchMode=yes "$OH_HOST" "ionice -c3 nice -n19 sha256sum '$LATEST'" | cut -d' ' -f1)"
LOCAL_SHA="$(shasum -a 256 "$WORK/oh.sql.gpg" | cut -d' ' -f1)"
[ "$REMOTE_SHA" = "$LOCAL_SHA" ] || fail "sha256 diferit (copie coruptă)"
log "sha256 identic ($LOCAL_SHA)"

# ---------- 3. Postgres de unică folosință ----------
PREV_CTX="$(docker context show 2>/dev/null || echo default)"
colima delete "$PROFILE" -f >/dev/null 2>&1 || true        # resturi de la o rulare întreruptă
VM_STARTED=1
colima start "$PROFILE" --cpu 4 --memory 8 --disk 60 --runtime docker >/dev/null 2>&1 || fail "colima nu pornește"
CNAME="ohrestore_$(openssl rand -hex 4)"
docker --context "colima-$PROFILE" run -d --name "$CNAME" -p 127.0.0.1::5432 \
  -e POSTGRES_PASSWORD="$(openssl rand -hex 16)" -e POSTGRES_DB="$OH_DB" --shm-size=1g "$PG_IMAGE" \
  -c fsync=off -c synchronous_commit=off -c full_page_writes=off -c maintenance_work_mem=1GB -c max_wal_size=8GB >/dev/null \
  || fail "containerul Postgres nu pornește"
for _ in $(seq 1 60); do
  docker --context "colima-$PROFILE" exec "$CNAME" pg_isready -U postgres -d "$OH_DB" >/dev/null 2>&1 && break; sleep 2
done
# rolurile din prod (backup-ul are OWNER TO / GRANT pe ele)
prod_sql <<<"select rolname from pg_roles where rolname !~ '^pg_' and rolname <> 'postgres';" \
  | sed 's/.*/create role "&" nologin;/' | test_sql >/dev/null || fail "nu am putut crea rolurile"

# ---------- 4. decriptare + restaurare (cheia vine prin ssh, nu atinge discul) ----------
T0=$(date +%s)
set +e
gpg --batch --quiet --pinentry-mode loopback --no-symkey-cache \
    --passphrase-file <(ssh -o BatchMode=yes "$OH_HOST" "cat $OH_PASS_FILE") \
    -d "$WORK/oh.sql.gpg" 2>"$WORK/gpg.err" \
  | docker --context "colima-$PROFILE" exec -i "$CNAME" psql -U postgres -d "$OH_DB" -q -v ON_ERROR_STOP=1 \
      >/dev/null 2>"$WORK/psql.err"
PS=("${PIPESTATUS[@]}")
set -e
RESTORE_S=$(( $(date +%s)-T0 ))
[ "${PS[0]}" = "0" ] || fail "decriptarea a eșuat (gpg rc=${PS[0]}): $(head -3 "$WORK/gpg.err")"
[ "${PS[1]}" = "0" ] || fail "restaurarea a eșuat (psql rc=${PS[1]}): $(head -5 "$WORK/psql.err")"
[ ! -s "$WORK/psql.err" ] || fail "restaurarea a scris erori: $(head -5 "$WORK/psql.err")"
log "restaurat fără erori în ${RESTORE_S} s"

# ---------- 5. comparații cu prod ----------
read -r -d '' SCHEMA_SQL <<'SQL' || true
select (select count(*) from pg_tables where schemaname='public')
    || '|' || (select count(*) from pg_indexes where schemaname='public')
    || '|' || (select count(*) from pg_constraint where contype='f');
SQL
P_SCHEMA="$(prod_sql <<<"$SCHEMA_SQL")"; T_SCHEMA="$(test_sql <<<"$SCHEMA_SQL")"
log "tabele|indexuri|FK — test $T_SCHEMA, prod $P_SCHEMA"
[ "$P_SCHEMA" = "$T_SCHEMA" ] || fail "schema diferă: test $T_SCHEMA vs prod $P_SCHEMA"

ERRORS=0
for t in orders line_items shipments facturi cs_queue_items scan_events oh_utilizatori; do
  P=$(prod_sql <<<"select count(*) from $t;"); T=$(test_sql <<<"select count(*) from $t;")
  # backup-ul e mai vechi decât prod-ul: test ≤ prod (+0,1% pt. ștergeri) și test ≥ 95% din prod
  if [ "$T" -le $(( P + P/1000 + 5 )) ] && [ $(( T * 100 )) -ge $(( P * 95 )) ]; then st=ok; else st=DIFERIT; ERRORS=$((ERRORS+1)); fi
  log "  $t: test $T, prod $P → $st"
done
P_OB=$(prod_sql <<<"select count(*) from orders where created_at < '$BACKUP_TS';")
T_OB=$(test_sql <<<"select count(*) from orders where created_at < '$BACKUP_TS';")
D=$(( P_OB > T_OB ? P_OB - T_OB : T_OB - P_OB ))
log "  orders create înainte de backup: test $T_OB, prod $P_OB (diferență $D, prag $ORDERS_TOLERANCE)"
[ "$D" -le "$ORDERS_TOLERANCE" ] || ERRORS=$((ERRORS+1))

# 3 comenzi recente: hash pe câmpurile care nu se mai schimbă după creare
SPOT_SQL="select id || '|' || name || '|' || md5(concat_ws('|', id, store_id, shopify_order_id, name,
  extract(epoch from created_at), currency)) from orders
  where created_at < timestamptz '$BACKUP_TS' - interval '1 hour' order by created_at desc, id desc limit 3;"
P_SPOT="$(prod_sql <<<"$SPOT_SQL")"
while IFS='|' read -r id name h; do
  T_H="$(test_sql <<<"select md5(concat_ws('|', id, store_id, shopify_order_id, name, extract(epoch from created_at), currency)) from orders where id=$id;")"
  if [ "$T_H" = "$h" ]; then log "  comanda $name ($id): identică"; else log "  comanda $name ($id): DIFERITĂ/LIPSĂ"; ERRORS=$((ERRORS+1)); fi
done <<<"$P_SPOT"
[ "$(wc -l <<<"$P_SPOT" | tr -d ' ')" = "3" ] || { log "  nu am găsit 3 comenzi de verificat"; ERRORS=$((ERRORS+1)); }

[ "$ERRORS" -eq 0 ] || fail "$ERRORS verificări nu se potrivesc cu prod-ul (vezi mai sus)"
log "VERDICT: backup-ul $STAMP se restaurează și se potrivește cu prod-ul (restaurare ${RESTORE_S} s)"
