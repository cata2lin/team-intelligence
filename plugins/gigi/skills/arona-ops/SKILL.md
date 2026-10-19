---
name: arona-ops
description: "MCP-ul arona-ops — sănătatea CRONURILOR ARONA, READ ONLY: ~115 joburi pe serverul de scripturi (84.46.242.181, root + ceilalți useri + /etc/cron.d) și ~30 pe gazda Order Hub (m23726). Crontab parsat (programare, log, heartbeat legat cu && sau ; — `;` = DEFECT), starea unui job (ultima rulare, coada logului, liniile de eroare, journal CMD, procese agățate), lista de probleme (Traceback, script lipsă, Google Ads v22, heartbeat întârziat, log tăcut, avertismente 401/404/STOP/deprecated), data_health (raport fără mail) și paritatea git↔VPS. Use when: 'ce cron e stricat', 'a rulat jobul X', 'de ce n-a mers cronul', 'ce crăpă în tăcere', 'heartbeat-uri', 'cronuri pe v22', 'log-ul jobului', 'e sănătos serverul de scripturi', 'data health', 'paritate deploy', 'git vs VPS'. Triggers: cron, crontab, heartbeat, data_health, watchdog, job stricat, silent failure, deploy parity, ops."
category: dev-tools
version: 1.0.0
---

# arona-ops — MCP pentru cronuri (READ ONLY)

Server: `mcp_server.py` (FastMCP, stdio). Register:
`claude mcp add --scope user arona-ops -- uv run <abs path>/plugins/gigi/skills/arona-ops/mcp_server.py`

De ce: pe 19-oct două cronuri erau stricate în tăcere — regimul de weekend Grandia pe Google Ads v22 (blocat din
17-oct) și `split_sales_watch`, care crăpa din 9-oct în timp ce heartbeat-ul lui, legat cu `;`, pingăuia mai departe.
`data_health` vede doar heartbeat-urile; tool-urile de aici citesc direct crontab-ul, logurile și sursele.

| Tool | Ce răspunde |
|---|---|
| `cron_lista(server="all", filtru="")` | fiecare job activ: id, user, programare + interval maxim, log + ultima scriere, heartbeat (`&&` / `;`, vârsta ping-ului), semnale |
| `cron_stare(job, linii=40, server="all")` | comanda (secrete mascate), ultima rulare programată, log, liniile de eroare din ULTIMA rulare, heartbeat, procese în viață, `journalctl` CMD (când a pornit efectiv), coada logului |
| `cron_probleme(server="all", cu_info=false)` | CRITIC: script lipsă, eroare în ultima rulare, Google Ads v22 (în sursă sau în log), heartbeat după `;`, heartbeat întârziat / fără ping · DE VERIFICAT: log tăcut N rulări, proces agățat, avertismente (ERROR/STOP/deprecated/401/404) · heartbeat-uri din `cron_heartbeat` fără cron (timere systemd) |
| `data_health(din_log=false)` | rulează `data_health.py` FĂRĂ `--email` (raport, nu trimite mail, nu scrie); `din_log=true` = ultimul raport al cronului de 09:15 |
| `deploy_paritate(toate=false)` | `deploy_parity.check()` fără `git fetch` și fără `--email` (nu atinge `parity_state`): DIFERĂ / FLAT-LIPSĂ pe scripturile deployate flat |

`server` = `scripturi` (alias vps) / `orderhub` (alias oh) / `all`.

## Cum funcționează
- `scripts/cron_collect.py` (doar stdlib) e trimis pe stdin la `python3 -` pe fiecare server, prin ssh cu cheie
  (`core/scripts/arona_ssh.py`; gazda OH = `ARONA_OPS_OH_HOST`, implicit `m23726.contaboserver.net`). Citește:
  `/var/spool/cron/crontabs/*`, `/etc/cron.d/*` (fără certbot/e2scrub/sysstat), `stat` + ultimii 256 KB din log,
  `profitability.db.cron_heartbeat` deschis `mode=ro`, sursele scripturilor (+ un nivel de importuri/scripturi chemate)
  pentru `"v22"` / `/v22/`, `ps`, `journalctl -u cron`.
- **Nu scrie nimic, nu editează crontab-ul, nu rulează niciun job.** Rezultatul `full` e ținut 90 s în memorie.
- „Ultima rulare” = liniile din log începând cu primul timestamp ≥ ultima rulare programată (ora logului — locală sau
  UTC — e dedusă din mtime; o rulare manuală ulterioară, după un salt de >30 min, o înlocuiește). Fără timestamp-uri:
  ultimele 40 de linii.
- Heartbeat întârziat = ping mai vechi decât 2 × intervalul maxim din programare + 30 min (aceeași regulă ca
  data_health, dar pe programarea reală, nu pe intervalul din tabel).
- Log tăcut = ≥2 rulări programate fără nicio scriere, fără heartbeat proaspăt și fără `--quiet`.

## Capcane
- Un log comun mai multor joburi (ex. `cache.log`, `cod_refusal.log`) apare la fiecare job care scrie în el.
- `;` înaintea heartbeat-ului = defect. Excepție de formă: un job al cărui exit 1 înseamnă „am găsit probleme”
  (data_health, promo_watchdog) → tiparul corect e `rc=$?; [ $rc -le 1 ] && heartbeat.py <nume>`.
- Logurile „ghicite” (`~` în cron_lista) vin din corpul scriptului, când cron-ul nu redirecționează ieșirea.
- Heartbeat-urile fără cron sunt de obicei timere systemd (`awb_stocksync_*`) sau joburi retrase — nu le șterge de aici.
- Repararea unui cron = editare pe server, cu OK-ul ownerului; tool-ul doar arată.
