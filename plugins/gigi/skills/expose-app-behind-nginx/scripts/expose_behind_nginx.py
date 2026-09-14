# /// script
# requires-python = ">=3.10"
# dependencies = ["paramiko>=3.4"]
# ///
"""expose_behind_nginx.py — pune un app intern după nginx+HTTPS și ÎNCHIDE portul public.

Regula: niciun app pe port public brut. DNS(Cloudflare) → nginx reverse-proxy → certbot → închide portul
(ufw ȘI regula iptables manuală care ocolește ufw). Secrete din KB, niciodată printate. Vezi SKILL.md.

  expose --sub cloudtalk --port 8040 [--body 200M] [--apply]   # tot fluxul
  audit                                                        # porturi încă publice + reguli iptables manuale
  close --port 5050 [--apply]                                  # doar închide un port

Dry-run implicit — fără --apply doar arată ce ar face.
"""
import argparse, json, os, subprocess, sys, urllib.request, urllib.error

ZONE_NAME = "arona.ro"
CF_API = "https://api.cloudflare.com/client/v4"


def _kb_path():
    here = os.path.dirname(os.path.abspath(__file__))
    cand = os.path.normpath(os.path.join(here, "..", "..", "..", "..", "core", "scripts", "kb.py"))
    if os.path.exists(cand):
        return cand
    for p in (os.path.expanduser("~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"),
              "/root/Scripturi/team-intelligence/plugins/core/scripts/kb.py"):
        if os.path.exists(p):
            return p
    sys.exit("kb.py negăsit (nu pot lua credențialele)")


KB = _kb_path()


def sec(k):
    # apel direct (fara `/bin/zsh -lc`): statiile sunt si pe Windows, unde zsh nu exista
    r = subprocess.run(["uv", "run", KB, "secret-get", k], capture_output=True, text=True)
    return r.stdout.strip()


# ---------- Cloudflare (stdlib urllib; tokenul nu iese niciodată în output) ----------
def cf(method, path, token, body=None):
    url = CF_API + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return json.load(e)


def cf_zone_id(token):
    d = cf("GET", f"/zones?name={ZONE_NAME}", token)
    if not d.get("success") or not d.get("result"):
        sys.exit(f"Cloudflare: zona {ZONE_NAME} negăsită / token invalid")
    return d["result"][0]["id"]


def cf_ensure_a(sub, ip, token, apply):
    fqdn = f"{sub}.{ZONE_NAME}"
    zid = cf_zone_id(token)
    ex = cf("GET", f"/zones/{zid}/dns_records?name={fqdn}", token).get("result", [])
    if ex:
        print(f"  DNS {fqdn} există deja → {ex[0]['content']} (proxied={ex[0]['proxied']})")
        return
    if not apply:
        print(f"  [dry-run] aș crea A {fqdn} → {ip} (proxied=false, ttl 300)")
        return
    r = cf("POST", f"/zones/{zid}/dns_records", token,
           {"type": "A", "name": fqdn, "content": ip, "proxied": False, "ttl": 300})
    print(f"  DNS {fqdn} → {'CREAT' if r.get('success') else 'EROARE: ' + str(r.get('errors'))}")


# ---------- SSH ----------
# core/scripts in orice layout de instalare (clona repo, marketplace, plugin-cache
# core/<commit>/scripts). GARDA: iteram parents (fara index fix => fara IndexError) si
# preferam core-ul din ACELASI commit ca skill-ul.
def _core_scripts(need="arona_ssh.py"):
    from pathlib import Path
    h = Path(__file__).resolve()
    c = [Path(os.environ["ARONA_CORE_SCRIPTS"])] if os.environ.get("ARONA_CORE_SCRIPTS") else []
    for up in h.parents:
        c += [up / "core" / "scripts", up / "plugins" / "core" / "scripts"] + \
             (sorted((up / "core").glob("*/scripts")) if (up / "core").is_dir() else [])
    ok = [x for x in c if (x / need).exists()]
    return next((x for x in ok if x.parent.name in h.parts), ok[0] if ok else None)


def _ssh_connect():
    """Helper SSH PARTAJAT (core/scripts/arona_ssh.py): CHEIE intai, apoi ssh-agent, parola
    doar ca ultim resort — VPS-ul accepta doar `publickey`."""
    cs = _core_scripts()
    if cs is None:
        sys.exit("core/scripts/arona_ssh.py negasit — actualizeaza plugin-urile echipei "
                 "sau seteaza ARONA_CORE_SCRIPTS=/cale/spre/plugins/core/scripts")
    if str(cs) not in sys.path:
        sys.path.insert(0, str(cs))
    import arona_ssh
    try:
        return arona_ssh.connect()
    except arona_ssh.SSHAuthError as e:
        sys.exit(str(e))


def run_vps(cmd, timeout=600):
    c = _ssh_connect()
    _, out, err = c.exec_command(cmd, timeout=timeout)
    o = out.read().decode(errors="replace"); e = err.read().decode(errors="replace")
    c.close()
    return o + (("\n" + e) if e.strip() else "")


def server_ip():
    return sec("PROFIT_SSH_HOST")


