# /// script
# requires-python = ">=3.10"
# dependencies = ["google-auth", "google-api-python-client"]
# ///
"""
gas_deploy.py — read & write Google Apps Script .gs source via the Apps Script API,
using the team `looker-sheets` service account + domain-wide delegation (impersonation).

Deploy/patch team Apps Script code WITHOUT manual copy-paste into the editor.
SAFE BY DESIGN: `push` is DRY-RUN by default (writes nothing) and always backs up the
current content first; add --apply to write; read-back verify runs after every apply.

AUTH (do this first — the SA key lives in the KB, never printed):
    KB=~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py
    export GA4_SA_JSON="$(uv run "$KB" secret-get GA4_SA_JSON)"

Commands:
    uv run gas_deploy.py list
    uv run gas_deploy.py get  --script-id <ID> [--out backup.json]
    uv run gas_deploy.py push --script-id <ID> --as owner@domain --file Code=new.gs [--file appsscript=manifest.json] [--apply]

Notes / gotchas (hard-won):
  * READ (`get`) works with the plain SA. WRITE (`push`) needs impersonation (`--as <owner>`)
    AND the owner must have Apps Script API = ON at https://script.google.com/home/usersettings
    (per-user toggle a service account cannot set) → else 403 "User has not enabled...".
  * The SA needs domain-wide delegation for scope https://www.googleapis.com/auth/script.projects
    (Workspace Admin → Security → API controls → Domain-wide delegation; SA Client/Unique ID).
  * updateContent REPLACES ALL files in the project → this tool re-sends every existing file
    and only swaps the source of the ones you pass; the manifest (appsscript) is preserved.
"""
import argparse, json, os, sys, datetime

SCOPES_RO = ["https://www.googleapis.com/auth/drive.readonly",
             "https://www.googleapis.com/auth/script.projects"]
SCOPE_RW = ["https://www.googleapis.com/auth/script.projects"]

def _creds(scopes, subject=None):
    from google.oauth2 import service_account
    raw = os.environ.get("GA4_SA_JSON")
    if not raw:
        sys.exit("GA4_SA_JSON not set. Run: export GA4_SA_JSON=\"$(uv run <kb.py> secret-get GA4_SA_JSON)\"")
    info = json.loads(raw)
    c = service_account.Credentials.from_service_account_info(info, scopes=scopes)
    return c.with_subject(subject) if subject else c

def _script(subject=None):
    from googleapiclient.discovery import build
    return build("script", "v1", credentials=_creds(SCOPE_RW, subject))

def cmd_list(a):
    from googleapiclient.discovery import build
    drive = build("drive", "v3", credentials=_creds(SCOPES_RO, a.as_user))
    r = drive.files().list(q="mimeType='application/vnd.google-apps.script'",
                           fields="files(id,name,modifiedTime,owners(emailAddress))",
                           pageSize=100, supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
    fs = r.get("files", [])
    print(f"{len(fs)} Apps Script project(s) visible to the SA:")
    for f in fs:
        own = (f.get("owners") or [{}])[0].get("emailAddress", "?")
        print(f"  {f['id']}  | {f['name']}  | owner={own}  | {f.get('modifiedTime','')}")

def cmd_get(a):
    c = _script(a.as_user).projects().getContent(scriptId=a.script_id).execute()
    out = a.out or f"gas_backup_{a.script_id[:8]}.json"
    json.dump(c, open(out, "w"), ensure_ascii=False, indent=2)
    print(f"scriptId: {c.get('scriptId')}  | files: {len(c.get('files',[]))}  | backup -> {out}")
    for f in c.get("files", []):
        print(f"  - {f['name']}.{f['type']}  ({len(f.get('source',''))} chars)")

def _infer_type(name):
    n = name.lower()
    if n == "appsscript": return "JSON"
    if n.endswith(".html") or n == "index": return "HTML"
    return "SERVER_JS"

def cmd_push(a):
    if not a.as_user:
        sys.exit("push needs --as <owner-email> (write requires impersonation).")
    # parse --file NAME=path (name = file name in project, no extension)
    newsrc = {}
    for spec in a.file:
        if "=" not in spec: sys.exit(f"--file must be NAME=path, got: {spec}")
        name, path = spec.split("=", 1)
        src = open(path, encoding="utf-8").read()
        if not src.strip(): sys.exit(f"refuse to push empty content for '{name}' ({path})")
        newsrc[name.strip()] = src
    if not newsrc: sys.exit("nothing to push (pass --file NAME=path)")

    svc = _script(a.as_user)
    cur = svc.projects().getContent(scriptId=a.script_id).execute()

    # always back up current content before any change
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bk = a.backup or f"gas_backup_{a.script_id[:8]}_{ts}.json"
    json.dump(cur, open(bk, "w"), ensure_ascii=False, indent=2)
    print(f"backup -> {bk}\n")

    existing = {f["name"]: f for f in cur["files"]}
    files_out, changes = [], []
    for f in cur["files"]:
        if f["name"] in newsrc:
            old = f.get("source", ""); new = newsrc[f["name"]]
            files_out.append({"name": f["name"], "type": f["type"], "source": new})
            changes.append((f["name"], len(old), len(new), "modificat"))
        else:
            files_out.append({"name": f["name"], "type": f["type"], "source": f.get("source", "")})
    for name, src in newsrc.items():           # files that don't exist yet -> add
        if name not in existing:
            t = _infer_type(name)
            files_out.append({"name": name, "type": t, "source": src})
            changes.append((name, 0, len(src), f"NOU ({t})"))

    print(f"mod: {'APLIC (--apply)' if a.apply else 'DRY-RUN (nimic scris)'}")
    for name, o, n, what in changes:
        print(f"  {what:14} {name:18} {o} -> {n} chars")
    keep = [f['name'] for f in files_out if f['name'] not in newsrc]
    print(f"  pastrate neschimbat: {keep}")

    if not a.apply:
        print("\nDRY-RUN. Adauga --apply pentru a scrie."); return

    r = svc.projects().updateContent(scriptId=a.script_id, body={"files": files_out}).execute()
    # read-back verify
    rb = {f["name"]: f.get("source", "") for f in svc.projects().getContent(scriptId=a.script_id).execute()["files"]}
    ok = all(rb.get(n) == s for n, s in newsrc.items())
    print(f"\nUPDATE: {'OK' if ok else 'ATENTIE — read-back nu coincide!'}  | fisiere in proiect: {[f['name'] for f in r.get('files',[])]}")
    if not ok: sys.exit("read-back mismatch — verifica manual (backup pastrat).")

def main():
    ap = argparse.ArgumentParser(description="Deploy/patch Google Apps Script code (dry-run default).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("list"); g.add_argument("--as", dest="as_user", default=None)
    g = sub.add_parser("get")
    g.add_argument("--script-id", required=True); g.add_argument("--out", default=None); g.add_argument("--as", dest="as_user", default=None)
    g = sub.add_parser("push")
    g.add_argument("--script-id", required=True)
    g.add_argument("--as", dest="as_user", required=True, help="owner email to impersonate (write needs it)")
    g.add_argument("--file", action="append", default=[], help="NAME=path (repeatable; NAME = file name in project, no extension)")
    g.add_argument("--backup", default=None)
    g.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    {"list": cmd_list, "get": cmd_get, "push": cmd_push}[a.cmd](a)

if __name__ == "__main__":
    main()
