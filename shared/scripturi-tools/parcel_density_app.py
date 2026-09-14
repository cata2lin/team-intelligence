"""Colete pe produs — pagina pt DEPOZIT: cate bucati intra intr-un colet, per produs.

Salveaza in SQLite (parcel_density.db), alimenteaza IMEDIAT map-ul central sku_box_map.json pe care cronul de
AWB (order_parcel_count) il foloseste, SI scrie metafield-ul `custom.nr_cutii` direct in Shopify, pe toate
magazinele unde exista SKU-ul (fara sa astepte cronul de 6:15). Arata si produsele care au valoarea pusa DEJA
in Shopify, marcate ca atare — pagina e tot tabloul, nu doar ce a completat depozitul.

Unitatea canonica (peste tot in pipeline) = `nr_cutii` = cate CUTII ocupa O BUCATA. Pagina vorbeste insa
limba depozitului: BUCATI/COLET (inversul). Pt produse voluminoase (o bucata = mai multe colete) exista
comutatorul de unitate. Servit sub /colete pe scripts.arona.ro."""
import os, sys, json, sqlite3, time
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import parcel_shop_index as SI

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


def _box(v):
    """Valoarea locala (parcel_density) — acolo 0 nu exista: pagina salveaza doar numere >= 1."""
    try:
        f = float(v)
        return f if f > 0 else None
    except Exception:
        return None


@app.get(PREFIX + "/api/products")
def api_products():
    prods = load_products()
    idx = SI.load_index()
    c = db()
    saved = {r["sku"]: dict(r) for r in c.execute("select * from parcel_density")}
    c.close()
    out = []
    for p in prods:
        sku = p.get("sku")
        s = saved.get(sku)
        shop = (idx.get(sku) or {}).get("box")
        local = _box(s["nr_cutii"]) if s else None
        by = ((s["updated_by"] if s else "") or "")
        if local is not None:
            src = "istoric" if by.startswith("istoric") else "depozit"
        else:
            src = "shopify" if shop is not None else None
        out.append({
            "sku": sku, "title": p.get("title") or "", "img": p.get("img") or "",
            "stores": p.get("stores") or [],
            "box": local if local is not None else shop,     # valoarea EFECTIVA (depozitul bate Shopify)
            "shop_box": shop,                                 # ce e acum in Shopify (pt drift)
            "src": src,
            "note": ((s["note"] if s else "") or ""),
            "by": by,
            "at": ((s["updated_at"] if s else "") or ""),
        })
    done = sum(1 for x in out if x["box"] is not None)
    human = sum(1 for x in out if x["src"] == "depozit")
    return {"products": out, "total": len(out), "done": done, "human": human}


