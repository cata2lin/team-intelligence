# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "psycopg2-binary>=2.9",
#   "requests>=2.31",
#   "pillow>=10",
#   "imagehash>=4.3",
#   "fastembed>=0.3",
#   "numpy>=1.24",
# ]
# ///
"""
grandia_pricematch.py — Grandia <-> arona-bi price-match (production).

Matches each ACTIVE Grandia product to competitor products scraped in the
arona-bi warehouse, using IMAGE matching so it works even when Grandia
re-hosts / re-shoots the photo:

  MATCH  = ( pHash Hamming distance <= --phash-max )        # free exact-photo boost
           OR
           ( CLIP cosine similarity >= --clip-min )         # semantic same-product recall
         THEN the ATTRIBUTE GATE must pass:
           - pack count  (set N / N buc / N placi)  agree if BOTH present
           - dimensions  (NxM cm)                    agree if BOTH present
           - wattage     (N W)                        agree if BOTH present

CLIP embeddings via fastembed (Qdrant/clip-ViT-B-32-vision, ONNX, CPU, no torch).

Candidate-gen: from the Grandia title take the most distinctive tokens, query
arona-bi products (image NOT NULL, ILIKE top-token AND (t2 OR t3)), LIMIT ~100,
joined to parsers (name) + latest price from mv_latest_price. Excludes stale
sources (price_seen_at older than --max-age-days) and the placeholder-stock
`atMag` parser. Wide net; image matching does the precision.

DRY (default): print matches, NO DB write.
--apply       : upsert into Grandia prc_competitor_products + prc_competitor_prices
                (idempotent per grandia<->arona pair, wrapped in a transaction).

Creds:  DATABASE_URL_GRANDIA  (full write)
        DATABASE_URL_ARONA_BI (read-only)
"""

# --- shared secret helper (env-first, KB fallback) via core/scripts/arona_pg.py ---
import os as _os, sys as _sys
from pathlib import Path as _Path
_here=_Path(__file__).resolve()
for _up in range(2,8):
    _c=_here.parents[_up]/"core"/"scripts"
    if (_c/"arona_pg.py").exists(): _sys.path.insert(0,str(_c)); break
try:
    import arona_pg as _apg
    _secret=_apg.secret
except Exception:
    _secret=lambda k: _os.environ[k]
# --- end helper ---
import os, re, sys, io, uuid, argparse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import psycopg2
import requests
import numpy as np
from PIL import Image
import imagehash

# Windows/depozit console safety (diacritics)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ----------------------------------------------------------------------------- DSN clean
_OK = {"host", "port", "dbname", "user", "password", "sslmode", "connect_timeout"}


def clean(d):
    """Strip non-libpq query params (e.g. pgbouncer=true, schema=...) that psycopg2 rejects."""
    p = urlsplit(d)
    if not p.query:
        return d
    kept = [(x, y) for x, y in parse_qsl(p.query, keep_blank_values=True) if x.lower() in _OK]
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(kept), p.fragment))


# ----------------------------------------------------------------------------- text helpers
STOP = set(
    "pentru cu si de la un una unui unei din pe ale ai sau prin mm cm buc bucati "
    "set piese negru alb gri auriu culoare model tip cadou premium pentru bucata "
    "placi plăci calitate produs".split()
)
UA = {"User-Agent": "Mozilla/5.0 (price-match/1.0)"}


_DIAC = str.maketrans("ăâîșțĂÂÎȘȚşţ", "aaistaaistst")


def _norm(t):
    return (t or "").lower().translate(_DIAC)


def tokens(t):
    """Most distinctive tokens (>=4 chars, not stopwords), longest first."""
    ws = [w for w in re.findall(r"[a-zA-ZăâîșțĂÂÎȘȚ0-9]{4,}", (t or "").lower()) if w not in STOP]
    ws = sorted(set(ws), key=len, reverse=True)
    return ws[:4]


def word_set(t):
    """All distinctive words (>=4 chars, diacritic-insensitive, no stopwords) for overlap tests."""
    return {w for w in re.findall(r"[a-z0-9]{4,}", _norm(t)) if w not in STOP}


def name_overlap(g_title, c_name):
    """Count of shared distinctive words between two titles (diacritic-insensitive)."""
    return len(word_set(g_title) & word_set(c_name))


def pack(t):
    """Pack count: 'set N', 'set de N', 'N buc/bucati/piese/placi'."""
    t = (t or "").lower()
    m = re.search(r"set\s*(?:de\s*)?(\d+)", t) or re.search(r"(\d+)\s*(?:buc|bucati|bucăți|piese|placi|plăci|set)\b", t)
    return int(m.group(1)) if m else None


