# /// script
# requires-python = ">=3.10"
# ///
"""Genereaza un manifest JSON al TUTUROR endpointurilor scripts.arona.ro din sursa FastAPI."""
import ast, json, glob, os, re, sys

REPO = os.path.expanduser("~/Downloads/Scripturi")
HIGH_RISK = re.compile(r"(delete|clear|cancel|send-to-tom|push-to-stores|download|remap|"
                       r"generate-and-push|execute|reset|reject|storno|void)", re.I)

def field_hint(annotation, default):
    t = annotation or "any"
    return t if default is None else f"{t} = {default}"

def parse_file(path):
    src = open(path, encoding="utf-8").read()
    mod = os.path.basename(path)[:-3]
    tree = ast.parse(src)
    prefix = ""
    m = re.search(r'APIRouter\(prefix="([^"]*)"', src)
    if m: prefix = m.group(1)

    # colecteaza modelele Pydantic (BaseModel) din fisier: nume -> [(field, type, default)]
    models = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and any(
                (isinstance(b, ast.Name) and b.id == "BaseModel") for b in node.bases):
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
            # parametrii functiei: path params, body model, query/body simple
            path_params = re.findall(r"\{(\w+)\}", full)
            body_model, body_fields, other_params = None, None, []
            for arg in node.args.args:
                if arg.arg in ("authorization", "request", "self"):
                    continue
                ann = ast.unparse(arg.annotation) if arg.annotation else None
                if ann in models:
                    body_model = ann
                    body_fields = [{"name": f, "type": t, "default": d} for f, t, d in models[ann]]
                elif arg.arg not in path_params:
                    other_params.append({"name": arg.arg, "type": ann})
            eps.append({
                "area": mod, "method": method, "path": full, "fn": node.name,
                "kind": "read" if method == "GET" else "mutation",
                "path_params": path_params,
                "body_model": body_model, "body_fields": body_fields,
                "other_params": other_params,
                "risk": "high" if (method == "DELETE" or HIGH_RISK.search(full)) else "normal",
            })
    return eps

all_eps = []
for f in sorted(glob.glob(os.path.join(REPO, "api", "*.py"))):
    all_eps.extend(parse_file(f))
all_eps.sort(key=lambda e: (e["area"], e["path"], e["method"]))
json.dump(all_eps, open(sys.argv[1] if len(sys.argv) > 1 else "endpoints.json", "w"),
          ensure_ascii=False, indent=1)
muts = [e for e in all_eps if e["kind"] == "mutation"]
print(f"{len(all_eps)} endpointuri: {len(muts)} mutatii, {len(all_eps)-len(muts)} citiri")
print(f"high-risk: {sum(1 for e in all_eps if e['risk']=='high')}")
print(f"cu model de body extras: {sum(1 for e in all_eps if e['body_fields'])}")