@app.post(PREFIX + "/api/save")
async def api_save(req: Request):
    b = await req.json()
    sku = (b.get("sku") or "").strip()
    if not sku:
        return JSONResponse({"ok": False, "err": "sku lipsă"}, status_code=400)
    by = (b.get("by") or "").strip()[:40]
    note = (b.get("note") or "").strip()[:200]
    unit = (b.get("unit") or "pc").strip()          # pc = bucăți/colet · box = colete/bucată
    val = b.get("value", b.get("per_parcel"))       # `per_parcel` = numele vechi al câmpului
    now = time.strftime("%Y-%m-%d %H:%M")

    if val in (None, "", 0, "0"):
        box, pp = None, None
    else:
        try:
            val = float(val)
        except Exception:
            return JSONResponse({"ok": False, "err": "număr invalid"}, status_code=400)
        if val < 1:
            val = 1
        val = int(val)
        if unit == "box":                           # o bucată ocupă `val` colete (voluminos)
            box, pp = float(val), None
        else:                                       # `val` bucăți intră într-un colet
            box, pp = round(1.0 / val, 4), val

    c = db()
    if box is None:
        c.execute("delete from parcel_density where sku=?", (sku,))
    else:
        c.execute("""insert into parcel_density(sku,per_parcel,nr_cutii,note,updated_at,updated_by)
            values(?,?,?,?,?,?)
            on conflict(sku) do update set per_parcel=excluded.per_parcel, nr_cutii=excluded.nr_cutii,
              note=excluded.note, updated_at=excluded.updated_at, updated_by=excluded.updated_by""",
                  (sku, pp, box, note, now, by))
    c.commit(); c.close()

    # alimentează IMEDIAT map-ul central (order_parcel_count îl reîncarcă pe mtime)
    try:
        m = json.load(open(MAP, encoding="utf-8"))
    except Exception:
        m = {}
    if box is None:
        m.pop(sku, None)
    else:
        m[sku] = box
    tmp = MAP + ".tmp"
    json.dump(m, open(tmp, "w", encoding="utf-8"))
    os.replace(tmp, MAP)

    # scrie DIRECT în Shopify (custom.nr_cutii) pe toate magazinele unde există SKU-ul.
    # Metafield-ul de pe produs are prioritate în order_parcel_count față de map — deci și ȘTERGEREA
    # trebuie să ajungă acolo, altfel valoarea greșită ar continua să fie folosită la AWB.
    push = {"ok": [], "err": [], "stores": 0}
    try:
        push = SI.push_sku(sku, box)
    except Exception as e:
        push["err"].append(("shopify", str(e)[:150]))

    return {"ok": True, "sku": sku, "box": box, "per_parcel": pp, "unit": unit,
            "pushed": push["ok"], "push_err": [{"store": s, "msg": m_} for s, m_ in push["err"]]}


@app.get(PREFIX)
@app.get(PREFIX + "/")
def page():
    return HTMLResponse(PAGE)




