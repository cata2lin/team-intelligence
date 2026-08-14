"""Colete pe produs — pagina pt DEPOZIT: câte bucăți intră într-un colet, per produs.
Salvează în SQLite (parcel_density.db) + alimentează IMEDIAT map-ul central sku_box_map.json
pe care cronul de AWB (order_parcel_count) îl folosește. Servit sub /colete pe scripts.arona.ro."""
import os, json, sqlite3, time
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

DATA = "/root/Scripturi/data"
DB = os.path.join(DATA, "parcel_density.db")
PRODUCTS = os.path.join(DATA, "parcel_products.json")
MAP = os.path.join(DATA, "sku_box_map.json")
PREFIX = "/colete"


def db():
    c = sqlite3.connect(DB, timeout=15)
    c.row_factory = sqlite3.Row
    return c


def _init():
    c = db()
    c.execute("""create table if not exists parcel_density(
        sku text primary key, per_parcel integer, nr_cutii real, note text,
        updated_at text, updated_by text)""")
    c.commit(); c.close()


_init()
app = FastAPI()


def load_products():
    try:
        return json.load(open(PRODUCTS, encoding="utf-8"))
    except Exception:
        return []


@app.get(PREFIX + "/api/products")
def api_products():
    prods = load_products()
    c = db()
    saved = {r["sku"]: dict(r) for r in c.execute("select * from parcel_density")}
    c.close()
    out = []
    for p in prods:
        s = saved.get(p.get("sku"))
        out.append({
            "sku": p.get("sku"), "title": p.get("title") or "", "img": p.get("img") or "",
            "per_parcel": (s["per_parcel"] if s else None),
            "note": (s["note"] if s else "") or "",
            "by": (s["updated_by"] if s else "") or "",
        })
    done = sum(1 for x in out if x["per_parcel"])
    return {"products": out, "total": len(out), "done": done}


@app.post(PREFIX + "/api/save")
async def api_save(req: Request):
    b = await req.json()
    sku = (b.get("sku") or "").strip()
    if not sku:
        return JSONResponse({"ok": False, "err": "sku lipsă"}, status_code=400)
    pp = b.get("per_parcel")
    by = (b.get("by") or "").strip()[:40]
    note = (b.get("note") or "").strip()[:200]
    now = time.strftime("%Y-%m-%d %H:%M")
    c = db()
    if pp in (None, "", 0, "0"):
        c.execute("delete from parcel_density where sku=?", (sku,))
        nr = None
    else:
        try:
            pp = int(float(pp))
        except Exception:
            c.close()
            return JSONResponse({"ok": False, "err": "număr invalid"}, status_code=400)
        if pp < 1:
            pp = 1
        nr = round(1.0 / pp, 4)
        c.execute("""insert into parcel_density(sku,per_parcel,nr_cutii,note,updated_at,updated_by)
            values(?,?,?,?,?,?)
            on conflict(sku) do update set per_parcel=excluded.per_parcel, nr_cutii=excluded.nr_cutii,
              note=excluded.note, updated_at=excluded.updated_at, updated_by=excluded.updated_by""",
                  (sku, pp, nr, note, now, by))
    c.commit(); c.close()
    # alimentează IMEDIAT map-ul central (order_parcel_count îl reîncarcă pe mtime)
    try:
        m = json.load(open(MAP, encoding="utf-8"))
    except Exception:
        m = {}
    if nr is None:
        m.pop(sku, None)
    else:
        m[sku] = nr
    tmp = MAP + ".tmp"
    json.dump(m, open(tmp, "w", encoding="utf-8"))
    os.replace(tmp, MAP)
    return {"ok": True, "sku": sku, "per_parcel": pp if nr is not None else None, "nr_cutii": nr}


@app.get(PREFIX)
@app.get(PREFIX + "/")
def page():
    return HTMLResponse(PAGE)