def dims(t):
    """Dimensions like '120x80', '120 x 80 cm' -> frozenset of the two numbers (order-agnostic)."""
    t = (t or "").lower()
    m = re.search(r"(\d{1,4})\s*[x×]\s*(\d{1,4})", t)
    if not m:
        return None
    return frozenset({int(m.group(1)), int(m.group(2))})


def watt(t):
    """Wattage like '2000 W', '2000w'. Requires the W token to avoid matching random numbers."""
    t = (t or "").lower()
    m = re.search(r"(\d{2,5})\s*w\b", t)
    return int(m.group(1)) if m else None


def attr_gate(g_title, c_name):
    """Return True if all shared attributes agree (pack / dims / wattage)."""
    gp, cp = pack(g_title), pack(c_name)
    if gp and cp and gp != cp:
        return False
    gd, cd = dims(g_title), dims(c_name)
    if gd and cd and gd != cd:
        return False
    gw, cw = watt(g_title), watt(c_name)
    if gw and cw and gw != cw:
        return False
    return True


# ----------------------------------------------------------------------------- images
def fetch_image(url):
    try:
        r = requests.get(url, headers=UA, timeout=8)
        r.raise_for_status()
        return Image.open(io.BytesIO(r.content)).convert("RGB")
    except Exception:
        return None


def phash_of(img):
    try:
        return imagehash.phash(img)
    except Exception:
        return None


# ----------------------------------------------------------------------------- CLIP (fastembed, ONNX, CPU)
_CLIP = None


def clip_model():
    global _CLIP
    if _CLIP is None:
        from fastembed import ImageEmbedding  # lazy: model downloads ONNX (~350MB) once

        _CLIP = ImageEmbedding(model_name="Qdrant/clip-ViT-B-32-vision")
    return _CLIP


def clip_embed(images):
    """images: list of PIL.Image (already RGB). Returns list of L2-normalized 512-d vectors (or None per failed)."""
    model = clip_model()
    # fastembed accepts PIL images or paths; keep index alignment.
    valid_idx = [i for i, im in enumerate(images) if im is not None]
    out = [None] * len(images)
    if not valid_idx:
        return out
    try:
        embs = list(model.embed([images[i] for i in valid_idx]))
    except Exception as e:
        print(f"    [clip] embed failed: {e}", file=sys.stderr)
        return out
    for slot, vec in zip(valid_idx, embs):
        v = np.asarray(vec, dtype=np.float32)
        n = np.linalg.norm(v)
        out[slot] = v / n if n > 0 else v
    return out


def cosine(a, b):
    if a is None or b is None:
        return -1.0
    return float(np.dot(a, b))  # already normalized


# ----------------------------------------------------------------------------- DB read
def load_grandia_products(conn, limit):
    with conn.cursor() as c:
        q = '''SELECT p.id, p.title, p."featuredImageUrl", v.price, v."costPerItem"
               FROM "Product" p JOIN "Variant" v ON v."productId" = p.id
               WHERE p.status = 'ACTIVE'
                 AND p."featuredImageUrl" IS NOT NULL
                 AND v.price > 0
               ORDER BY v."inventoryQuantity" DESC NULLS LAST'''
        if limit:
            q += " LIMIT %s"
            c.execute(q, (limit,))
        else:
            c.execute(q)
        return c.fetchall()


ATMAG_PARSER = "atmag"  # placeholder-stock noise, exclude


def candidates(conn, toks, max_age_days):
    """Wide-net candidate gen: top-token ILIKE AND (t2 OR t3), fresh, non-atMag, LIMIT 100."""
    if not toks:
        return {}, []
    with conn.cursor() as bc:
        base = '''
            SELECT p.id, p.name, p.image, p.url, pa.name AS parser,
                   lp.price, lp.price_seen_at
            FROM products p
            JOIN parsers pa       ON pa.id = p.parser_id
            JOIN mv_latest_price lp ON lp.product_id = p.id
            WHERE p.image IS NOT NULL
              AND pa.name <> %s
              AND lp.price_seen_at >= (now() - %s::interval)
              AND p.name ILIKE %s
              {extra}
            LIMIT 100'''
        params = [ATMAG_PARSER, f"{max_age_days} days", f"%{toks[0]}%"]
        # secondary distinctiveness: (t2 OR t3) if we have them
        sec = toks[1:3]
        if sec:
            extra = "AND (" + " OR ".join(["p.name ILIKE %s"] * len(sec)) + ")"
            params += [f"%{t}%" for t in sec]
        else:
            extra = ""
        bc.execute(base.format(extra=extra), tuple(params))
        rows = bc.fetchall()
        # Fallback: if the tight query is too narrow, widen to top-token only.
        if len(rows) < 3:
            bc.execute(
                base.format(extra=""),
                (ATMAG_PARSER, f"{max_age_days} days", f"%{toks[0]}%"),
            )
            rows = bc.fetchall()
    # rows: (id, name, image, url, parser, price, price_seen_at)
    prices = {r[0]: (r[5], r[6]) for r in rows}
    cands = [(r[0], r[1], r[2], r[3], r[4]) for r in rows]
    return prices, cands


