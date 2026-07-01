# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9"]
# ///
"""Content library / vetting cache in the KB 'files' table (team-visible).
Key = path = Drive file id (stable). description = JSON Gemini analysis. status = ok/rejected/posted.
Saves EVERYTHING scanned (kept + rejected) so we never re-vet and keep a full understanding library."""
import os, json, sys, subprocess, psycopg2

_URL=None
def _url():
    global _URL
    if _URL: return _URL
    _URL=os.environ.get("KB_DATABASE_URL") or subprocess.run(
        ["/bin/zsh","-lc","echo $KB_DATABASE_URL"],capture_output=True,text=True).stdout.strip()
    return _URL
def conn(): return psycopg2.connect(_url(), connect_timeout=15)

def cached(ref):
    """Return the stored Gemini analysis dict for a Drive id, or None if not yet vetted."""
    if not ref: return None
    try:
        with conn() as c, c.cursor() as cur:
            cur.execute("SELECT description FROM files WHERE location='drive' AND category='reel' AND path=%s",(str(ref),))
            r=cur.fetchone()
            return json.loads(r[0]) if r and r[0] else None
    except Exception as e:
        print(f"   [vetting_store] cache read err: {str(e)[:80]}",flush=True); return None

def save(ref, brand, name, dur, analysis, blob_url, status):
    """Upsert a scanned clip's full analysis, keyed by Drive id."""
    if not ref: return
    payload=dict(analysis or {}); payload.update({"blob_url":blob_url,"name":name,"brand":brand})
    try:
        with conn() as c, c.cursor() as cur:
            cur.execute("SELECT id FROM files WHERE location='drive' AND category='reel' AND path=%s",(str(ref),))
            row=cur.fetchone()
            if row:
                cur.execute("UPDATE files SET name=%s,source=%s,status=%s,size_bytes=%s,description=%s,updated_at=now() WHERE id=%s",
                            (name,brand,status,int((dur or 0)*1000),json.dumps(payload,ensure_ascii=False),row[0]))
            else:
                cur.execute("""INSERT INTO files(location,path,name,category,source,status,size_bytes,description,created_at,updated_at)
                               VALUES('drive',%s,%s,'reel',%s,%s,%s,%s,now(),now())""",
                            (str(ref),name,brand,status,int((dur or 0)*1000),json.dumps(payload,ensure_ascii=False)))
    except Exception as e:
        print(f"   [vetting_store] save err: {str(e)[:80]}",flush=True)

def stats():
    with conn() as c, c.cursor() as cur:
        cur.execute("SELECT status,count(*) FROM files WHERE category='reel' GROUP BY status ORDER BY 2 DESC")
        return cur.fetchall()

def backfill_queue(qpath):
    q=json.load(open(qpath)); n=0
    for brand,reels in q["brands"].items():
        for r in reels:
            ref=r.get("src") or r["url"]
            an={"caption":r.get("caption"),"dur":r.get("dur"),"note":"backfill din coada"}
            save(ref,brand,r.get("src") or "",r.get("dur"),an,r["url"],"posted" if r.get("posted") else "queued")
            n+=1
    return n

if __name__=="__main__":
    if sys.argv[1:2]==["backfill"]:
        n=backfill_queue(os.path.join(os.path.dirname(os.path.abspath(__file__)),"queue.json"))
        print(f"backfill: {n}")
    print("content_reels in DB per status:", stats())
