# /// script
# requires-python = ">=3.10"
# dependencies = ["boto3>=1.34"]
# ///
"""
cf.py — punte spre Cloudflare pentru toate domeniile ARONA (DNS + R2).

Auth: un singur API token în KB (`CLOUDFLARE_API_TOKEN`) + `CLOUDFLARE_ACCOUNT_ID`.
Tokenul are azi: Zone:Read pe toate zonele + DNS records Read/Edit. R2 = când e
activat pe cont (creds `CLOUDFLARE_R2_*` deja în KB). Secretele NU se printează.

SIGURANȚĂ: DNS-urile sunt de PRODUCȚIE pe magazine live. Orice scriere
(create/update/delete) e DRY-RUN by default și cere `--apply` ca să execute.

Comenzi:
  verify                                   # tokenul e valid? ce poate?
  zones [--filter parfum]                  # listează zonele (domeniu -> zone_id)
  dns-list <domeniu> [--type A] [--name www] [--json]
  dns-get  <domeniu> --name www [--type CNAME]
  dns-create <domeniu> --type TXT --name _foo --content "v=..." [--ttl 1] [--proxied] --apply
  dns-update <domeniu> (--id <rec_id> | --name www --type CNAME) --content "..." [--ttl] [--proxied|--no-proxied] --apply
  dns-delete <domeniu> (--id <rec_id> | --name www --type CNAME) --apply
  r2-buckets                               # (când R2 e activat)
  r2-ls <bucket> [--prefix p] [--max 50]
  r2-put <bucket> <key> <fisier-local> --apply
  r2-get <bucket> <key> <fisier-local>

Exemple:
  uv run cf.py zones --filter nocturna
  uv run cf.py dns-list esteban.ro --type MX
  uv run cf.py dns-create grandia.ro --type TXT --name _verif --content '"abc"' --apply
"""
import os, sys, json, argparse, subprocess, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")
API = "https://api.cloudflare.com/client/v4"


def secret(key):
    """Valoare secret din env, altfel din KB (kb.py secret-get). Nu se printează."""
    v = os.environ.get(key)
    if v:
        return v.strip()
    try:
        out = subprocess.run(["uv", "run", KB, "secret-get", key],
                             capture_output=True, text=True, timeout=30)
        return (out.stdout or "").strip()
    except Exception:
        return ""


def _token():
    t = secret("CLOUDFLARE_API_TOKEN")
    if not t:
        sys.exit("CLOUDFLARE_API_TOKEN lipsește din KB.")
    return t


def cf(method, path, body=None, token=None):
    token = token or _token()
    url = path if path.startswith("http") else API + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"success": False, "errors": [{"message": f"HTTP {e.code}"}]}
    except Exception as e:
        return {"success": False, "errors": [{"message": str(e)}]}


def die_if_fail(resp, what):
    if not resp.get("success"):
        sys.exit(f"❌ {what}: {resp.get('errors')}")
    return resp


def all_zones(token=None):
    """[(name, id)] — toate zonele paginat."""
    out, page = [], 1
    while True:
        r = cf("GET", f"/zones?per_page=50&page={page}", token=token)
        die_if_fail(r, "zones")
        res = r.get("result") or []
        out += [(z["name"], z["id"]) for z in res]
        info = r.get("result_info") or {}
        if page >= (info.get("total_pages") or 1):
            break
        page += 1
    return out


def resolve_zone(domain, token=None):
    domain = domain.strip().lower().lstrip("*.")
    r = cf("GET", f"/zones?name={domain}", token=token)
    if r.get("success") and r.get("result"):
        return r["result"][0]["id"]
    # fallback: match din lista completă (subdomeniu -> zona rădăcină)
    zones = all_zones(token=token)
    for name, zid in zones:
        if domain == name or domain.endswith("." + name):
            return zid
    sys.exit(f"❌ zona pentru '{domain}' nu a fost găsită (tokenul vede {len(zones)} zone).")


# ---------------- commands ----------------
def cmd_verify(a):
    tok = _token()
    acc = secret("CLOUDFLARE_ACCOUNT_ID")
    v = cf("GET", f"/accounts/{acc}/tokens/verify", token=tok) if acc else cf("GET", "/user/tokens/verify", token=tok)
    print("token:", "✅ valid & activ" if v.get("success") else f"❌ {v.get('errors')}")
    zr = cf("GET", "/zones?per_page=1", token=tok)
    print("Zone:Read:", "✅" if zr.get("success") else f"❌ {zr.get('errors')}")
    zones = all_zones(token=tok) if zr.get("success") else []
    print(f"zone vizibile: {len(zones)}")
    if zones:
        dr = cf("GET", f"/zones/{zones[0][1]}/dns_records?per_page=1", token=tok)
        print("DNS records:", "✅ read/edit" if dr.get("success") else f"❌ {dr.get('errors')}")
    if acc:
        r2 = cf("GET", f"/accounts/{acc}/r2/buckets", token=tok)
        print("R2:", "✅ activat" if r2.get("success") else f"❌ {(r2.get('errors') or [{}])[0].get('message')}")