PAGE = r"""<!doctype html><html lang="ro"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Colete pe produs — Depozit</title>
<style>
:root{--bg:#f4f5f7;--card:#fff;--line:#e6e8eb;--txt:#1e2229;--mut:#6b7280;--acc:#2563eb;--ok:#16a34a;--shop:#7c3aed;--warn:#b45309}
*{box-sizing:border-box}
body{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial;background:var(--bg);color:var(--txt);-webkit-text-size-adjust:100%}
header{position:sticky;top:0;z-index:10;background:#fff;border-bottom:1px solid var(--line);padding:8px 12px;box-shadow:0 1px 4px rgba(0,0,0,.04)}
.h1{font-size:16px;font-weight:700;display:flex;align-items:center;gap:8px}
.row1{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:7px}
/* 16px = pragul sub care iOS face zoom la focus pe input */
input,select,button{font-size:16px;padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:#fff;color:var(--txt);font-family:inherit}
#nume{min-width:130px;flex:1}
#q{flex:2;min-width:150px}
#eunume{display:none;align-items:center;gap:6px;font-size:13px;color:var(--mut);border:1px solid var(--line);border-radius:20px;padding:6px 10px;background:#fafafa;cursor:pointer;white-space:nowrap}
.prog{font-size:13px;color:var(--mut);white-space:nowrap}
.bar{height:6px;background:#e6e8eb;border-radius:6px;overflow:hidden;flex:1;min-width:110px;display:flex}
.bar>i{display:block;height:100%;background:var(--ok);width:0;transition:width .3s}
.bar>u{display:block;height:100%;background:#c4b5fd;width:0;transition:width .3s}
details.hint{margin-top:6px;font-size:12.5px;color:var(--mut);line-height:1.45}
details.hint>summary{cursor:pointer;list-style:none;color:var(--txt)}
details.hint>summary::-webkit-details-marker{display:none}
details.hint>summary::after{content:" ⌄";color:var(--mut)}
details.hint[open]>summary::after{content:" ⌃"}
details.hint>div{margin-top:5px}
.wrap{max-width:900px;margin:0 auto;padding:12px}
.item{display:flex;gap:12px;align-items:center;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 12px;margin-bottom:10px}
.item.done{border-color:#bfe3c9;background:#f6fdf8}
.item.shop{border-color:#ddd6fe;background:#faf8ff}
.thumb{width:60px;height:60px;flex:none;border-radius:8px;object-fit:cover;background:#eceef1}
.meta{flex:1;min-width:0}
.tt{font-size:14px;font-weight:600;line-height:1.3;word-break:break-word}
.sku{font-family:ui-monospace,Menlo,monospace;font-size:12px;color:var(--mut);margin-top:2px}
.shops{font-size:11px;color:var(--mut);margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ctl{display:flex;flex-direction:column;align-items:flex-end;gap:5px;flex:none}
.inp{display:flex;gap:4px;align-items:center}
.pp{width:76px;text-align:center;font-size:17px;font-weight:700;padding:8px 4px}
.un{font-size:12px;padding:7px 4px;color:var(--mut)}
.chips{display:flex;gap:4px}
.chip{font-size:12px;padding:3px 7px;border:1px solid var(--line);border-radius:20px;background:#fafafa;cursor:pointer;color:var(--mut);-webkit-tap-highlight-color:transparent;user-select:none}
.chip:active{background:#e8efff;border-color:var(--acc);color:var(--acc)}
.st{font-size:11.5px;color:var(--mut);min-height:14px;text-align:right;max-width:230px}
.st.ok{color:var(--ok)}
.st.shop{color:var(--shop)}
.st.warn{color:var(--warn)}
.filters{display:flex;gap:8px;align-items:center;margin:2px 0 12px;flex-wrap:wrap}
.filters select{font-size:14px;padding:7px 8px;flex:1;min-width:140px}
.legend{font-size:12px;color:var(--mut);margin-left:auto;white-space:nowrap}
.empty{text-align:center;color:var(--mut);padding:40px}
#more{text-align:center;color:var(--mut);font-size:13px;padding:14px}

/* ——— TELEFON: cardul se rupe pe trei rânduri, ca titlul să aibă lățime întreagă
       și butoanele să fie cât degetul (22px era imposibil de nimerit) ——— */
@media(max-width:600px){
  header{padding:7px 10px}
  .h1{font-size:15px}
  .wrap{padding:10px}
  .legend{display:none}
  .item{flex-wrap:wrap;gap:10px;padding:10px}
  .thumb{width:56px;height:56px}
  .meta{flex:1 1 0;min-width:0}
  .tt{font-size:14.5px}
  .ctl{flex:1 0 100%;flex-direction:row;flex-wrap:wrap;align-items:center;gap:8px;
       border-top:1px solid var(--line);padding-top:9px}
  .inp{order:1}
  .pp{width:84px;height:44px;font-size:19px}
  .un{height:44px;padding:0 6px}
  .st{order:2;flex:1;text-align:right;max-width:none;font-size:12px}
  .chips{order:3;flex:1 0 100%;gap:6px}
  .chip{flex:1;text-align:center;font-size:15px;padding:11px 0;border-radius:10px}
}
</style></head><body>
<header>
  <div class="h1">📦 Colete pe produs — Depozit</div>
  <div class="row1">
    <input id="nume" placeholder="Numele tău (cine completează)">
    <span id="eunume"></span>
    <input id="q" placeholder="Caută produs / SKU…">
  </div>
  <div class="row1">
    <div class="prog"><span id="cnt">0 / 0</span></div>
    <div class="bar"><i id="fill"></i><u id="fill2"></u></div>
  </div>
  <details class="hint">
    <summary>Scrie <b>câte BUCĂȚI intră într-UN colet</b>. Se salvează automat <b>și în Shopify</b>.</summary>
    <div><b>1</b> = fiecare bucată în colet separat · <b>2</b> = 2 la colet · <b>20</b> = 20 la colet.<br>
    Dacă produsul e voluminos și <b>o bucată ocupă mai multe colete</b>, schimbă unitatea în <b>colete/buc</b>.<br>
    🟢 verde = confirmat (depozit sau învățat din istoric) · 🟣 violet = valoare luată din Shopify, neconfirmată încă.</div>
  </details>
</header>
<div class="wrap">
  <div class="filters">
    <select id="fstore"><option value="">toate magazinele</option></select>
    <select id="fstat">
      <option value="">toate produsele</option>
      <option value="empty">doar necompletate</option>
      <option value="shop">doar din Shopify</option>
      <option value="hum">completate de depozit</option>
      <option value="istoric">învățate din istoric</option>
    </select>
    <span class="legend">🟢 confirmat (depozit / istoric) · 🟣 doar în Shopify</span>
  </div>
  <div id="list"></div>
  <div id="more" style="display:none">se încarcă…</div>
  <div id="empty" class="empty" style="display:none">Nimic de afișat.</div>
</div>
<script>
const P="/colete", $=s=>document.querySelector(s), BATCH=60;
let DATA=[], VIZ=[], SHOWN=0, NUME=localStorage.getItem("colete_nume")||"";
// nume: odată scris, se strânge într-un chip ca să nu mănânce un rând întreg pe telefon
function numeUI(){
  const inp=$("#nume"), chip=$("#eunume");
  if(NUME){ inp.style.display="none"; chip.style.display="inline-flex"; chip.textContent="✎ "+NUME; }
  else { inp.style.display=""; chip.style.display="none"; inp.value=""; }
}
$("#eunume").onclick=()=>{ const inp=$("#nume"); inp.style.display=""; $("#eunume").style.display="none"; inp.value=NUME; inp.focus(); };
$("#nume").oninput=e=>{NUME=e.target.value.trim();localStorage.setItem("colete_nume",NUME)};
$("#nume").onblur=numeUI;
numeUI();

// nr_cutii (cutii/bucată) -> ce vede depozitul. <=1 => bucăți/colet; >1 => colete/bucată (voluminos)
function disp(box){
  if(box==null || box<=0) return {v:"",u:"pc"};   // 0 = produsul nu-si cere colet propriu (nu se scrie din pagina)
  if(box<=1) return {v:Math.round(1/box),u:"pc"};
  return {v:Math.round(box),u:"box"};
}
function lbl(box){
  if(box===0) return "fără colet propriu (merge cu alt produs)";
  const d=disp(box); return d.u=="pc" ? d.v+"/colet" : d.v+" colete/buc";
}
function prog(){
  const h=DATA.filter(x=>x.src=="depozit"||x.src=="istoric").length, s=DATA.filter(x=>x.src=="shopify").length;
  $("#cnt").textContent=(h+s)+" / "+DATA.length+" · "+h+" confirmate";
  $("#fill").style.width=(DATA.length?100*h/DATA.length:0)+"%";
  $("#fill2").style.width=(DATA.length?100*s/DATA.length:0)+"%";
}
function stores(){
  const all=new Set(); DATA.forEach(p=>(p.stores||[]).forEach(s=>all.add(s)));
  const sel=$("#fstore");
  [...all].sort().forEach(s=>{const o=document.createElement("option");o.value=s;o.textContent=s;sel.appendChild(o)});
}
function status(p){
  if(p.src=="depozit") return ["ok","✓ "+lbl(p.box)+(p.by?" · "+p.by:"")];
  if(p.src=="istoric") return ["ok","✓ "+lbl(p.box)+" · învățat din istoric"];
  if(p.src=="shopify") return ["shop","din Shopify: "+lbl(p.box)];
  return ["",""];
}
function cls(p){ return "item"+(p.src=="depozit"||p.src=="istoric"?" done":(p.src=="shopify"?" shop":"")); }
function shops(a){ a=a||[]; return a.length>3 ? a.slice(0,3).join(", ")+" +"+(a.length-3) : a.join(", "); }
function esc(s){return (s||"").replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}

function row(p){
  const d=disp(p.box), s=status(p);
  const it=document.createElement("div");
  it.className=cls(p); it.dataset.sku=p.sku;
  it.innerHTML=`<img class="thumb" loading="lazy" decoding="async" referrerpolicy="no-referrer" src="${p.img||""}">
    <div class="meta"><div class="tt">${esc(p.title)}</div><div class="sku">${esc(p.sku)}</div>
      <div class="shops" title="${esc((p.stores||[]).join(", "))}">${esc(shops(p.stores))}</div></div>
    <div class="ctl">
      <div class="inp">
        <input class="pp" type="number" min="1" inputmode="numeric" enterkeyhint="done" placeholder="?" value="${d.v}">
        <select class="un"><option value="pc"${d.u=="pc"?" selected":""}>buc/colet</option><option value="box"${d.u=="box"?" selected":""}>colete/buc</option></select>
      </div>
      <div class="st ${s[0]}">${esc(s[1])}</div>
      <div class="chips">${[1,2,5,10,20].map(n=>`<span class="chip" data-n="${n}">${n}</span>`).join("")}</div>
    </div>`;
  const inp=it.querySelector(".pp"), un=it.querySelector(".un"), stx=it.querySelector(".st");
  inp.addEventListener("change",()=>save(p,inp.value,un.value,stx,it));
  un.addEventListener("change",()=>{ if(inp.value!=="") save(p,inp.value,un.value,stx,it) });
  it.querySelectorAll(".chip").forEach(ch=>ch.onclick=()=>{inp.value=ch.dataset.n;un.value="pc";save(p,inp.value,"pc",stx,it)});
  return it;
}
// randare incrementala: 1024 de carduri deodata inghetau telefonul la fiecare filtrare
function paint(){
  const n=Math.min(SHOWN+BATCH, VIZ.length), frag=document.createDocumentFragment();
  for(let i=SHOWN;i<n;i++) frag.appendChild(row(VIZ[i]));
  $("#list").appendChild(frag); SHOWN=n;
  $("#more").style.display = SHOWN<VIZ.length ? "block" : "none";
  $("#more").textContent = "încă "+(VIZ.length-SHOWN)+" produse…";
}
function render(){
  const q=($("#q").value||"").toLowerCase().trim(), fs=$("#fstore").value, st=$("#fstat").value;
  VIZ=DATA.filter(p=>{
    if(st=="empty" && p.box!=null) return false;
    if(st=="shop" && p.src!="shopify") return false;
    if(st=="hum" && p.src!="depozit") return false;
    if(st=="istoric" && p.src!="istoric") return false;
    if(fs && !(p.stores||[]).includes(fs)) return false;
    if(q && !((p.title||"").toLowerCase().includes(q) || (p.sku||"").toLowerCase().includes(q))) return false;
    return true;
  });
  SHOWN=0; $("#list").innerHTML="";
  $("#empty").style.display=VIZ.length?"none":"block";
  paint(); prog();
}
new IntersectionObserver(es=>{ if(es[0].isIntersecting && SHOWN<VIZ.length) paint(); },
  {rootMargin:"600px"}).observe($("#more"));

async function save(p,val,unit,st,it){
  st.textContent="… salvez"; st.className="st";
  try{
    const r=await fetch(P+"/api/save",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({sku:p.sku,value:val===""?null:val,unit:unit,by:NUME})});
    const j=await r.json();
    if(j.ok){
      p.box=j.box; p.by=NUME; p.src=(j.box==null?(p.shop_box!=null?"shopify":null):"depozit");
      if(j.box!=null) p.shop_box=j.box;
      const pushed=(j.pushed||[]), errs=(j.push_err||[]);
      let txt = j.box==null ? "— șters" : ("✓ "+lbl(j.box)+(NUME?" · "+NUME:""));
      if(pushed.length) txt += " → Shopify: "+pushed.join(", ");
      else if(!errs.length) txt += " · SKU negăsit în magazine";
      st.className="st "+(errs.length?"warn":"ok");
      if(errs.length) txt += " ⚠ "+errs.map(e=>e.store).join(", ");
      st.textContent=txt;
      it.className=cls(p);
      prog();
    } else { st.className="st warn"; st.textContent="⚠ "+(j.err||"eroare"); }
  }catch(e){ st.className="st warn"; st.textContent="⚠ fără net"; }
}
let tq; $("#q").oninput=()=>{clearTimeout(tq);tq=setTimeout(render,180)};
$("#fstat").onchange=render; $("#fstore").onchange=render;
fetch(P+"/api/products").then(r=>r.json()).then(d=>{DATA=d.products;stores();render()});
</script></body></html>"""
