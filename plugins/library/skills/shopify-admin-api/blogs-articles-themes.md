---
name: blogs-articles-themes
description: Blog/Article CRUD quirks (REST vs GraphQL gaps), theme JSON template editing (image_picker / shopify://shop_images), and the AI-image-generation pattern used to fill in product/article imagery. Carved out of "Things deliberately NOT covered" after a real GT (george-talent.ro) blog-image cleanup session.
---

# Blogs, Articles & Theme Templates

This file exists because **"Themes & Online Store" was listed as out of scope**
for this skill, but a real task (re-imaging all 15 GT blog articles, fixing a
sitewide placeholder banner, fixing a `25%` → `20%` copy typo) needed exactly
this surface. Promote anything else you learn here into its own section
rather than leaving it tribal knowledge in a chat transcript.

## 1. Article image: GraphQL has no write field — use REST

`Article` (GraphQL) exposes `image: Image` as **read-only**. There is no
`image` input on `ArticleUpdateInput` in 2026-04. To set/replace an
article's featured image, use the **legacy REST endpoint** with a base64
`attachment` — it still works fine and is the only documented write path:

```python
PUT https://{shop}/admin/api/{version}/blogs/{blog_id}/articles/{article_id}.json
{
  "article": {
    "id": <article_id>,
    "image": {"attachment": "<base64 bytes, no data: prefix>"}
  }
}
```

- `blog_id` / `article_id` are the **numeric** ids — strip them off the GID
  (`gid://shopify/Article/123` → `123`).
- Sending only `id` + `image` in the payload leaves title/body/tags/etc.
  untouched — REST article updates are partial, not full-resource-replace.
- Get the blog/article ids via a normal GraphQL read first:
  `{ blogs(first: N) { edges { node { id articles(first: N) { edges { node { id handle title } } } } } } }`.

## 2. Article body vs. summary — two separate HTML fields

`Article` has **both** `body: HTML` and `summary: HTML` (not `excerpt` —
that name doesn't exist; `summary` is correct in 2026-04, confirmed via
`__type(name:"Article"){fields{name}}` introspection). The blog **index/
listing page** renders `summary`; the **article detail page** renders
`body`. They are independent strings — fixing a typo in `body` does
**not** fix the same typo in `summary`. If you're doing a sitewide
find/replace on article copy, check and patch both fields separately:

```python
# REST partial update, only the field(s) that actually contain the match
requests.put(f".../articles/{id}.json", json={"article": {
    "id": id,
    **({"body_html": new_body} if changed_body else {}),
    **({"summary_html": new_summary} if changed_summary else {}),
}})
```

`tags` on `Article` is also list-replacement (send the full desired list/
comma-string), same rule as product tags in [`products-variants.md`](products-variants.md) §11.

## 3. Theme JSON templates: the generic `image` block does NOT bind to `article.image`

This is the gotcha that cost the most debugging time. A `templates/
article.json` section can place a block of type **`image`** (Dawn-style
generic block, `blocks/image.liquid`) to show the "featured image." That
block reads `block.settings.image` — a **static** image chosen once in
the Theme Customizer — and falls back to Shopify's generic gradient
`<placeholder-image>` graphic if nothing was ever picked. **It does not
read the current article's `article.image` at render time.** Symptom:
the exact same placeholder graphic appears on every single blog article,
and updating `article.image` via the REST call in §1 has *zero* visible
effect in that block's position.

Two fixes, not equivalent:

- **Static, same image on every article (quick):** set
  `block.settings.image` once — either by hand in Theme Customizer, or
  by writing the asset directly (see §4). Fine for a sitewide banner.
- **Dynamic, per-article image (correct):** some themes ship an unused
  dedicated block built for this (e.g. `blocks/_blog-post-image.liquid`,
  which takes `image` as a doc param and is clearly meant to render the
  article's own photo). If the template wires the generic `image` block
  instead of that dedicated one, swap the block `"type"` in the template
  JSON. Always check what blocks already exist in the theme
  (`themes/{id}/assets.json` listing, filter for `blog`/`article`) before
  assuming you need to write new Liquid.

## 4. Setting a theme `image_picker` value programmatically

The Theme Customizer's image picker stores a reference string, not a URL:

```
"image": "shopify://shop_images/<filename>"
```

To set this from a script (no customizer click):

1. `stagedUploadsCreate(input: [{filename, mimeType, httpMethod, resource: IMAGE}])`
   → upload the bytes to the returned `url` with its `parameters` (see
   [`platform.md`](platform.md) staged-upload steps 1–2).
2. `fileCreate(files: [{originalSource: <resourceUrl>, contentType: IMAGE}])`
   → poll `node(id){ ... on MediaImage { fileStatus image { url } } }`
   until `fileStatus: READY`.
3. Take the filename portion of the final CDN `image.url` (the part after
   the last `/`, before the `?v=` query string) and build
   `shopify://shop_images/<that filename>`.
4. Read the current `templates/article.json` (or whichever template) via
   `GET themes/{id}/assets.json?asset[key]=templates/article.json`,
   `json.loads` it, set **only** the one block's `settings.image` key,
   `json.dumps` it back, and `PUT` the whole asset:
   `PUT themes/{id}/assets.json {"asset": {"key": "...", "value": <json string>}}`.

Patch the parsed dict surgically (one key) rather than hand-building the
JSON string — every other section/block setting in that template comes
back byte-identical, which matters when several blog posts/teammates
share the same template file.

## 5. AI image generation for product/article imagery

No Shopify endpoint generates images, but the KB has both `OPENAI_API_KEY`
and `GEMINI_API_KEY` (`google-ai` service) available for any script that
needs to fill in a featured image. Real-world finding from the GT session:

- **OpenAI `gpt-image-1`** (`/v1/images/generations`, `/v1/images/edits`)
  produces good composition but **garbles small label text** on a
  product bottle when used as an image-edit reference (e.g. "Eau de
  Parfum" → "Ene de Parfun"). Acceptable for abstract/background imagery,
  risky for anything with brand text that must stay readable.
- **Gemini `gemini-2.5-flash-image`**
  (`generativelanguage.googleapis.com/v1beta/models/{model}:generateContent`,
  `inline_data` parts for reference image(s) + a text part for the edit
  instruction, `generationConfig.responseModalities: ["IMAGE"]`) was
  **much more faithful to reference-image text/label content** in the
  same task — preferred default for "keep this exact product, change the
  scene around it" edits.
- Gemini also accepts `generationConfig.imageConfig.aspectRatio: "16:9"`
  (etc.) to force a specific aspect ratio directly, instead of hoping the
  text prompt's "16:9 aspect ratio" phrase is honored (it often isn't —
  a prompt-only request came back portrait 864×1184 once).
- Multiple `inline_data` parts can be sent in one request (e.g. one
  reference image per product variant/gender) and the prompt can refer to
  "the first/second reference image" to combine them in one generated
  scene.
- Counting requests in the prompt (e.g. "exactly 3 black + 3 green
  bottles") is unreliable on the first attempt — expect 1–2 retries
  before the count/labels/composition all land. Iterate with small,
  explicit, single-change prompt edits rather than rewriting the whole
  prompt each time.

## Sources

Learned hands-on during a GT (george-talent.ro) blog-content session,
2026-06-17/18 — not pulled from shopify.dev docs like the rest of this
skill. Re-verify the GraphQL field names (`summary`, lack of `image` on
`ArticleUpdateInput`) against a fresh introspection if working on a
different API version.