def cmd_zones(a):
    zones = all_zones()
    if a.filter:
        zones = [z for z in zones if a.filter.lower() in z[0].lower()]
    for name, zid in sorted(zones):
        print(f"  {name:28} {zid}")
    print(f"total: {len(zones)}")


def _find_records(zid, name=None, rtype=None, domain=None):
    q = "?per_page=100"
    if rtype:
        q += f"&type={rtype.upper()}"
    if name:
        fqdn = name if (domain and (name.endswith(domain) or name == domain)) else (f"{name}.{domain}" if (domain and name not in ('@',)) else domain)
        if name == "@":
            fqdn = domain
        q += f"&name={fqdn}"
    r = cf("GET", f"/zones/{zid}/dns_records{q}")
    die_if_fail(r, "dns_records")
    return r.get("result") or []


def cmd_dns_list(a):
    zid = resolve_zone(a.domain)
    recs = _find_records(zid, a.name, a.type, a.domain)
    if a.json:
        print(json.dumps(recs, indent=2, ensure_ascii=False)); return
    print(f"{a.domain}: {len(recs)} records")
    for r in recs:
        print(f"  [{r['id']}] {r['type']:6} {r['name']:34} -> {str(r['content'])[:55]:55} ttl={r['ttl']} proxied={r.get('proxied')}")


def cmd_dns_get(a):
    zid = resolve_zone(a.domain)
    recs = _find_records(zid, a.name, a.type, a.domain)
    print(json.dumps(recs, indent=2, ensure_ascii=False))


def _fqdn(name, domain):
    if not name or name == "@":
        return domain
    return name if name.endswith(domain) else f"{name}.{domain}"


def cmd_dns_create(a):
    zid = resolve_zone(a.domain)
    body = {"type": a.type.upper(), "name": _fqdn(a.name, a.domain), "content": a.content,
            "ttl": a.ttl}
    if a.proxied:
        body["proxied"] = True
    print("CREATE:", json.dumps(body, ensure_ascii=False))
    if not a.apply:
        print("(dry-run — adaugă --apply ca să execut)"); return
    r = cf("POST", f"/zones/{zid}/dns_records", body=body)
    die_if_fail(r, "create")
    print("✅ creat id:", r["result"]["id"])


def _resolve_record_id(zid, a):
    if a.id:
        return a.id
    if not (a.name and a.type):
        sys.exit("dă --id SAU (--name și --type) ca să identific recordul.")
    recs = _find_records(zid, a.name, a.type, a.domain)
    if len(recs) == 0:
        sys.exit("nu am găsit recordul.")
    if len(recs) > 1:
        sys.exit(f"{len(recs)} recorduri se potrivesc — folosește --id (vezi dns-list).")
    return recs[0]["id"]


def cmd_dns_update(a):
    zid = resolve_zone(a.domain)
    rid = _resolve_record_id(zid, a)
    cur = cf("GET", f"/zones/{zid}/dns_records/{rid}")
    die_if_fail(cur, "get record")
    cur = cur["result"]
    body = {"type": (a.type or cur["type"]).upper(),
            "name": _fqdn(a.name, a.domain) if a.name else cur["name"],
            "content": a.content if a.content is not None else cur["content"],
            "ttl": a.ttl if a.ttl is not None else cur["ttl"]}
    if a.proxied is not None:
        body["proxied"] = a.proxied
    print(f"UPDATE [{rid}]:", json.dumps(body, ensure_ascii=False))
    print("  (era:", f"{cur['type']} {cur['name']} -> {cur['content']} ttl={cur['ttl']} proxied={cur.get('proxied')})")
    if not a.apply:
        print("(dry-run — adaugă --apply)"); return
    r = cf("PUT", f"/zones/{zid}/dns_records/{rid}", body=body)
    die_if_fail(r, "update")
    print("✅ actualizat")


def cmd_dns_delete(a):
    zid = resolve_zone(a.domain)
    rid = _resolve_record_id(zid, a)
    cur = cf("GET", f"/zones/{zid}/dns_records/{rid}")
    die_if_fail(cur, "get record")
    c = cur["result"]
    print(f"DELETE [{rid}]: {c['type']} {c['name']} -> {c['content']}")
    if not a.apply:
        print("(dry-run — adaugă --apply)"); return
    r = cf("DELETE", f"/zones/{zid}/dns_records/{rid}")
    die_if_fail(r, "delete")
    print("✅ șters")


