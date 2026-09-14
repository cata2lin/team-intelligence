---
name: expose-app-behind-nginx
description: "Put an internal app behind nginx + HTTPS instead of exposing a raw public port on the ARONA VPS. Creates the Cloudflare DNS record, an nginx reverse-proxy server block (websocket-safe), a Let's Encrypt cert via certbot, then CLOSES the direct port (ufw AND any manual iptables ACCEPT rule that bypasses ufw). Also the standing security RULE: never leave an app listening on a public port. Triggers: 'put X behind nginx', 'reverse proxy this app', 'expose app safely', 'give this app a domain', 'close the public port', 'stop exposing port N', 'app is on a raw port', 'add subdomain for app', 'secure the VPS ports', 'firewall this service', 'why is port N still open after ufw'."
argument-hint: "expose --sub <name> --port <internal_port> [--body 200M] [--apply]  |  audit  |  close --port N [--apply]"
---

# expose-app-behind-nginx — apps după nginx, NU pe porturi publice

**Regula (durabilă, aplic-o mereu):** niciun app intern nu stă pe un port public brut
(`:5050`, `:8040`, `:3000`…). Îl pui **după nginx** pe un subdomeniu HTTPS și **închizi portul direct**.
Un port brut = fără TLS, fără log central, fără WAF, scanabil direct. Vezi [[vps-security-audit-exposure]].

VPS: `84.46.242.181` (ufw activ, policy DROP; nginx în `/etc/nginx/sites-available/`; certbot instalat;
DNS arona.ro pe **Cloudflare**, subdomenii **DNS-only / proxied=false → IP-ul serverului**).
Secrete din KB (niciodată printate): `CLOUDFLARE_API_TOKEN`, `PROFIT_SSH_HOST/USER/PASS`.

## Comandă rapidă (helper)

```bash
# dry-run implicit; --apply execută
uv run scripts/expose_behind_nginx.py expose --sub cloudtalk --port 8040 --body 100M --apply
uv run scripts/expose_behind_nginx.py audit                 # ce porturi sunt încă publice + reguli iptables manuale
uv run scripts/expose_behind_nginx.py close --port 5050 --apply   # doar închide un port (ufw + iptables manual)
```

## Procedura manuală (ce face pas cu pas — reproductibilă fără script)

### 1. DNS (Cloudflare API) — creează `SUB.arona.ro → IP`, **proxied=false**, ttl 300
Găsește `zone_id` (`GET /zones?name=arona.ro`), apoi `POST /zones/{id}/dns_records`
`{"type":"A","name":"SUB.arona.ro","content":"<IP>","proxied":false,"ttl":300}`.
Potrivește setarea subdomeniilor existente (app/scripts sunt **proxied=false**). Verifică: `dig +short A SUB.arona.ro @1.1.1.1`.

### 2. nginx server block (HTTP întâi — certbot adaugă 443)
`/etc/nginx/sites-available/SUB` + symlink în `sites-enabled/`:
```nginx
server {
    listen 80;
    server_name SUB.arona.ro;
    client_max_body_size 200M;                      # generos pt upload (muzică/video/imgs)
    location / {
        proxy_pass http://127.0.0.1:PORT;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;      # websocket-safe
        proxy_set_header Connection $connection_upgrade;
        proxy_buffering off; proxy_cache off;
        proxy_read_timeout 3600s; proxy_send_timeout 3600s;
    }
}
```
⚠️ `$connection_upgrade` vine dintr-un `map` la nivel http. **Verifică întâi dacă există deja**
(`grep -rl connection_upgrade /etc/nginx`) — dacă nu, adaugă `/etc/nginx/conf.d/ws_upgrade.conf`:
`map $http_upgrade $connection_upgrade { default upgrade; '' close; }`. **Nu-l dubla** (duplicate map = nginx -t fail).
Apoi `nginx -t && systemctl reload nginx`.

### 3. Cert SSL — certbot reutilizează contul existent + adaugă redirect
```bash
certbot --nginx -d SUB.arona.ro --non-interactive --agree-tos --redirect --keep-until-expiring
```
Reînnoire automată deja programată. Certurile: `/etc/letsencrypt/live/SUB.arona.ro/`.

### 4. ÎNCHIDE portul direct — ufw **ȘI** iptables manual (capcana #1)
```bash
ufw --force delete allow PORT/tcp ; ufw --force delete allow PORT
```
⚠️ **CAPCANĂ REALĂ (m-a păcălit pe :5050):** cineva pusese manual
`iptables -A INPUT -p tcp --dport 5050 -j ACCEPT` **înaintea** lanțurilor ufw în INPUT → portul rămâne
deschis oricât ștergi din ufw. Verifică și scoate:
```bash
iptables -S INPUT | grep -E "dport.*ACCEPT" | grep -v ufw     # reguli manuale care ocolesc ufw
iptables -D INPUT -p tcp -m tcp --dport PORT -j ACCEPT        # scoate-o (NU atinge 22/SSH!)
# persistență: dacă e în /etc/iptables/rules.v4, scoate-o și de acolo (sed + .bak)
```

### 5. Verifică — DIN EXTERIOR (nu de pe VPS: hairpin NAT dă fals-000)
```bash
# de pe Mac / o mașină externă:
curl -s -o /dev/null -w '%{http_code}' http://<IP>:PORT/        # trebuie 000 (BLOCAT)
curl -s -o /dev/null -w '%{http_code}' https://SUB.arona.ro/    # trebuie 200/302 (MERGE prin nginx)
```
⚠️ **CAPCANĂ #2:** VPS-ul nu-și poate accesa propriul IP public (hairpin) → `curl` de pe VPS dă `000`
chiar dacă totul e ok. Testează extern, sau pe VPS cu `--resolve SUB.arona.ro:443:127.0.0.1`.

## După ce închizi un port: repointează monitorizarea
Dacă un monitor extern lovea portul brut (ex. **Infrawatch** lovea `:5050` direct), acum e blocat —
repointează-l pe `https://SUB.arona.ro` în dashboard-ul de monitorizare. Vezi [[vps-security-audit-exposure]].

## Checklist mental
1. DNS proxied=**false** (altfel HTTP-01 se complică). 2. ws `map` o singură dată. 3. `nginx -t` înainte de reload.
4. certbot `--redirect`. 5. închide portul: ufw **+ iptables manual** + persistent. 6. verifică **extern**. 7. repoint monitor.

Related: [[vps-security-audit-exposure]] · [[scripturi-project-overview]] · `gigi:ops-health` (deploy/parity VPS).
