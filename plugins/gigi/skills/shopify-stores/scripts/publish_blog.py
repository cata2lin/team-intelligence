#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["python-docx"]
# ///
"""
publish_blog.py — turn a Word .docx (+ optional photo .zip) into a Shopify blog article
on any team store. Uploads images to the Shopify CDN, builds clean body HTML with inline
images per section, sets SEO meta + an ASCII handle. Defaults to DRAFT (review, then publish).

The .docx convention this expects (how Limitless/agencies deliver): Heading 1 = title,
Heading 2 = section headings, "normal" paragraphs = body, a paragraph starting with
"Poză…" right after an H2 = that section's image credit. The .zip holds the images,
named "<n>. <section name>_<alt text>.jpg" — image 1 = hero, the rest map to H2s by name.

Usage:
  uv run publish_blog.py --prefix GRAN --blog news --docx article.docx --zip photos.zip --tags "a,b" [--publish]
  (--zip optional → text-only; --publish → live, else DRAFT; --blog accepts a blog handle or numeric id)

SAFETY: without --publish the article is created UNPUBLISHED for review. Use --publish only
when the user explicitly wants it live.
"""
import argparse, base64, io, mimetypes, os, re, sys, time, unicodedata, urllib.request, zipfile
import docx
from shopify_gql import resolve_store
from shopify_theme import rest, API_VERSION

RO = {'ă': 'a', 'â': 'a', 'î': 'i', 'ș': 's', 'ş': 's', 'ț': 't', 'ţ': 't'}
STOP = set("si de la in cu pe un o pentru care ce pas ghid din".split())


def gql(shop, token, q, v=None):
    r = rest(shop, token, "POST", "graphql.json", {"query": q, "variables": v or {}})
    if r.get("errors"):
        raise SystemExit("GQL " + str(r["errors"])[:300])
    return r["data"]


def handleize(t):
    t = ''.join(RO.get(c, c) for c in t)
    t = unicodedata.normalize('NFKD', t).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]+', '-', t.lower()).strip('-')[:120]


def words(s):
    return {w for w in re.findall(r'[a-zăâîșţțş]+', s.lower()) if len(w) > 3 and w not in STOP}


def parse_docx(path):
    d = docx.Document(path)
    title, blocks = None, []
    for p in d.paragraphs:
        t = p.text.strip()
        if not t:
            continue
        st = p.style.name
        if st == "Heading 1" and title is None:
            title = t
        elif st == "Heading 2":
            blocks.append(("h2", t))
        elif re.match(r'^Poz[ăa]\b', t):
            blocks.append(("credit", t))
        else:
            esc = t.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            runs = [r for r in p.runs if r.text.strip()]
            if runs and runs[0].bold and ':' in esc[:60]:
                lbl, rest_ = esc.split(':', 1)
                blocks.append(("li", f"<strong>{lbl}:</strong>{rest_}"))
            else:
                blocks.append(("p", esc))
    return title, blocks


def load_images(zip_path):
    imgs = []
    z = zipfile.ZipFile(zip_path)
    for n in z.namelist():
        if not re.search(r'\.(jpg|jpeg|png|webp)$', n, re.I):
            continue
        base = os.path.basename(n)
        m = re.match(r'\s*(\d+)\.\s*(.*?)_([^_]+)\.(jpg|jpeg|png|webp)$', base, re.I)
        num = int(m.group(1)) if m else 999
        alt = re.sub(r'\.(jpg|jpeg|png|webp)$', '', base, flags=re.I).split('_')[-1].strip()
        sect = m.group(2) if m else base
        imgs.append({"num": num, "name": base, "data": z.read(n), "alt": alt,
                     "sect": sect, "mime": mimetypes.guess_type(base)[0] or "image/jpeg"})
    imgs.sort(key=lambda x: x["num"])
    return imgs


