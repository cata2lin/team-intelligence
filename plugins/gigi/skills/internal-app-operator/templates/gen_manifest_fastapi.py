# /// script
# requires-python = ">=3.10"
# ///
"""
gen_manifest_fastapi.py — generează endpoints.json (manifestul CLI) din sursa unui app FastAPI.

De ce din SURSĂ și nu din /openapi.json: la app-urile noastre /openapi.json dă adesea 500 (un model
strică generarea schemei), și oricum vrei manifestul BAKED în skill (colegii nu au repo-ul).

Usage:
  uv run gen_manifest_fastapi.py <repo_dir> [out.json] [--api-glob "api/*.py"]

Scoate, per endpoint: area, method, path (cu prefixul routerului), fn, kind (read/mutation),
path_params, modelul Pydantic de body + câmpurile lui, alți parametri, și clasificarea de risc.
Adaptează HIGH_RISK la verbele periculoase ale app-ului tău.
"""
import ast, glob, json, os, re, sys

HIGH_RISK = re.compile(r"(delete|clear|cancel|send-to|push-to|download|remap|"
                       r"generate-and-push|execute|reset|reject|storno|void)", re.I)

def parse_file(path):
    src = open(path, encoding="utf-8").read()
    mod = os.path.basename(path)[:-3]
    tree = ast.parse(src)
    m = re.search(r'APIRouter\(prefix="([^"]*)"', src)
    prefix = m.group(1) if m else ""

    models = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and any(
                isinstance(b, ast.Name) and b.id == "BaseModel" for b in node.bases):
            fields = []
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    ann = ast.unparse(stmt.annotation) if stmt.annotation else None
                    dflt = ast.unparse(stmt.value) if stmt.value is not None else None
                    fields.append((stmt.target.id, ann, dflt))
            models[node.name] = fields

    eps = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
                continue
            method = dec.func.attr.upper()
            if method not in ("GET", "POST", "PUT", "DELETE", "PATCH"):
                continue
            route = dec.args[0].value if dec.args and isinstance(dec.args[0], ast.Constant) else ""
            full = prefix + route
            path_params = re.findall(r"\{(\w+)\}", full)
            body_model, body_fields, other = None, None, []
            for arg in node.args.args:
                if arg.arg in ("authorization", "request", "self"):
                    continue
                ann = ast.unparse(arg.annotation) if arg.annotation else None
                if ann in models:
                    body_model = ann
                    body_fields = [{"name": f, "type": t, "default": d} for f, t, d in models[ann]]
                elif arg.arg not in path_params:
                    other.append({"name": arg.arg, "type": ann})
            eps.append({
                "area": mod, "method": method, "path": full, "fn": node.name,
                "kind": "read" if method == "GET" else "mutation",
                "path_params": path_params, "body_model": body_model,
                "body_fields": body_fields, "other_params": other,
                "risk": "high" if (method == "DELETE" or HIGH_RISK.search(full)) else "normal",
            })
    return eps

def main():
    if len(sys.argv) < 2:
        sys.exit("Usage: gen_manifest_fastapi.py <repo_dir> [out.json] [--api-glob 'api/*.py']")
    repo = os.path.expanduser(sys.argv[1])
    out = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else "endpoints.json"
    glob_pat = "api/*.py"
    if "--api-glob" in sys.argv:
        glob_pat = sys.argv[sys.argv.index("--api-glob") + 1]

    all_eps = []
    for f in sorted(glob.glob(os.path.join(repo, glob_pat))):
        try:
            all_eps.extend(parse_file(f))
        except SyntaxError as e:
            print(f"  ! sar peste {f}: {e}", file=sys.stderr)
    all_eps.sort(key=lambda e: (e["area"], e["path"], e["method"]))
    json.dump(all_eps, open(out, "w"), ensure_ascii=False, indent=1)
    muts = [e for e in all_eps if e["kind"] == "mutation"]
    print(f"{len(all_eps)} endpointuri → {out}: {len(muts)} mutații, {len(all_eps)-len(muts)} citiri, "
          f"{sum(1 for e in all_eps if e['risk']=='high')} high-risk, "
          f"{sum(1 for e in all_eps if e['body_fields'])} cu model de body")

if __name__ == "__main__":
    main()