PAGE = r"""<!doctype html><html lang="ro"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Colete pe produs — Depozit</title>
<style>
:root{--bg:#f4f5f7;--card:#fff;--line:#e6e8eb;--txt:#1e2229;--mut:#6b7280;--acc:#2563eb;--ok:#16a34a}
*{box-sizing:border-box}
body{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial;background:var(--bg);color:var(--txt)}
header{position:sticky;top:0;z-index:10;background:#fff;border-bottom:1px solid var(--line);padding:10px 14px;box-shadow:0 1px 4px rgba(0,0,0,.04)}
.h1{font-size:17px;font-weight:700;display:flex;align-items:center;gap:8px}
.row1{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:8px}
input,select{font-size:15px;padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:#fff;color:var(--txt)}
#nume{min-width:150px}
#q{flex:1;min-width:160px}
.prog{font-size:13px;color:var(--mut);white-space:nowrap}
.bar{height:6px;background:#e6e8eb;border-radius:6px;overflow:hidden;flex:1;min-width:120px}
.bar>i{display:block;height:100%;background:var(--ok);width:0;transition:width .3s}
.hint{font-size:12.5px;color:var(--mut);margin-top:6px;line-height:1.4}
.wrap{max-width:900px;margin:0 auto;padding:12px}
.item{display:flex;gap:12px;align-items:center;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 12px;margin-bottom:10px}
.item.done{border-color:#bfe3c9;background:#f6fdf8}
.thumb{width:60px;height:60px;flex:none;border-radius:8px;object-fit:cover;background:#eceef1}
.meta{flex:1;min-width:0}
.tt{font-size:14px;font-weight:600;line-height:1.25;word-break:break-word}
.sku{font-family:ui-monospace,Menlo,monospace;font-size:12px;color:var(--mut);margin-top:2px}
.ctl{display:flex;flex-direction:column;align-items:flex-end;gap:5px;flex:none}
.pp{width:74px;text-align:center;font-size:17px;font-weight:700}
.chips{display:flex;gap:4px}
.chip{font-size:12px;padding:3px 7px;border:1px solid var(--line);border-radius:20px;background:#fafafa;cursor:pointer;color:var(--mut)}
.chip:hover{border-color:var(--acc);color:var(--acc)}
.st{font-size:11px;color:var(--mut);min-height:14px}
.st.ok{color:var(--ok)}
.filters{display:flex;gap:8px;align-items:center;margin:2px 0 12px}
label.tg{font-size:13px;color:var(--mut);display:flex;gap:5px;align-items:center;cursor:pointer}
.empty{text-align:center;color:var(--mut);padding:40px}
@media(max-width:520px){.thumb{width:48px;height:48px}.tt{font-size:13px}.pp{width:64px}}
</style></head><body>
<header>
  <div class="h1">📦 Colete pe produs — Depozit</div>
  <div class="row1">
    <input id="nume" placeholder="Numele tău (cine completează)">
    <input id="q" placeholder="Caută produs / SKU…">
    <div class="prog"><span id="cnt">0 / 0</span></div>
    <div class="bar"><i id="fill"></i></div>
  </div>
  <div class="hint">Scrie <b>câte BUCĂȚI din produs intră într-UN colet</b>. Ex: <b>1</b> = fiecare bucată în colet separat · <b>2</b> = 2 la colet · <b>20</b> = 20 la colet. Se salvează automat.</div>
</header>
<div class="wrap">
  <div class="filters">
    <label class="tg"><input type="checkbox" id="onlyempty"> arată doar necompletate</label>
  </div>
  <div id="list"></div>
  <div id="empty" class="empty" style="display:none">Nimic de afișat.</div>
</div>
<script>
const P="/colete", $=s=>document.querySelector(s);
let DATA=[], NUME=localStorage.getItem("colete_nume")||"";
$("#nume").value=NUME;
$("#nume").oninput=e=>{NUME=e.target.value.trim();localStorage.setItem("colete_nume",NUME)};
function prog(){const d=DATA.filter(x=>x.per_parcel).length;$("#cnt").textContent=d+" / "+DATA.length;$("#fill").style.width=(DATA.length?100*d/DATA.length:0)+"%"}
function render(){
  const q=($("#q").value||"").toLowerCase(), oe=$("#onlyempty").checked;
  const list=$("#list"); list.innerHTML="";
  let shown=0;
  for(const p of DATA){
    if(oe && p.per_parcel) continue;
    if(q && !((p.title||"").toLowerCase().includes(q) || (p.sku||"").toLowerCase().includes(q))) continue;
    shown++;
    const it=document.createElement("div"); it.className="item"+(p.per_parcel?" done":""); it.dataset.sku=p.sku;
    it.innerHTML=`<img class="thumb" loading="lazy" referrerpolicy="no-referrer" src="${p.img||""}">
      <div class="meta"><div class="tt">${esc(p.title)}</div><div class="sku">${esc(p.sku)}</div></div>
      <div class="ctl">
        <input class="pp" type="number" min="1" inputmode="numeric" placeholder="?" value="${p.per_parcel||""}">
        <div class="chips">${[1,2,5,10,20].map(n=>`<span class="chip" data-n="${n}">${n}</span>`).join("")}</div>
        <div class="st ${p.per_parcel?'ok':''}">${p.per_parcel?('✓ '+p.per_parcel+'/colet'+(p.by?' · '+esc(p.by):'')):''}</div>
      </div>`;
    const inp=it.querySelector(".pp"), st=it.querySelector(".st");
    inp.addEventListener("change",()=>save(p,inp.value,st,it));
    it.querySelectorAll(".chip").forEach(ch=>ch.onclick=()=>{inp.value=ch.dataset.n;save(p,inp.value,st,it)});
    list.appendChild(it);
  }
  $("#empty").style.display=shown?"none":"block";
  prog();
}
function esc(s){return (s||"").replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
let t;
async function save(p,val,st,it){
  st.textContent="… salvez"; st.className="st";
  try{
    const r=await fetch(P+"/api/save",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({sku:p.sku,per_parcel:val===""?null:val,by:NUME})});
    const j=await r.json();
    if(j.ok){
      p.per_parcel=j.per_parcel; p.by=NUME;
      st.className="st ok"; st.textContent=j.per_parcel?("✓ "+j.per_parcel+"/colet"+(NUME?" · "+NUME:"")):"— șters";
      it.classList.toggle("done",!!j.per_parcel);
      prog();
    } else { st.className="st"; st.textContent="⚠ "+(j.err||"eroare"); }
  }catch(e){ st.className="st"; st.textContent="⚠ fără net"; }
}
$("#q").oninput=render; $("#onlyempty").onchange=render;
fetch(P+"/api/products").then(r=>r.json()).then(d=>{DATA=d.products;render()});
</script></body></html>"""