# ---------- nginx block ----------
def nginx_block(sub, port, body):
    fqdn = f"{sub}.{ZONE_NAME}"
    return f"""set -e
# map $connection_upgrade doar dacă nu există deja (evită duplicate map)
if ! grep -rq connection_upgrade /etc/nginx 2>/dev/null; then
  printf 'map $http_upgrade $connection_upgrade {{\\n    default upgrade;\\n    \\x27\\x27      close;\\n}}\\n' > /etc/nginx/conf.d/ws_upgrade.conf
fi
cat > /etc/nginx/sites-available/{sub} <<'CONF'
server {{
    listen 80;
    server_name {fqdn};
    client_max_body_size {body};
    location / {{
        proxy_pass http://127.0.0.1:{port};
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_buffering off; proxy_cache off;
        proxy_read_timeout 3600s; proxy_send_timeout 3600s;
    }}
}}
CONF
ln -sf /etc/nginx/sites-available/{sub} /etc/nginx/sites-enabled/{sub}
nginx -t && systemctl reload nginx && echo OK_NGINX"""


def close_port_cmd(port):
    return f"""ufw --force delete allow {port}/tcp >/dev/null 2>&1; ufw --force delete allow {port} >/dev/null 2>&1
# capcana: regulă iptables manuală care ocolește ufw (NU atinge 22/SSH)
iptables -D INPUT -p tcp -m tcp --dport {port} -j ACCEPT 2>/dev/null && echo "iptables manual scos"
if [ -f /etc/iptables/rules.v4 ] && grep -q -- "--dport {port} -j ACCEPT" /etc/iptables/rules.v4; then
  sed -i.bak_{port} "/--dport {port} -j ACCEPT/d" /etc/iptables/rules.v4 && echo "scos și din rules.v4"
fi
echo "--- rămâne pt {port}:"; iptables -S INPUT | grep -- "dport {port} " || echo "  (nimic, blocat de DROP)"
ufw status | grep -c "{port}" | sed "s/^/  reguli ufw rămase: /" """


# ---------- comenzi ----------
def cmd_expose(a):
    ip = server_ip()
    fqdn = f"{a.sub}.{ZONE_NAME}"
    print(f"═══ expose {fqdn} → 127.0.0.1:{a.port}  (apply={a.apply}) ═══")
    print("1) DNS Cloudflare:")
    cf_ensure_a(a.sub, ip, sec("CLOUDFLARE_API_TOKEN"), a.apply)
    if not a.apply:
        print("2) nginx: [dry-run] aș crea /etc/nginx/sites-available/%s → :%s (body %s) + ws map + reload" % (a.sub, a.port, a.body))
        print("3) certbot: [dry-run] certbot --nginx -d %s --redirect" % fqdn)
        print("4) închide portul %s: [dry-run] ufw delete + iptables -D manual" % a.port)
        print("\nRulează din nou cu --apply. Apoi testează EXTERN (nu de pe VPS): curl https://%s/" % fqdn)
        return
    print("2) nginx block:"); print("  " + run_vps(nginx_block(a.sub, a.port, a.body)).strip().replace("\n", "\n  "))
    print("3) certbot:")
    print("  " + run_vps(f"certbot --nginx -d {fqdn} --non-interactive --agree-tos --redirect --keep-until-expiring 2>&1 | tail -6").strip().replace("\n", "\n  "))
    print(f"4) închid portul {a.port}:")
    print("  " + run_vps(close_port_cmd(a.port)).strip().replace("\n", "\n  "))
    print("5) verific (loopback+SNI pe VPS; hairpin face curl-de-pe-VPS-la-IP-public fals):")
    print("  " + run_vps(f"curl -s -o /dev/null -w 'https://{fqdn} via nginx → %{{http_code}}\\n' --resolve {fqdn}:443:127.0.0.1 https://{fqdn}/").strip())
    print(f"\n⚠️ Testează BLOCAREA din exterior: curl http://{ip}:{a.port}/  (trebuie 000)")
    print(f"⚠️ Dacă un monitor (Infrawatch) lovea :{a.port} direct, repointează-l pe https://{fqdn}")


def cmd_audit(a):
    print("═══ audit expunere porturi ═══")
    print(run_vps(r"""echo '-- porturi care ASCULTĂ pe 0.0.0.0 (public dacă firewall-ul le lasă):'
ss -tlnp 2>/dev/null | awk '$4 ~ /0.0.0.0:|\[::\]:/ {print "  "$4}' | sort -u
echo '-- reguli ufw ALLOW (v4):'; ufw status | grep ALLOW | grep -v '(v6)' | awk '{print "  "$1}'
echo '-- ⚠️ reguli iptables MANUALE care ocolesc ufw (ACCEPT direct în INPUT):'
iptables -S INPUT | grep -E 'dport.*ACCEPT' | grep -v ufw | sed 's/^/  /' || true
echo '     (fiecare rând = un port deschis PE LÂNGĂ ufw — de investigat/închis)'""").rstrip())


def cmd_close(a):
    print(f"═══ close port {a.port} (apply={a.apply}) ═══")
    if not a.apply:
        print(f"  [dry-run] aș rula: ufw delete allow {a.port} + iptables -D INPUT --dport {a.port} ACCEPT + curăț rules.v4")
        return
    print(run_vps(close_port_cmd(a.port)).rstrip())
    print(f"⚠️ verifică extern: curl http://{server_ip()}:{a.port}/  (trebuie 000)")


def main():
    p = argparse.ArgumentParser(description="pune apps după nginx, închide porturi publice")
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("expose"); e.add_argument("--sub", required=True); e.add_argument("--port", required=True)
    e.add_argument("--body", default="200M"); e.add_argument("--apply", action="store_true"); e.set_defaults(func=cmd_expose)
    au = sub.add_parser("audit"); au.set_defaults(func=cmd_audit)
    c = sub.add_parser("close"); c.add_argument("--port", required=True); c.add_argument("--apply", action="store_true"); c.set_defaults(func=cmd_close)
    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
