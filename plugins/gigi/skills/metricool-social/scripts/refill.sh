#!/bin/sh
# Wrapper cron pentru auto-refill-ul cozii. Aceleasi 3 capcane ca la run.sh:
#   PATH (uv in subprocese) · KB_DATABASE_URL exportat la copii · TZ RO.
export TZ=Europe/Bucharest
export PATH=/root/.local/bin:$PATH
set -a; . /root/.kb_env; set +a
cd /root/social-queue
exec /root/.local/bin/uv run refill_queue.py --min 3 --target 5