# ---------------- R2 (S3-compatible) ----------------
def _r2_client():
    import boto3
    ep = secret("CLOUDFLARE_R2_ENDPOINT")
    ak = secret("CLOUDFLARE_R2_ACCESS_KEY_ID")
    sk = secret("CLOUDFLARE_R2_SECRET_ACCESS_KEY")
    if not (ep and ak and sk):
        sys.exit("Creds R2 lipsesc din KB (CLOUDFLARE_R2_*).")
    return boto3.client("s3", endpoint_url=ep, aws_access_key_id=ak,
                        aws_secret_access_key=sk, region_name="auto")


def _r2_guard(e):
    msg = str(e)
    if "SSLV3_ALERT_HANDSHAKE_FAILURE" in msg or "handshake" in msg.lower():
        sys.exit("❌ R2 pare neactivat pe cont (endpoint-ul nu răspunde TLS). Activează R2 în dashboard → R2 → Enable.")
    sys.exit(f"❌ R2: {msg}")


def cmd_r2_buckets(a):
    try:
        for b in _r2_client().list_buckets().get("Buckets", []):
            print(f"  {b['Name']}  (created {b['CreationDate'].date()})")
    except Exception as e:
        _r2_guard(e)


def cmd_r2_ls(a):
    try:
        r = _r2_client().list_objects_v2(Bucket=a.bucket, Prefix=a.prefix or "", MaxKeys=a.max)
        for o in r.get("Contents", []):
            print(f"  {o['Key']:50} {o['Size']:>12} {o['LastModified']}")
        print(f"({r.get('KeyCount',0)} objecte)")
    except Exception as e:
        _r2_guard(e)


def cmd_r2_put(a):
    if not a.apply:
        print(f"PUT {a.file} -> r2://{a.bucket}/{a.key}  (dry-run, adaugă --apply)"); return
    try:
        _r2_client().upload_file(a.file, a.bucket, a.key)
        print(f"✅ urcat r2://{a.bucket}/{a.key}")
    except Exception as e:
        _r2_guard(e)


def cmd_r2_get(a):
    try:
        _r2_client().download_file(a.bucket, a.key, a.file)
        print(f"✅ descărcat -> {a.file}")
    except Exception as e:
        _r2_guard(e)


def main():
    p = argparse.ArgumentParser(description="Cloudflare (DNS + R2) pentru domeniile ARONA")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("verify").set_defaults(fn=cmd_verify)
    sp = sub.add_parser("zones"); sp.add_argument("--filter"); sp.set_defaults(fn=cmd_zones)

    sp = sub.add_parser("dns-list"); sp.add_argument("domain"); sp.add_argument("--type"); sp.add_argument("--name"); sp.add_argument("--json", action="store_true"); sp.set_defaults(fn=cmd_dns_list)
    sp = sub.add_parser("dns-get"); sp.add_argument("domain"); sp.add_argument("--name", required=True); sp.add_argument("--type"); sp.set_defaults(fn=cmd_dns_get)

    sp = sub.add_parser("dns-create"); sp.add_argument("domain"); sp.add_argument("--type", required=True); sp.add_argument("--name", required=True); sp.add_argument("--content", required=True); sp.add_argument("--ttl", type=int, default=1); sp.add_argument("--proxied", action="store_true"); sp.add_argument("--apply", action="store_true"); sp.set_defaults(fn=cmd_dns_create)

    sp = sub.add_parser("dns-update"); sp.add_argument("domain"); sp.add_argument("--id"); sp.add_argument("--name"); sp.add_argument("--type"); sp.add_argument("--content"); sp.add_argument("--ttl", type=int)
    g = sp.add_mutually_exclusive_group(); g.add_argument("--proxied", dest="proxied", action="store_true", default=None); g.add_argument("--no-proxied", dest="proxied", action="store_false")
    sp.add_argument("--apply", action="store_true"); sp.set_defaults(fn=cmd_dns_update)

    sp = sub.add_parser("dns-delete"); sp.add_argument("domain"); sp.add_argument("--id"); sp.add_argument("--name"); sp.add_argument("--type"); sp.add_argument("--apply", action="store_true"); sp.set_defaults(fn=cmd_dns_delete)

    sub.add_parser("r2-buckets").set_defaults(fn=cmd_r2_buckets)
    sp = sub.add_parser("r2-ls"); sp.add_argument("bucket"); sp.add_argument("--prefix"); sp.add_argument("--max", type=int, default=50); sp.set_defaults(fn=cmd_r2_ls)
    sp = sub.add_parser("r2-put"); sp.add_argument("bucket"); sp.add_argument("key"); sp.add_argument("file"); sp.add_argument("--apply", action="store_true"); sp.set_defaults(fn=cmd_r2_put)
    sp = sub.add_parser("r2-get"); sp.add_argument("bucket"); sp.add_argument("key"); sp.add_argument("file"); sp.set_defaults(fn=cmd_r2_get)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
