# BLOG PLAYBOOK — ARONA perfume stores (GT / Esteban / Nubra)

Operational guide shared by the `gt-articles`, `esteban-articles`,
`nubra-articles` skills. Brand **voice** lives in `gt.md` / `esteban.md` /
`nubra.md`; this file is the **process** (pipeline, SEO, footer, lessons).
All scripts use the **ARONA Assistant** app (`kb_env` loads `SHOPIFY_ARONA_*`).

## Store facts

| store | public | myshopify | Blog GID | blog handle | Files CDN folder | footer menu (rendered) |
|---|---|---|---|---|---|---|
| gt | george-talent.ro | `ix5bxc-hr` | `gid://shopify/Blog/116880474435` | `blog` | `1/0939/7370/9123` | `footer` |
| esteban | esteban.ro | `6f9e22-9d` | `gid://shopify/Blog/110902477145` | `blog` | `1/0881/7275/7337` | `footer` |
| nubra | nubra.ro | `bmuwvv-jy` | `gid://shopify/Blog/102386696425` | `blog` | `1/0795/7105/8921` | `footer-menu` |

> Blog handle was renamed `news → blog` (10 Jun 2026) with 301 redirects.
> Nubra's literal `footer` menu ("Relatii Clienti") is **not** rendered — the
> visible "SUPORT" column is the `footer-menu` menu. Always check the live
> footer before assuming which menu a theme renders.

## End-to-end pipeline (everything lives in `blog-rollout/`)

```bash
P=team-intelligence/plugins/core/scripts        # publish/seo/footer scripts
B=blog-rollout                                   # data + workflows

# 1. RECON — pull blogs/products/images per store (re-run when stock/catalog changes)
uv run "$P/_blog_recon.py"                       # -> $B/recon/<store>.json

# 2. GROUND — build product index + compact in-stock catalog
uv run "$B/build_index.py"                       # -> $B/index/index_<store>.json
uv run "$B/build_catalog.py"                     # -> $B/catalog/<store>.md

# 3. WRITE — workflow: per article, write (brand voice) + adversarial verify
#    (real in-stock handles, correct inspiration pairing, no banned words)
Workflow scriptPath=$B/articles_workflow.js      # -> result -> process_results.py
uv run "$B/process_results.py"                   # normalize HTML, resolve hero img -> $B/articles/<store>.json

# 4. PUBLISH — validate then create (dry-run -> draft -> live)
uv run "$P/blog_publish_articles.py" --store <s> --dry-run
uv run "$P/blog_publish_articles.py" --store <s>            # PUBLISH LIVE (confirm first)

# 5. SEO — generate meta, then set title_tag/description_tag on blog + articles
Workflow scriptPath=$B/seo_workflow.js           # -> $B/seo/seo.json (via process_seo.py)
uv run "$B/process_seo.py"
uv run "$P/blog_seo_and_handle.py" --store <s> --dry-run
uv run "$P/blog_seo_and_handle.py" --store <s>             # set SEO (add --new-handle blog to rename)

# 6. FOOTER — add a resource-linked 'Blog' link to the footer menu (idempotent)
uv run "$P/blog_add_to_footer.py" --store <s> --dry-run
uv run "$P/blog_add_to_footer.py" --store <s>
```

## SEO — how it actually works

- Article/Blog have **no native `seo` input field**. SEO `<title>` and
  `<meta name="description">` are set via metafields **`global.title_tag`** and
  **`global.description_tag`** (`single_line_text_field`), via `metafieldsSet`.
  Verified: the theme renders these into the page head.
- The article **`summary`** field is the blog *excerpt*, **not** the SEO meta —
  they are separate. Set both.
- Limits enforced by `process_seo.py`: **title ≤ 60 chars, description ≤ 160**
  (aim 145–158). It also `html.unescape`s (`&amp;`→`&`) before storing.
- Good article SEO title = lead with the strongest query term (the famous
  original / "alternativa" / gender / season), optionally ` | <brand>`. Desc =
  hook + concrete benefit (persistenta/pret/profil) + soft CTA ("Descopera").

## Blog handle rename

- `blogUpdate(id, blog: { handle, redirectArticles: true, redirectNewHandle: true })`.
  **Both** redirect flags are required — `redirectArticles` alone fails with
  *"Blog posts cannot be redirected automatically without redirecting the blog."*
- `blog_seo_and_handle.py --new-handle blog` does rename + SEO in one run.

## Footer menu

- `menuUpdate(id, title, handle, items)` is a **full replace** — re-send every
  existing item *with its `id`* (and `resourceId` for resource items), then
  append the new one. `blog_add_to_footer.py` does this and is **idempotent**
  (skips if a BLOG item already exists).
- The 'Blog' item is **type `BLOG` with `resourceId` = the blog GID** (not a
  hardcoded URL), so it auto-follows handle changes.

## Lessons (gotchas — read before re-running)

1. **HTML entity escaping.** Workflow/verifier agents sometimes return the body
   with tags entity-escaped (`&lt;p&gt;`). `process_results.py` detects and
   `html.unescape`s the whole body once. Always run it before publishing; never
   push raw workflow output.
2. **Banned words trip even when negating.** "nu e o copie…" still contains
   *copie*. The `--dry-run` validator (regex `cop[iî]e|clon[aă]|fake|replic[aă]`)
   blocks publish; rephrase to "o suprapunere identica, molecula cu molecula" or
   similar. Forbidden everywhere: copie/clona/fake/replica/"identic 100%"/"1:1".
3. **Hero image = the main CTA product's `featuredImage`** (resolved from the
   index), never a guessed CDN URL. Always valid + on-topic.
4. **Ground every CTA.** Article links must be real in-stock `/products/<handle>`
   from the catalog. GT's inspiration is in the **tags**, Esteban/Nubra's in the
   **title** — `build_index.py` maps both. The dry-run validator enforces this.
5. **Session-limit resilience.** The big write workflow can hit per-session
   limits mid-run; verify stages then fall back to the unverified draft. The
   deterministic `--dry-run` (handles + banned words + length) is the safety net
   — always run it on the full batch, and manually spot-check unverified ones
   (inspiration pairing) before publishing.
6. **Publishing is outward-facing** — confirm with the user before LIVE. Use
   `--dry-run` → `--draft` (review in Shopify admin) → live.
7. **Check the live footer** to learn which menu the theme renders before
   editing navigation (handles named `footer` aren't always the visible ones).