def upload_image(shop, token, img):
    safe = re.sub(r'[^A-Za-z0-9._-]', '_', img["name"])
    q = '''mutation($i:[StagedUploadInput!]!){ stagedUploadsCreate(input:$i){
      stagedTargets{url resourceUrl parameters{name value}} userErrors{message} } }'''
    tg = gql(shop, token, q, {"i": [{"filename": safe, "mimeType": img["mime"],
                                     "resource": "FILE", "httpMethod": "POST"}]})["stagedUploadsCreate"]["stagedTargets"][0]
    boundary = "----gd" + str(img["num"]) + str(len(img["data"]))
    body = io.BytesIO()
    w = lambda s: body.write(s.encode() if isinstance(s, str) else s)
    for p in tg["parameters"]:
        w(f'--{boundary}\r\nContent-Disposition: form-data; name="{p["name"]}"\r\n\r\n{p["value"]}\r\n')
    w(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{safe}"\r\n')
    w(f'Content-Type: {img["mime"]}\r\n\r\n'); w(img["data"]); w(f'\r\n--{boundary}--\r\n')
    urllib.request.urlopen(urllib.request.Request(
        tg["url"], data=body.getvalue(),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST")).read()
    fc = gql(shop, token, '''mutation($f:[FileCreateInput!]!){ fileCreate(files:$f){
      files{ id ... on MediaImage{ image{url} } } userErrors{message} } }''',
             {"f": [{"originalSource": tg["resourceUrl"], "contentType": "IMAGE", "alt": img["alt"]}]})
    fid = fc["fileCreate"]["files"][0]["id"]
    for _ in range(25):
        node = gql(shop, token, 'query($id:ID!){ node(id:$id){ ... on MediaImage{ image{url} } } }', {"id": fid})["node"]
        if node and node.get("image", {}).get("url"):
            return node["image"]["url"]
        time.sleep(1.5)
    return None


def resolve_blog_id(shop, token, blog):
    if str(blog).isdigit():
        return int(blog)
    for b in rest(shop, token, "GET", "blogs.json").get("blogs", []):
        if b["handle"] == blog:
            return b["id"]
    raise SystemExit(f"blog '{blog}' not found")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", required=True)
    ap.add_argument("--blog", required=True, help="blog handle (e.g. news) or numeric id")
    ap.add_argument("--docx", required=True)
    ap.add_argument("--zip", help="photo archive (optional)")
    ap.add_argument("--tags", default="")
    ap.add_argument("--publish", action="store_true", help="publish live (default: DRAFT)")
    ap.add_argument("--author", default="", help="article author (default: store default)")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    shop, token = resolve_store(a.prefix)
    blog_id = resolve_blog_id(shop, token, a.blog)

    title, blocks = parse_docx(a.docx)
    h2s = [b[1] for b in blocks if b[0] == 'h2']
    imgs = load_images(a.zip) if a.zip else []
    print(f"TITLE: {title}\n  blocks={len(blocks)} h2={len(h2s)} images={len(imgs)} -> blog {blog_id}")

    h2_img, used = {}, set()
    for img in imgs:
        if img["num"] == 1:
            continue
        iw = words(img["sect"]); best, sc = None, 0
        for h in h2s:
            if h in used:
                continue
            s = len(iw & words(h))
            if s > sc:
                sc, best = s, h
        if best and sc >= 1:
            h2_img[best] = img; used.add(best)
    top_img = next((i for i in imgs if i["num"] == 1), imgs[0] if imgs else None)
    if a.dry:
        print("featured:", top_img and top_img["name"])
        for h in h2s:
            print(f"  H2 {h[:40]!r} -> {h2_img.get(h, {}).get('name', '—')}")
        return

    cache = {}
    def url_for(img):
        if img["name"] not in cache:
            cache[img["name"]] = upload_image(shop, token, img)
        return cache[img["name"]]

    featured = url_for(top_img) if top_img else None
    html, list_buf = [], []
    if featured:
        html.append(f'<p><img src="{featured}" alt="{top_img["alt"]}" loading="lazy"></p>')

    def flush():
        if list_buf:
            html.append("<ul>" + "".join(f"<li>{x}</li>" for x in list_buf) + "</ul>"); list_buf.clear()

    for kind, val in blocks:
        if kind == "li":
            list_buf.append(val); continue
        flush()
        if kind == "h2":
            html.append(f"<h2>{val}</h2>")
            if val in h2_img:
                u = url_for(h2_img[val])
                if u:
                    html.append(f'<p><img src="{u}" alt="{h2_img[val]["alt"]}" loading="lazy"></p>')
        else:
            html.append(f"<p>{val}</p>")
    flush()

    desc = next((re.sub('<[^>]+>', '', v)[:155] for k, v in blocks if k == "p" and len(v) > 120), "")
    art = {"article": {"blog_id": blog_id, "title": title,
                       "body_html": "\n".join(html), "published": bool(a.publish), "tags": a.tags,
                       "handle": handleize(title),
                       "metafields": [
                           {"namespace": "global", "key": "title_tag", "type": "single_line_text_field", "value": title[:70]},
                           {"namespace": "global", "key": "description_tag", "type": "single_line_text_field", "value": desc}]}}
    if a.author:
        art["article"]["author"] = a.author
    if featured:
        art["article"]["image"] = {"src": featured, "alt": top_img["alt"]}
    r = rest(shop, token, "POST", f"blogs/{blog_id}/articles.json", art)["article"]
    state = "PUBLISHED" if a.publish else "DRAFT"
    print(f"{state}: {r.get('handle')} (id {r.get('id')})")
    print(f"  https://{shop.replace('.myshopify.com','')}… /blogs/{a.blog}/{r.get('handle')}")


if __name__ == "__main__":
    main()