# ----------------------------------------------------------------------------- DB write (--apply)
def apply_matches(gconn, grandia_id, matched):
    """
    matched: list of dicts with keys arona_id, competitor_name, competitor_url,
             price, thumbnail_url. Idempotent per (grandia_id, arona_id):
             replaces the existing prc_competitor_products row + its latest price.
    Wrapped in a single transaction by the caller's connection (autocommit off).
    """
    now = datetime.utcnow()
    with gconn.cursor() as cur:
        for m in matched:
            note = f"aronabi:{m['arona_id']}"
            # find an existing row for this exact grandia<->arona pair (idempotency key = notes)
            cur.execute(
                """SELECT id FROM prc_competitor_products
                   WHERE product_id = %s AND notes = %s
                   LIMIT 1""",
                (grandia_id, note),
            )
            row = cur.fetchone()
            if row:
                cp_id = row[0]
                cur.execute(
                    """UPDATE prc_competitor_products
                       SET competitor_name = %s,
                           competitor_url  = %s,
                           thumbnail_url   = %s,
                           last_price      = %s,
                           last_scraped_at = %s,
                           scrape_status   = 'matched',
                           scrape_error    = NULL,
                           created_by      = 'arona-bi-matcher'
                       WHERE id = %s""",
                    (m["competitor_name"], m["competitor_url"], m.get("thumbnail_url"),
                     m["price"], now, cp_id),
                )
            else:
                cp_id = "bi" + uuid.uuid4().hex
                cur.execute(
                    """INSERT INTO prc_competitor_products
                       (id, product_id, competitor_name, competitor_url, thumbnail_url,
                        notes, scrape_status, last_scraped_at, last_price, created_at, created_by)
                       VALUES (%s, %s, %s, %s, %s, %s, 'matched', %s, %s, %s, 'arona-bi-matcher')""",
                    (cp_id, grandia_id, m["competitor_name"], m["competitor_url"],
                     m.get("thumbnail_url"), note, now, m["price"], now),
                )
            # record the price point (skip if missing)
            if m["price"] is not None:
                cur.execute(
                    """INSERT INTO prc_competitor_prices
                       (id, competitor_product_id, price, source, recorded_at)
                       VALUES (%s, %s, %s, 'arona-bi', %s)""",
                    (uuid.uuid4().hex, cp_id, m["price"], now),
                )


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="max Grandia active products (0 = all)")
    ap.add_argument("--phash-max", type=int, default=12, help="pHash Hamming distance threshold")
    ap.add_argument("--clip-min", type=float, default=0.925, help="CLIP cosine similarity threshold")
    ap.add_argument("--clip-min-overlap", type=int, default=2,
                    help="min shared distinctive words for a CLIP-only match (pHash matches bypass)")
    ap.add_argument("--max-age-days", type=int, default=14, help="exclude competitor prices older than this")
    ap.add_argument("--apply", action="store_true", help="WRITE matches to Grandia prc_* tables")
    ap.add_argument("--dry", action="store_true", help="print matches, no DB write (default)")
    ap.add_argument("--per-product", type=int, default=3, help="max competitor matches kept per Grandia product")
    args = ap.parse_args()
    apply = args.apply and not args.dry

    G = psycopg2.connect(clean(_secret("DATABASE_URL_GRANDIA")), connect_timeout=15)
    B = psycopg2.connect(clean(_secret("DATABASE_URL_ARONA_BI")), connect_timeout=15)
    B.set_session(readonly=True)
    if apply:
        G.autocommit = False  # transactional writes
    else:
        G.set_session(readonly=True)

    prods = load_grandia_products(G, args.limit)
    mode = "APPLY (writing prc_*)" if apply else "DRY (no DB write)"
    print(f"[{mode}] {len(prods)} Grandia products | pHash<={args.phash_max} OR CLIP>={args.clip_min} "
          f"| freshness<={args.max_age_days}d\n" + "=" * 96)

    n_with_match = 0
    total_matches = 0

    for pid, title, img_url, price, cost in prods:
        g_img = fetch_image(img_url)
        if g_img is None:
            print(f"\n[skip] {title[:64]} — Grandia image failed to load")
            continue
        g_ph = phash_of(g_img)
        toks = tokens(title)
        if not toks:
            print(f"\n[skip] {title[:64]} — no distinctive tokens")
            continue

        prices, cands = candidates(B, toks, args.max_age_days)
        if not cands:
            print(f"\n[   ] {title[:64]} | {float(price):.0f} lei | 0 cand")
            continue

        # download candidate images in parallel
        with ThreadPoolExecutor(max_workers=12) as ex:
            cand_imgs = list(ex.map(lambda cd: fetch_image(cd[2]), cands))

        # CLIP embed: Grandia + all candidate images in one batch (index-aligned)
        embs = clip_embed([g_img] + cand_imgs)
        g_emb, cand_embs = embs[0], embs[1:]

        matches = []
        for cd, cimg, cemb in zip(cands, cand_imgs, cand_embs):
            cid, cname, cimg_url, curl, parser = cd
            if cimg is None:
                continue
            c_ph = phash_of(cimg)
            d = (g_ph - c_ph) if (g_ph is not None and c_ph is not None) else 99
            cos = cosine(g_emb, cemb)

            is_phash = d <= args.phash_max
            is_clip = cos >= args.clip_min
            if not (is_phash or is_clip):
                continue
            # attribute gate (pack / dims / wattage)
            if not attr_gate(title, cname):
                continue
            # CLIP-only guard: commodity hard-goods on white look alike (a plain socket
            # scores ~0.92 vs an IP54 outdoor socket). A pure-CLIP match must also share
            # >= clip-min-overlap distinctive words with the Grandia title. pHash matches
            # (exact re-hosted photo) bypass this — the photo itself is the proof.
            if is_clip and not is_phash and name_overlap(title, cname) < args.clip_min_overlap:
                continue

            mtype = "pHash" if is_phash else "CLIP"
            # combined score for ranking: prefer exact photo, then high cosine
            rank = (0 if is_phash else 1, d if is_phash else 999, -cos)
            pr = prices.get(cid)
            matches.append({
                "rank": rank, "type": mtype, "dist": d, "cos": cos,
                "arona_id": cid, "competitor_name": cname, "competitor_url": curl,
                "thumbnail_url": cimg_url, "parser": parser,
                "price": (float(pr[0]) if pr and pr[0] is not None else None),
                "seen": (pr[1] if pr else None),
            })

        matches.sort(key=lambda m: m["rank"])
        # dedupe by parser+name (same product listed twice)
        seen_keys, deduped = set(), []
        for m in matches:
            k = (m["parser"], (m["competitor_name"] or "")[:40].lower())
            if k in seen_keys:
                continue
            seen_keys.add(k)
            deduped.append(m)
        kept = deduped[: args.per_product]

        if kept:
            n_with_match += 1
            total_matches += len(kept)

        print(f"\n[{'M' if kept else ' '}] {title[:64]} | {float(price):.0f} lei"
              f" (cost {cost}) | {len(cands)} cand -> {len(kept)} match")
        for m in kept:
            if m["price"] is not None:
                pp = f"{m['price']:.2f}"
                delta = f" | Δ {(float(price) / m['price'] - 1) * 100:+.0f}%"
                seen = f" ({m['seen'].date()})" if m["seen"] else ""
            else:
                pp, delta, seen = "no price", "", ""
            score = f"d={m['dist']:2}" if m["type"] == "pHash" else f"cos={m['cos']:.3f}"
            print(f"      [{m['type']:5} {score:>10}] [{m['parser'][:14]:14}]"
                  f" {pp:>10}{seen}{delta}  {m['competitor_name'][:46]}")

        if apply and kept:
            apply_matches(G, pid, kept)

    if apply:
        G.commit()
        print("\n" + "=" * 96)
        print(f"[APPLY] committed: {n_with_match} products, {total_matches} competitor rows upserted.")
    else:
        print("\n" + "=" * 96)
        recall = (n_with_match / len(prods) * 100) if prods else 0
        print(f"[DRY] {n_with_match}/{len(prods)} products got >=1 match "
              f"({recall:.0f}% recall) | {total_matches} total matches. No DB write.")

    G.close()
    B.close()


if __name__ == "__main__":
    main()
