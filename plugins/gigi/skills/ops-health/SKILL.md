---
name: ops-health
description: "System-health, silent-failure monitoring, and safe deploy for the ARONA data pipeline on the VPS. One entry point over the tooling built to stop silent failures (the Meta token died 11 days unnoticed; profit_orders went 23 days unsynced and reported July as −1.5M loss). Runs: data freshness + cron dead-man-switch (data_health), 3-source reconciliation engine↔AWBprint↔warehouse (reconcile_sources), git↔VPS code parity (deploy_parity), git-driven safe deploy (deploy.sh), and a consistent profitability.db backup. Triggers: 'is anything broken', 'system health', 'is the data fresh', 'did the cron run', 'which cron died', 'which sources diverge', 'reconcile engine vs awbprint', 'deploy the tools', 'git vs vps drift', 'code parity', 'backup the profit db', 'are the watchdogs ok', 'ops health', 'monitoring'."
argument-hint: "health | reconcile [--months N] | parity | deploy [--apply] | backup | cron"
---

# ops-health — monitorizare + operare sigură (VPS)

Un singur punct de intrare peste uneltele de ops construite ca să **prindem eșecurile TĂCUTE**
(tokenul Meta mort 11 zile nedetectat; `profit_orders` nesincronizat 23 zile → iulie raportat −1,5M fals).
Toate rulează pe VPS prin SSH (parola din KB, niciodată printată). Read-only sau sigur (deploy = dry-run implicit).

## Comenzi

```bash
uv run scripts/ops.py health                 # data_health: prospețime DATE per pipeline vs SLA + heartbeat cronuri
uv run scripts/ops.py reconcile --months 3   # reconcile_sources: divergențe engine↔AWBprint (livrate) & sheet↔warehouse (marketing) + istoric drift
uv run scripts/ops.py parity                 # deploy_parity check: cod git(origin/main) ↔ fișiere flat VPS (IDENTIC/DIFERĂ)
uv run scripts/ops.py deploy                 # deploy.sh DRY-RUN: ce fișiere s-ar sincroniza din git
uv run scripts/ops.py deploy --apply         # deploy.sh: sync flat (cu .bak) + pull --ff-only checkout
uv run scripts/ops.py backup                 # backup_profitdb: snapshot consistent + gzip + rotație(7) acum
uv run scripts/ops.py cron                   # lista cronurilor active
```

## Ce verifică fiecare (și cine trimite email singur)
- **`health`** (cron VPS 09:15, email pe roșu) — spend/brand_pnl/fx/tokenuri/sync_runs/AWBprint/WMS/`profit_orders` +
  **`brand_pnl.gol`** (marketing>0 dar venit/livrate=0 → P&L FALS) + **heartbeat cronuri** (dead-man-switch: „n-a rulat DELOC").
  Principiul: verifică IEȘIREA pipeline-ului vs SLA, NU dacă „a rulat jobul" (logul se scrie și când sync-ul eșuează).
- **`reconcile`** (cron 09:30, email DOAR pe drift NOU) — compară VALORI între surse INDEPENDENTE. Livrate coincid <2%
  când datele-s proaspete; un offset sistematic ~+4% pe marketing e definiție (sub prag). Istoric în `recon_history`.
- **`parity`** (cron 09:45) — cauza bombelor de drift: fișiere copiate de mână care diverg de git. Email pe fișier nou-divergent.
- **`deploy`** — **modul CORECT de deploy** (NU scp manual). `git fetch` + sync flat via parity (cu `.bak`) + `pull --ff-only`
  (imposibil să piardă mods locale; sare curat dacă checkout-ul are modificări).
- **`backup`** (cron 03:30) — `profitability.db` (~333MB, tot motorul de profit) → snapshot consistent (SQLite online-backup API,
  sigur cu writeri) + gzip (→60MB) + rotație. Era FĂRĂ backup automat.

## Note
- Codul real trăiește în `shared/scripturi-tools/` (git) + `/root/Scripturi/*.py` (VPS, deployat). Vezi memoria
  [[data-health-watchdog]] + `shared/HARTA.md` secțiunea „Monitorizare + operare".
- Erorile de COD (excepții) = separat, în **Sentry** (app intern instrumentat + MCP `sentry`). Vezi [[sentry-error-monitoring]].
- Toate email-urile merg pe Gmail API (SA Workspace) DOAR pe erori reale/noi — nu spam zilnic.
