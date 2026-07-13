#!/bin/bash
# Arona social auto-poster (VPS). Cron: 0 9 * * * (09:00 Berlin = 10:00 RO).
export TZ=Europe/Bucharest
export PATH=/root/.local/bin:$PATH          # so the poster's `uv run mc_post.py` subprocess finds uv
set -a; source /root/.kb_env; set +a        # export KB_DATABASE_URL etc. to child processes
cd /root/social-queue
exec /root/.local/bin/uv run social_queue_poster.py --apply >> /root/social-queue/poster.log 2>&1
