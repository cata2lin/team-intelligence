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
    uv run gas_deploy.py get    --script-id <ID> [--out backup.json]
    uv run gas_deploy.py push   --script-id <ID> --as owner@domain --file Code=new.gs [--file appsscript=manifest.json] [--apply]
    uv run gas_deploy.py create --title "My Script" --as owner@domain [--parent <driveFileId>] [--file Code=code.gs] [--apply]

Notes / gotchas (hard-won):
  * READ (`get`) works with the plain SA. WRITE (`push`) needs impersonation (`--as <owner>`)
    AND the owner must have Apps Script API = ON at https://script.google.com/home/usersettings
    (per-user toggle a service account cannot set) → else 403 "User has not enabled...".
  * The SA needs domain-wide delegation for scope https://www.googleapis.com/auth/script.projects
    (Workspace Admin → Security → API controls → Domain-wide delegation; SA Client/Unique ID).
  * updateContent REPLACES ALL files in the project → this tool re-sends every existing file
    and only swaps the source of the ones you pass; the manifest (appsscript) is preserved.
"""
import argparse, json, os, re, sys, datetime

SCOPES_RO = ["https://www.googleapis.com/auth/drive.readonly",
             "https://www.googleapis.com/auth/script.projects"]
SCOPE_RW = ["https://www.googleapis.com/auth/script.projects"]
SCOPE_DRIVE = ["https://www.googleapis.com/auth/drive"]
SCOPE_SHEETS_RO = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# ── LINT ────────────────────────────────────────────────────────────────────
# Every rule below is a bug that actually broke a team script in production.
# ERROR = refuse to push (unless --force). WARN = print, push anyway.
LINT_RULES = [
    ("ERROR", r"\.getSheetById\s*\(",
     "getSheetById() NU exista in Apps Script (e Sheets API). Foloseste "
     "ss.getSheets().filter(s => s.getSheetId() === gid)[0]. -> scriptul 'nu scrie nimic'."),
    # Bug-ul de "0 la tot". ATENTIE la diferenta (a picat aici un fals-pozitiv):
    #   BUN  (deschis):  '!$' + col + '$2:$' + col            -> prinde datele care intra peste zi
    #   RAU  (marginit): '!$' + col + '$2:$' + col + '$' + lastRow   /   literal $C$2:$C$8408
    ("ERROR", r"\$[A-Z]{1,3}\$2:\$[A-Z]{1,3}\$\d+",
     "Range SURSA cu limita HARDCODATA (ex $C$2:$C$8408) intr-o formula. Datele zilei se "
     "sincronizeaza DUPA ce scrii randul -> cad dincolo de limita -> formula da 0 la tot. "
     "Foloseste range DESCHIS: 'sheet'!$C$2:$C."),
    # independent de numele variabilei: dupa "$2:$"+col mai vine un "$" concatenat = o LIMITA
    ("ERROR", r"\$2:\$[\"']?\s*\+[^\n]*?\+\s*[\"']\$[\"']\s*\+|\$2:\$[^\n]*?(?:getLastRow|lastRow|ultimulRand)",
     "Range SURSA marginit dinamic la getLastRow() ('$2:$' + col + '$' + lastRow). Acelasi bug: "
     "limita e inghetata la momentul rularii -> datele care intra ulterior nu sunt vazute -> 0 la tot. "
     "Lasa range-ul DESCHIS (fara capat)."),
    ("WARN", r"SpreadsheetApp\.flush\s*\(\s*\)",
     "SpreadsheetApp.flush() forteaza recalcul sincron -> pe foi mari = 'ruleaza la infinit'."),
    ("WARN", r"ARRAYFORMULA\([^)\n]{0,120}?[^!$A-Z0-9]([A-Z]{1,2}:[A-Z]{1,2})\b",
     "ARRAYFORMULA pe COLOANA INTREAGA intr-o formula scrisa in celula = recalc greu pe fiecare rand."),
    ("WARN", r"LET\((?=[^)\n]{0,400}(FILTER|ARRAYFORMULA|UNIQUE)\()",
     "LET cu variabile de tip ARRAY se rupe (merge doar pe scalari) -> a intors 0 la FB/TikTok. "
     "Inline-uieste doar scalarii."),
    ("WARN", r"for\s*\([^)]*\)\s*\{[^}]{0,400}?\.setValue\s*\(",
     "setValue() intr-o bucla = 1 apel/celula. Aduna intr-un array si scrie o data cu setValues()."),
    ("WARN", r"for\s*\([^)]*\)\s*\{[^}]{0,400}?\.(getValues|getDataRange)\s*\(",
     "citire din Sheet in bucla = N round-trip-uri. Citeste o data inainte de bucla."),
]

def lint_source(name, src, quiet=False):
    """Returns (n_errors, n_warns). Prints findings with line numbers."""
    errs = warns = 0
    for level, pat, msg in LINT_RULES:
        for m in re.finditer(pat, src, re.I):
            line = src.count("\n", 0, m.start()) + 1
            snippet = src.splitlines()[line - 1].strip()[:90]
            if level == "ERROR": errs += 1
            else: warns += 1
            if not quiet:
                print(f"  {'✖ ERROR' if level == 'ERROR' else '! WARN '} {name}:{line}  {msg}")
                print(f"           > {snippet}")
    if not quiet and not errs and not warns:
        print(f"  ✓ {name}: curat")
    return errs, warns

def cmd_lint(a):
    total_e = total_w = 0
    for spec in a.file:
        if "=" not in spec: sys.exit(f"--file must be NAME=path, got: {spec}")
        name, path = spec.split("=", 1)
        e, w = lint_source(name.strip(), open(path, encoding="utf-8").read())
        total_e += e; total_w += w
    print(f"\nlint: {total_e} ERROR, {total_w} WARN")
    if total_e: sys.exit(1)

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

    # LINT inainte de orice apel de retea — ERROR = refuz push-ul (bug-uri care au picat prod)
    if not a.no_lint:
        print("lint:")
        errs = sum(lint_source(n, s)[0] for n, s in newsrc.items())
        if errs and not a.force:
            sys.exit(f"\n✖ {errs} ERROR de lint — refuz push-ul. Repara, sau --force daca stii ce faci.")
        if errs:
            print(f"\n! {errs} ERROR ignorate (--force)")
        print()

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

    if a.verify_sheet:
        print(f"\n── verificare prin EFECT in Sheet (dupa ce ruleaza trigger-ul) ──")
        v = argparse.Namespace(sheet_id=a.verify_sheet, tab=a.verify_tab, rows=a.verify_rows,
                               key_cols=a.verify_cols, last_col=a.verify_last_col)
        cmd_verify(v)

# ── VERIFY (post-deploy, prin EFECT in Sheet) ───────────────────────────────
# Nu exista "run" remote (scripts.run cere scriptul legat de proiectul GCP al SA + deployment
# API Executable). Verificarea utila e oricum alta: s-a scris ceva REAL in foaie?
# Semnaturile de dezastru pe care le prinde:
#   (a) rand recent cu TOATE metricile 0            -> exact incidentul "0 la tot"
#   (b) formule cu range SURSA marginit (:$X$123)   -> cauza lui (a)
#   (c) erori de formula (#REF!, #N/A, #VALUE!)
def _colname(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s

def cmd_verify(a):
    from googleapiclient.discovery import build
    sh = build("sheets", "v4", credentials=_creds(SCOPE_SHEETS_RO)).spreadsheets()
    rng = f"'{a.tab}'!A1:{a.last_col}"
    vals = sh.values().get(spreadsheetId=a.sheet_id, range=rng,
                           valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [])
    fmls = sh.values().get(spreadsheetId=a.sheet_id, range=rng,
                           valueRenderOption="FORMULA").execute().get("values", [])
    if not vals: sys.exit(f"tab '{a.tab}' e gol sau nu exista")
    header = vals[0]
    body = vals[1:]
    tail = body[-a.rows:] if a.rows else body
    first_row_no = len(body) - len(tail) + 2          # 1-indexed sheet row of tail[0]
    key_idx = ([ord(c.strip().upper()) - 65 for c in a.key_cols.split(",")]
               if a.key_cols else list(range(2, min(len(header), 8))))
    print(f"{a.tab}: {len(body)} randuri | verific ultimele {len(tail)} "
          f"| coloane-cheie {[_colname(i) for i in key_idx]}\n")

    zero_rows, err_cells, bounded = [], [], []
    for ri, row in enumerate(tail):
        rno = first_row_no + ri
        got = [row[i] if i < len(row) else "" for i in key_idx]
        nums = [v for v in got if isinstance(v, (int, float))]
        if got and all((v == 0 or v == "" or v is None) for v in got):
            zero_rows.append((rno, row[1] if len(row) > 1 else "?"))
        for i, v in enumerate(got):
            if isinstance(v, str) and v.startswith("#"):
                err_cells.append((rno, _colname(key_idx[i]), v))
    for ri in range(max(0, len(fmls) - len(tail) - 1), len(fmls) - 1):
        row = fmls[ri + 1] if ri + 1 < len(fmls) else []
        rno = ri + 2
        for ci, cell in enumerate(row):
            if isinstance(cell, str) and cell.startswith("=") and re.search(r":\$?[A-Z]{1,3}\$\d+", cell):
                bounded.append((rno, _colname(ci)))

    bad = False
    if zero_rows:
        bad = True
        print(f"✖ {len(zero_rows)} rand(uri) recente cu TOATE metricile 0 — semnatura incidentului 'nu vede datele':")
        for rno, who in zero_rows[:10]: print(f"    rand {rno}  ({who})")
    if err_cells:
        bad = True
        print(f"✖ {len(err_cells)} celule cu eroare de formula:")
        for rno, col, v in err_cells[:10]: print(f"    {col}{rno} = {v}")
    if bounded:
        cols = sorted({c for _, c in bounded})
        rows_b = sorted({r for r, _ in bounded})
        print(f"! {len(bounded)} celule cu range SURSA MARGINIT (:$X$123) pe randurile {rows_b[0]}–{rows_b[-1]}, "
              f"coloanele {cols}")
        print("   Inofensiv pe randuri vechi (datele au intrat deja), PERICULOS pe randul de azi:")
        print("   datele zilei se sincronizeaza dupa ce scrii randul -> cad in afara limitei -> 0.")
    if not bad:
        print("✓ niciun rand recent complet gol/zero, nicio eroare de formula.")
    # arata ultimul rand ca proba de viata
    if tail:
        last = tail[-1]
        print("\nultimul rand:", {(_colname(i)): (last[i] if i < len(last) else "") for i in ([0, 1] + key_idx)})
    if bad: sys.exit(1)

def cmd_create(a):
    if not a.as_user:
        sys.exit("create needs --as <owner-email> (write/create requires impersonation).")
    files = {}
    for spec in a.file:
        if "=" not in spec: sys.exit(f"--file must be NAME=path, got: {spec}")
        name, path = spec.split("=", 1)
        files[name.strip()] = open(path, encoding="utf-8").read()
    body = {"title": a.title}
    if a.parent: body["parentId"] = a.parent     # bound script (Sheet/Doc/Form Drive id)
    kind = "BOUND la " + a.parent if a.parent else "STANDALONE"
    print(f"mod: {'APLIC (--apply)' if a.apply else 'DRY-RUN (nimic creat)'}")
    print(f"  creez proiect {kind}: titlu={a.title!r} ca {a.as_user}")
    for n in files: print(f"    + fisier initial: {n} ({len(files[n])} chars)")
    if not a.apply:
        print("\nDRY-RUN. Adauga --apply pentru a crea."); return
    svc = _script(a.as_user)
    proj = svc.projects().create(body=body).execute()
    sid = proj["scriptId"]
    print(f"\nCREAT scriptId: {sid}")
    if files:
        cur = svc.projects().getContent(scriptId=sid).execute()   # default Code + appsscript
        out, names = [], set(files)
        for f in cur["files"]:
            out.append({"name": f["name"], "type": f["type"],
                        "source": files.get(f["name"], f.get("source", ""))})
            names.discard(f["name"])
        for n in names:                                           # extra files not in default
            out.append({"name": n, "type": _infer_type(n), "source": files[n]})
        svc.projects().updateContent(scriptId=sid, body={"files": out}).execute()
        print(f"  continut initial scris: {[f['name'] for f in out]}")
    print(f"  editor: https://script.google.com/d/{sid}/edit")

def cmd_trash(a):
    if not a.as_user:
        sys.exit("trash needs --as <owner-email> (Drive trash via impersonation).")
    from googleapiclient.discovery import build
    drive = build("drive", "v3", credentials=_creds(SCOPE_DRIVE, a.as_user))
    meta = drive.files().get(fileId=a.script_id, fields="id,name,mimeType,trashed").execute()
    print(f"mod: {'APLIC (--apply)' if a.apply else 'DRY-RUN (nimic sters)'}")
    print(f"  tinta: {meta['name']!r}  ({meta['mimeType']}, trashed={meta.get('trashed')})")
    if meta["mimeType"] != "application/vnd.google-apps.script":
        sys.exit("  ABORT: nu e un proiect Apps Script — refuz sa-l ating.")
    if not a.apply:
        print("\n  DRY-RUN. Adauga --apply ca sa-l muti la cos (reversibil din Drive)."); return
    drive.files().update(fileId=a.script_id, body={"trashed": True}).execute()
    print(f"\n  TRASHED -> {a.script_id} (recuperabil din Drive Trash ~30 zile)")

def main():
    ap = argparse.ArgumentParser(description="Deploy/patch Google Apps Script code (dry-run default).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("list"); g.add_argument("--as", dest="as_user", default=None)
    g = sub.add_parser("get")
    g.add_argument("--script-id", required=True); g.add_argument("--out", default=None); g.add_argument("--as", dest="as_user", default=None)
    g = sub.add_parser("lint", help="static checks on .gs source (no API call) — the bugs that broke prod")
    g.add_argument("--file", action="append", required=True, help="NAME=path (repeatable)")
    g = sub.add_parser("verify", help="post-deploy check: did the script actually write real values?")
    g.add_argument("--sheet-id", required=True)
    g.add_argument("--tab", required=True, help='ex: "Raport Zilnic 2"')
    g.add_argument("--rows", type=int, default=20, help="cate randuri de la coada verific (default 20)")
    g.add_argument("--key-cols", default=None, help="coloanele metrice, ex C,E,F,G (default C..G)")
    g.add_argument("--last-col", default="Z")
    g = sub.add_parser("push")
    g.add_argument("--script-id", required=True)
    g.add_argument("--as", dest="as_user", required=True, help="owner email to impersonate (write needs it)")
    g.add_argument("--file", action="append", default=[], help="NAME=path (repeatable; NAME = file name in project, no extension)")
    g.add_argument("--backup", default=None)
    g.add_argument("--apply", action="store_true")
    g.add_argument("--no-lint", action="store_true", help="sari peste lint (nerecomandat)")
    g.add_argument("--force", action="store_true", help="pusheaza chiar daca lint-ul da ERROR")
    g.add_argument("--verify-sheet", default=None, help="dupa --apply, verifica efectul in acest Sheet")
    g.add_argument("--verify-tab", default=None)
    g.add_argument("--verify-rows", type=int, default=20)
    g.add_argument("--verify-cols", default=None)
    g.add_argument("--verify-last-col", default="Z")
    g = sub.add_parser("create")
    g.add_argument("--title", required=True, help="title of the new script project")
    g.add_argument("--as", dest="as_user", required=True, help="owner email to impersonate")
    g.add_argument("--parent", default=None, help="Drive file id of a Sheet/Doc/Form -> bound script (else standalone)")
    g.add_argument("--file", action="append", default=[], help="NAME=path initial content (repeatable)")
    g.add_argument("--apply", action="store_true")
    g = sub.add_parser("trash")
    g.add_argument("--script-id", required=True, help="scriptId / Drive file id of the project to trash")
    g.add_argument("--as", dest="as_user", required=True, help="owner email to impersonate (needs DWD drive scope)")
    g.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    {"list": cmd_list, "get": cmd_get, "lint": cmd_lint, "verify": cmd_verify,
     "push": cmd_push, "create": cmd_create, "trash": cmd_trash}[a.cmd](a)

if __name__ == "__main__":
    main()
