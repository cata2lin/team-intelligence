# /// script
# requires-python = ">=3.10"
# dependencies = ["requests"]
# ///
"""Footer ANPC/SOL badges + menu cleanup for Shopify stores.

The visible-footer companion to compliance.py: compliance.py puts the LEGAL
content (trader-id + ANPC/SAL/SOL) into the Terms policy; this surfaces the
official ANPC SAL + SOL *icon badges* in the footer and removes redundant
text links.

  # add the icon badges, matching the store's footer colour (full-bleed band):
  uv run footer_badges.py add --store CARP --bg "#332f2e" --apply
  uv run footer_badges.py add --store COV  --bg "#334FB4" --apply
  uv run footer_badges.py add --store EST  --bg "#232323" --apply

  # remove redundant ANPC / SOL *text* links from footer menus (icons stay):
  uv run footer_badges.py clean-text --store ROSSI --apply

WHY --bg is required, not auto-detected
  The footer's real background colour can only be read from the RENDERED page
  (getComputedStyle on the footer / its dark or coloured bar), NOT from the
  Admin API. Read it first in a browser (chrome-devtools evaluate_script:
  getComputedStyle(document.querySelector('footer')).backgroundColor, or walk
  the footer's children for the dominant non-transparent bg) and pass the hex.
  Then the badge band blends into the footer instead of floating on white.

Theme coverage
  * Dawn / Dawn-like (footer-group.json with a `footer` section): this script
    adds a `custom-liquid` section `anpc_badges` after the footer, full-bleed.
    Tested: Carpetto, Covoria, Reduceri Bune, Casa Ofertelor.
  * GemPages footer (layout/theme.gempages.footer.liquid) or a `.anpc` div
    injected before </body> in layout/theme.liquid (Esteban/Belasil/Apreciat):
    the badge band lives in the LAYOUT, not footer-group.json — edit the
    `.anpc` div's inline style (add `background:<hex>;padding-top:18px`). For
    GemPages the white copyright strip is painted by row elements with
    auto-generated ids (#gXXXX); inject `<style>#id1,#id2{background:<hex>
    !important}` for each, INCLUDING the outermost row (the body shows white
    through transparent rows). See reference/pitfalls.md.
  * Ella / themes whose footer columns are theme-config linklists: editing the
    Shopify menu may NOT surface a new item — the column is wired to a linklist
    handle in the footer SECTION settings; check there.

IDEMPOTENT: refuses to add a second badge set if one already exists (the
duplicate-icons trap). Always pass --apply to write; dry-run otherwise.
"""
import sys, os, json, argparse
sys.path.insert(0, os.path.dirname(__file__))
from shopify_lib import Store

# Official ANPC SAL + SOL badge images (GT-hosted on Shopify CDN — reliable;
# the anpc.ro originals 404, and the vbrmarketing.ro copies are http-only).
SAL_IMG = "https://cdn.shopify.com/s/files/1/0939/7370/9123/files/anpc-sal1-1.png?v=1744841014"
SOL_IMG = "https://cdn.shopify.com/s/files/1/0939/7370/9123/files/anpc-sol.png?v=1744841013"
SAL_URL = "https://anpc.ro/ce-este-sal/"
SOL_URL = "https://ec.europa.eu/consumers/odr/main/index.cfm?event=main.home2.show&lng=RO"

# markers that say "badges already present" — used to stay idempotent
BADGE_MARKERS = ("anpc-sal1-1", "anpc-sol.png", "badge-anpc", 'class="anpc"', "footer-badges")


