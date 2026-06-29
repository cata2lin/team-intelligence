# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
cs_photo.py — VEDE pozele clientului dintr-un tichet Richpanel.

MCP-ul Richpanel taie bytes-ii imaginilor inline, DAR dă URL-ul atașamentului
(bucket public S3 `richpanel-data`). Scriptul scoate URL-urile din get_conversation,
descarcă imaginile și le DESCRIE cu un model vizual (gpt-4o-mini): produs defect/spart?
dovadă de livrare (AWB/SMS/curier)? etichetă? captură de ecran? — util la retur/reclamație.

  uv run cs_photo.py --conv 277664
  uv run cs_photo.py --conv 274972 --json
  uv run cs_photo.py --conv 277664 --save ./poze      # salvează imaginile local
  uv run cs_photo.py --conv 274972 --no-describe       # doar descarcă/listează (fără LLM)

Necesită: RICHPANEL_MCP_TOKEN (KB/env) pt atașamente, OPENAI_API_KEY pt descriere.
NU scrie nimic în Richpanel (read-only).
"""
import os, json, base64, urllib.request, urllib.parse, subprocess, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")
MCP_URL = "https://mcp.richpanel.com/mcp"
IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic")


def enc_url(u):
    """Percent-encode path/query (pozele WhatsApp au spații în nume → urllib crapă pe URL neîncodat)."""
    p = urllib.parse.urlsplit(u)
    return urllib.parse.urlunsplit((p.scheme, p.netloc, urllib.parse.quote(p.path), urllib.parse.quote(p.query, safe="=&%"), p.fragment))


def secret(k):
    v = os.environ.get(k)
    if v:
        return v
    try:
        return subprocess.run(["uv", "run", KB, "secret-get", k], capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        return ""


class MCP:
    def __init__(self, token):
        self.t = token
        self._post({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                    "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "cs-photo", "version": "1"}}})

    def _post(self, p):
        h = {"Authorization": "Bearer " + self.t, "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        b = urllib.request.urlopen(urllib.request.Request(MCP_URL, data=json.dumps(p).encode(), headers=h), timeout=60).read().decode()
        ln = [l for l in b.splitlines() if l.startswith("data:")]
        return json.loads(ln[-1][5:]) if ln else json.loads(b)

    def call(self, name, args):
        r = self._post({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
        txt = r["result"]["content"][0]["text"]
        try:
            return json.loads(txt)
        except Exception:
            return {"_text": txt}


SYS = ("Ești asistent CS ARONA. Descrie pe SCURT (1-2 fraze, factual, în română) ce arată poza trimisă de client într-un tichet: "
       "produs defect/spart/deteriorat (zi exact ce e rupt/lipsă), dovadă de livrare (AWB, SMS/email curier, ce status), etichetă/colet, "
       "captură de ecran (ce text/aplicație). Dacă e relevant pentru o reclamație (defect/retur/livrare), spune clar ce DOVEDEȘTE.")


def describe(img_bytes, ctype, ctx):
    ok = secret("OPENAI_API_KEY")
    if not ok:
        return "(fără OPENAI_API_KEY — nu pot descrie)"
    b64 = base64.b64encode(img_bytes).decode()
    body = {"model": os.environ.get("VISION_MODEL", "gpt-4o-mini"), "temperature": 0, "messages": [
        {"role": "system", "content": SYS},
        {"role": "user", "content": [
            {"type": "text", "text": "Context tichet: " + (ctx or "—")},
            {"type": "image_url", "image_url": {"url": "data:%s;base64,%s" % (ctype or "image/jpeg", b64)}}]}]}
    try:
        req = urllib.request.Request("https://api.openai.com/v1/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Authorization": "Bearer " + ok, "content-type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=90).read())["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return "(eroare descriere: %s)" % e


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--conv", required=True, help="nr conversație Richpanel (sau id)")
    ap.add_argument("--save", default=None, help="director unde să salveze imaginile")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-describe", action="store_true", help="doar descarcă/listează, fără descriere LLM")
    ap.add_argument("--all", action="store_true", help="descrie și pozele trimise de AGENT (implicit doar ale clientului)")
    a = ap.parse_args()

    mcp = MCP(secret("RICHPANEL_MCP_TOKEN"))
    key = "id" if not str(a.conv).isdigit() else "conversation_number"
    cv = mcp.call("get_conversation", {key: str(a.conv), "mode": "audit", "max_messages": 30, "max_message_chars": 300})
    tk = cv.get("ticket") or {}
    ctx = " ".join(((tk.get("subject") or "") + " " + (tk.get("first_message") or "")).split())[:300]
    msgs = (cv.get("messages_page") or {}).get("messages") or cv.get("messages") or []

    atts = []
    seen = set()
    for m in msgs:
        who = "AGENT" if m.get("author_is_workspace_agent") else ("AI" if m.get("is_ai") else "CLIENT")
        for at in (m.get("attachments") or []):
            url = at.get("url") or at.get("href") or at.get("downloadUrl") or ""
            base = url.lower().split("?")[0]
            if not url or not base.endswith(IMG_EXT):
                continue
            key = base.split("/")[-1]   # dedup pe nume fișier (același atașament se repetă în thread)
            if key in seen:
                continue
            seen.add(key)
            atts.append({"idx": m.get("index"), "who": who, "url": url,
                         "name": urllib.parse.unquote(url.split("/")[-1].split("?")[0])})

    if a.save:
        os.makedirs(a.save, exist_ok=True)
    out = []
    for i, at in enumerate(atts):
        try:
            data = urllib.request.urlopen(enc_url(at["url"]), timeout=60).read()
        except Exception as e:
            at["error"] = "download: %s" % e
            out.append(at)
            continue
        at["bytes"] = len(data)
        ext = (os.path.splitext(at["name"])[1] or ".jpg").lower()
        ctype = "image/png" if ext == ".png" else "image/jpeg"
        if a.save:
            p = os.path.join(a.save, "%s_%d%s" % (a.conv, i, ext))
            open(p, "wb").write(data)
            at["saved"] = p
        if not a.no_describe and (a.all or at["who"] == "CLIENT"):
            at["desc"] = describe(data, ctype, ctx)
        out.append(at)

    if a.json:
        print(json.dumps({"conv": a.conv, "subject": ctx, "photos": out}, ensure_ascii=False, indent=1))
        return
    print("📷 Tichet #%s — %s" % (a.conv, ctx[:80]))
    cli = sum(1 for o in atts if o["who"] == "CLIENT")
    print("   %d imagine(i) atașată(e) (%d de la client).\n" % (len(atts), cli))
    for i, o in enumerate(out, 1):
        size = ("%d KB" % (o["bytes"] // 1024)) if o.get("bytes") else o.get("error", "")
        print("  [%d] %s · %s · %s" % (i, o.get("who"), o.get("name"), size))
        if o.get("saved"):
            print("      salvat: %s" % o["saved"])
        if o.get("desc"):
            print("      👁  %s" % o["desc"])
    print()


if __name__ == "__main__":
    main()
