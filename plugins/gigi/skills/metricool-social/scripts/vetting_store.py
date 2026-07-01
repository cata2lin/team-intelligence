# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9"]
# ///
"""Content library / vetting cache in the KB 'files' table (team-visible).
Key = path (Drive id or blob url). description = JSON vetting. status=ok/rejected/posted."""
import os, json, sys, psycopg2
URL=os.environ["KB_DATABASE_URL"]
def conn(): return psycopg2.connect(URL, connect_timeout=15)
def cached(ref):
    with conn() as c, c.cursor() as cur:
        cur.execute("SELECT description FROM files WHERE location='drive' AND category='reel' AND path=%s",(ref,))
        r=cur.fetchone()
        return json.loads(r[0]) if r and r[0] else None
def save(ref, brand, name, dur, analysis, blob_url, status):
    payload=dict(analysis or {}); payload.update({"blob_url":blob_url})
    with conn() as c, c.cursor() as cur:
        cur.execute("SELECT id FROM files WHERE location='drive' AND category='reel' AND path=%s",(ref,))
        row=cur.fetchone()
        if row:
            cur.execute("UPDATE files SET description=%s,status=%s,updated_at=now() WHERE id=%s",
                        (json.dumps(payload,ensure_ascii=False),status,row[0]))
        else:
            cur.execute("""INSERT INTO files(location,path,name,category,source,status,size_bytes,description,created_at,updated_at)
                           VALUES('drive',%s,%s,'reel',%s,%s,%s,%s,now(),now())""",
                        (ref,name,brand,status,int((dur or 0)*1000),json.dumps(payload,ensure_ascii=False)))
def backfill_queue(qpath):
    q=json.load(open(qpath)); n=0
    for brand,reels in q["brands"].items():
        for r in reels:
            ref=r.get("src") or r["url"]
            an={"caption":r.get("caption"),"dur":r.get("dur"),"note":"backfill din coada (fara analiza Gemini completa)"}
            save(ref,brand,r.get("src") or "",r.get("dur"),an,r["url"],"posted" if r.get("posted") else "queued")
            n+=1
    return n
if __name__=="__main__":
    if sys.argv[1:]==["backfill"]:
        n=backfill_queue(os.path.join(os.path.dirname(os.path.abspath(__file__)),"queue.json"))
        with conn() as c, c.cursor() as cur:
            cur.execute("SELECT count(*),count(*) FILTER(WHERE status='posted') FROM files WHERE category='reel'")
            tot,posted=cur.fetchone()
        print(f"backfill: {n} din coada; content_reels in DB={tot} (posted={posted})")
