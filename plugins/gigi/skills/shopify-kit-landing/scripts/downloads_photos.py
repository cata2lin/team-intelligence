# /// script
# requires-python = ">=3.10"
# dependencies = ["requests"]
# ///
"""Match in-stock colors to their REAL nail photos in ~/Downloads by SKU code (after R203-), upload to Shopify, patch colors.json."""
import os, re, glob, json, csv, subprocess, time, requests, pathlib
from concurrent.futures import ThreadPoolExecutor
HERE=pathlib.Path(__file__).parent; HOME=os.path.expanduser("~")

def kb(k): return subprocess.run(["uv","run","/Users/gheorghebeschea/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py","secret-get",k],capture_output=True,text=True).stdout
shop=tok=None
for r in csv.reader(kb("SHOPIFY_STORES_CSV").splitlines()):
    if r and r[0]=="ROSSI": shop,tok=r[1],r[2]
H={"X-Shopify-Access-Token":tok,"Content-Type":"application/json"}
GQL=f"https://{shop}/admin/api/2026-01/graphql.json"
def gql(q,v=None): return requests.post(GQL,headers=H,json={"query":q,"variables":v or {}},timeout=40).json()

# variant_id -> sku
Q='''query($c:String){ products(first:120, after:$c, query:"product_type:'Pudra de unghii kit'"){ pageInfo{hasNextPage endCursor} nodes{ variants(first:2){nodes{ id sku }}} } }'''
v2sku={}; c=None
while True:
    d=gql(Q,{"c":c}); pr=d.get("data",{}).get("products")
    if not pr: break
    for n in pr["nodes"]:
        for vv in n["variants"]["nodes"]: v2sku[vv["id"].split("/")[-1]]=vv.get("sku") or ""
    if not pr["pageInfo"]["hasNextPage"]: break
    c=pr["pageInfo"]["endCursor"]; time.sleep(0.2)

# index Downloads images (prefer 'Swatch-uri dip mici')
files=[]
for base in ["Downloads/Swatch-uri dip mici","Downloads"]:
    for ext in ("png","jpg","jpeg","PNG","JPG"):
        files+=glob.glob(f"{HOME}/{base}/**/*.{ext}",recursive=True)
files=list(dict.fromkeys(files))
print("SKU:",len(v2sku)," fișiere:",len(files))

def code_of(sku):
    m=re.search(r'R203-(.+)$', sku or '')
    return m.group(1).strip() if m else None
def norm(s): return re.sub(r'[^a-z0-9]','', (s or '').lower())
def find_photo(code, name):
    nn=norm(name)
    cands=[]
    for f in files:
        b=os.path.basename(f); nb=norm(b)
        by_code = code and re.match(r'^'+re.escape(code)+r'(?![A-Za-z0-9])', b, re.I)
        by_name = len(nn)>=5 and nn in nb          # numele concatenat, ex 'feelincozy'
        if by_code or by_name: cands.append((f,nb,by_code,by_name))
    if not cands: return None
    def score(t):
        f,nb,bc,bn=t
        in_swatch = 'swatch-uri dip' in f.lower()
        # prefer poza-unghie '2' după nume/cod
        tail=nb.split(nn)[-1] if nn and nn in nb else nb
        has2 = bool(re.match(r'2(\D|$)', tail)) or nb.endswith('2') or '2' in tail[:2]
        return (0 if in_swatch else 1, 0 if (bc or bn) else 2, 0 if has2 else 1, len(os.path.basename(f)))
    return sorted(cands, key=score)[0][0]

cols=[x for x in json.load(open(HERE/'colors.json')) if x.get('avail')]
todo={}; miss=[]
for col in cols:
    code=code_of(v2sku.get(col['id'],''))
    f=find_photo(code, col['name'])
    if f: todo[col['name']]=f
    else: miss.append((col['name'],code or '?'))
print(f"POTRIVITE: {len(todo)}/{len(cols)} | lipsă: {len(miss)} {[m[0] for m in miss]}")
import sys
if "--dry" in sys.argv:
    for k,v in list(todo.items()): print(f"   {k:22} -> {os.path.basename(v)}")
    sys.exit(0)

# upload to Shopify Files (parallel)
def upload(name, path):
    ext=os.path.splitext(path)[1].lstrip('.').lower() or 'png'
    mime='image/jpeg' if ext in('jpg','jpeg') else 'image/png'
    fn=re.sub(r'[^a-z0-9]+','-',name.lower()).strip('-')+'-nail.'+ext
    st=gql("""mutation($i:[StagedUploadInput!]!){stagedUploadsCreate(input:$i){stagedTargets{url resourceUrl parameters{name value}}}}""",
        {"i":[{"filename":fn,"mimeType":mime,"resource":"FILE","httpMethod":"POST"}]})
    tgt=st["data"]["stagedUploadsCreate"]["stagedTargets"][0]
    form=[(p["name"],p["value"]) for p in tgt["parameters"]]
    with open(path,'rb') as fh: data=fh.read()
    r=requests.post(tgt["url"],data=form,files={"file":(fn,data,mime)},timeout=120)
    if r.status_code not in(200,201,204): return None
    cf=gql("""mutation($f:[FileCreateInput!]!){fileCreate(files:$f){files{... on MediaImage{id}} userErrors{message}}}""",
        {"f":[{"originalSource":tgt["resourceUrl"],"contentType":"IMAGE"}]})
    if cf["data"]["fileCreate"]["userErrors"]: return None
    fid=cf["data"]["fileCreate"]["files"][0]["id"]
    for _ in range(20):
        u=(((gql('{node(id:"%s"){... on MediaImage{image{url}}}}'%fid).get("data") or {}).get("node") or {}).get("image") or {}).get("url")
        if u: return u
        time.sleep(2)
    return None

results={}
def work(item):
    name,path=item
    try: return name, upload(name,path)
    except Exception as e: return name, None
with ThreadPoolExecutor(max_workers=6) as ex:
    for name,url in ex.map(work, todo.items()):
        results[name]=url
        print(("  ✓ " if url else "  ✗ ")+name)

# patch colors.json (ALL entries, in-stock matched)
allc=json.load(open(HERE/'colors.json'))
patched=0
for col in allc:
    u=results.get(col['name'])
    if u: col['img']=u+"&width=150"; col['nail']=True; patched+=1
json.dump(allc,open(HERE/'colors.json','w'),ensure_ascii=False,indent=0)
print(f"\ncolors.json patched cu poze reale din Downloads: {patched}")