def badge_html(bg: str) -> str:
    """Full-bleed badge band. margin-left/right:calc(50% - 50vw) breaks out of
    the section's page-width container so the colour spans the whole viewport
    (a centred custom-liquid section is otherwise ~80px narrower than the page,
    leaving ugly gutters of the section's default bg)."""
    return (
        "<!-- ANPC/SOL footer badges -->"
        "<style>.footer-badges{background:%s;margin-left:calc(50%% - 50vw);"
        "margin-right:calc(50%% - 50vw);padding:14px 0 18px;text-align:center;}"
        ".footer-badges .badge-row{display:flex;justify-content:center;"
        "align-items:center;gap:14px;flex-wrap:wrap;}"
        ".footer-badges img{max-height:46px;width:auto;border-radius:6px;}</style>"
        '<div class="footer-badges"><div class="badge-row">'
        '<a href="%s" target="_blank" rel="noopener"><img src="%s" alt="ANPC – SAL"></a>'
        '<a href="%s" target="_blank" rel="noopener"><img src="%s" alt="SOL"></a>'
        "</div></div>"
    ) % (bg, SAL_URL, SAL_IMG, SOL_URL, SOL_IMG)


def already_has_badges(s: Store) -> str | None:
    """Return where badges already live, or None. Checks footer-group.json +
    the two layout files that may carry an injected `.anpc` div."""
    for key in ("sections/footer-group.json", "layout/theme.liquid",
                "layout/theme.gempages.footer.liquid"):
        try:
            v = s.asset_get(key)
        except Exception:
            continue
        if any(m in v for m in BADGE_MARKERS):
            return key
    return None


def add_badges(s: Store, bg: str, apply: bool, force: bool):
    where = already_has_badges(s)
    if where and not force:
        print(f"  SKIP: badges already present in {where} (use --force to add anyway)")
        return
    try:
        fg = s.asset_get("sections/footer-group.json")
    except Exception:
        print("  no footer-group.json — this theme keeps the footer in the layout; "
              "edit the `.anpc` div in layout/theme.liquid instead (see module docstring).")
        return
    d = json.loads(fg)
    foot = d["sections"].get("footer") or next(iter(d["sections"].values()))
    scheme = foot.get("settings", {}).get("color_scheme", "scheme-1")
    d["sections"]["anpc_badges"] = {
        "type": "custom-liquid",
        "settings": {"custom_liquid": badge_html(bg), "color_scheme": scheme},
    }
    if "anpc_badges" not in d["order"]:
        d["order"].append("anpc_badges")
    if apply:
        s.asset_put("sections/footer-group.json", json.dumps(d, ensure_ascii=False, indent=2))
        print(f"  added anpc_badges section (bg={bg}, scheme={scheme}); order={d['order']}")
    else:
        print(f"  would add anpc_badges section (bg={bg}); order would be {d['order']}")


def clean_text_links(s: Store, apply: bool, force: bool = False):
    """Remove ANPC / SOL TEXT items from every menu that has them, preserving
    all other items faithfully (resourceId carried over so menuUpdate doesn't
    fail with 'Subject can't be blank' on SHOP_POLICY/PAGE items).

    GUARD: refuses to strip the text links if the store has NO icon badges yet —
    otherwise the store is left with ZERO ANPC reference in the footer (the
    mistake that wiped ROSSI/NOC originals before icons existed). Pass --force to
    override."""
    if not already_has_badges(s) and not force:
        print("  REFUSED: no ANPC icon badges found — removing the text links would leave "
              "the footer with NO ANPC reference. Run `add` first, or pass --force.")
        return
    data = s.gql("{menus(first:30){nodes{id handle title items{id title type url resourceId}}}}")
    drop = lambda t: bool(t) and (
        t.strip().upper() in ("ANPC", "SOL", "SAL")
        or "soluționarea" in t.lower() or "solutionarea" in t.lower()
        or "(sol)" in t.lower() or "(sal)" in t.lower())
    for m in data["menus"]["nodes"]:
        if not any(drop(it["title"]) for it in m["items"]):
            continue
        kept = [it for it in m["items"] if not drop(it["title"])]
        items = []
        for it in kept:
            entry = {"title": it["title"], "type": it["type"]}
            if it["resourceId"]:
                entry["resourceId"] = it["resourceId"]
            elif it["url"]:
                entry["url"] = it["url"]
            items.append(entry)
        print(f"  menu '{m['handle']}': dropping {[it['title'] for it in m['items'] if drop(it['title'])]}")
        if apply:
            r = s.gql(
                "mutation($id:ID!,$t:String!,$h:String!,$i:[MenuItemUpdateInput!]!)"
                "{menuUpdate(id:$id,title:$t,handle:$h,items:$i){userErrors{message}}}",
                {"id": m["id"], "t": m["title"], "h": m["handle"], "i": items})
            ue = r.get("menuUpdate", {}).get("userErrors")
            print("    " + ("OK" if not ue else f"ERR {ue}"))


def add_gdpr_link(s: Store, apply: bool):
    """Surface a legal-links column (Termeni / Politica / Livrare / Ștergere date)
    in a Dawn footer. KEY LESSON: a Dawn footer menu renders ONLY if a `link_list`
    BLOCK in the footer section references it — the menu *existing* is not enough
    (that's why the GDPR link was invisible on Reduceri Bune / Covoria even though
    the 'footer' menu had it). So this (1) rebuilds the 'footer' menu with the
    legal links as HTTP items (full URLs, no resourceId hassle) and (2) adds a
    `link_list` block to footer-group.json. Dawn-style themes only."""
    dom = s.public
    items = [
        {"title": "Termeni și condiții", "type": "HTTP", "url": f"https://{dom}/policies/terms-of-service"},
        {"title": "Politica de confidențialitate", "type": "HTTP", "url": f"https://{dom}/policies/privacy-policy"},
        {"title": "Livrare și retur", "type": "HTTP", "url": f"https://{dom}/policies/refund-policy"},
        {"title": "Ștergere date (GDPR)", "type": "HTTP", "url": f"https://{dom}/pages/stergere-date"},
    ]
    fm = next((m for m in s.gql("{menus(first:30){nodes{id handle}}}")["menus"]["nodes"]
               if m["handle"] == "footer"), None)
    if not fm:
        print("  no 'footer' menu — create one in admin, then re-run.")
        return
    try:
        d = json.loads(s.asset_get("sections/footer-group.json"))
    except Exception:
        print("  no footer-group.json — not a Dawn-style footer; add the link via the page builder.")
        return
    foot = d["sections"].get("footer") or next(iter(d["sections"].values()))
    has_block = any(b.get("type") == "link_list" and b.get("settings", {}).get("menu") == "footer"
                    for b in foot.get("blocks", {}).values())
    if apply:
        s.gql("mutation($id:ID!,$t:String!,$h:String!,$i:[MenuItemUpdateInput!]!)"
              "{menuUpdate(id:$id,title:$t,handle:$h,items:$i){userErrors{message}}}",
              {"id": fm["id"], "t": "footer", "h": "footer", "i": items})
        if not has_block:
            foot.setdefault("blocks", {})["link_list_legal"] = {
                "type": "link_list", "settings": {"heading": "Informații", "menu": "footer"}}
            bo = foot.setdefault("block_order", [])
            bo.insert(1 if len(bo) > 1 else len(bo), "link_list_legal")
            s.asset_put("sections/footer-group.json", json.dumps(d, ensure_ascii=False, indent=2))
        print("  footer menu = legal links (incl. Ștergere date) + link_list block rendered")
    else:
        print(f"  would set footer menu (4 legal links) + {'add' if not has_block else 'reuse'} link_list block")


ap = argparse.ArgumentParser()
ap.add_argument("action", choices=["add", "clean-text", "gdpr-link"])
ap.add_argument("--store", required=True, help="stores.csv prefix or *.myshopify.com domain")
ap.add_argument("--bg", help="footer background hex for the badge band (e.g. '#332f2e'); read it live first")
ap.add_argument("--apply", action="store_true")
ap.add_argument("--force", action="store_true", help="add badges even if some already exist")
A = ap.parse_args()

s = Store(A.store) if "." in A.store else Store.from_csv(A.store)
print(f"{A.store} -> {s.public}")
if A.action == "add":
    if not A.bg:
        sys.exit("add needs --bg <hex> (read the footer's real bg colour in a browser first)")
    add_badges(s, A.bg, A.apply, A.force)
elif A.action == "gdpr-link":
    add_gdpr_link(s, A.apply)
else:
    clean_text_links(s, A.apply, A.force)
