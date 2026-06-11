---
name: ads-transparency
description: Competitive intelligence on Google Ads — see exactly what ads any advertiser is running, by domain and region, straight from the Google Ads Transparency Center. Returns the advertiser's legal entity, number of active creatives, format/content breakdown (image / HTML / video), first & last shown dates, and sample creatives (image URLs, ad text, creative IDs). No login, no browser — a direct call to the Transparency Center's internal RPC. Use to scout competitors' ad activity, check if a brand is advertising, gauge a competitor's scale/recency, or pull real ad creatives.
---

# Google Ads Transparency Center — competitor ad intel

The Google Ads Transparency Center (adstransparency.google.com) publishes **every ad
any advertiser runs**, per region. It has an **undocumented internal RPC** that returns
this as JSON — **no auth, no cookies, no browser** needed. This skill wraps it.

> Endpoint: `POST https://adstransparency.google.com/anji/_/rpc/SearchService/SearchCreatives`
> Body: `f.req={"2":<limit>,"3":{"8":[<region>],"12":{"1":"<domain>","2":true}},"7":{"1":1,"2":0,"3":<region>}}`
> Headers that matter: `content-type: application/x-www-form-urlencoded`, `x-same-domain: 1`, a real `user-agent`.

## What you get per domain
- Whether the brand advertises on Google at all (0 = not running).
- Advertiser **legal entity** name (e.g. "SC. ZDR DIGITAL MARKET CONCEPT SRL").
- Number of **active creatives** (capped at `--limit`, default 100 → "100+").
- **Content breakdown**: image / HTML-responsive / video / text.
- **First & last shown** dates → longevity + whether they're active *today*.
- **Sample creatives**: image URLs (`tpc.googlesyndication.com/archive/simgad/…` open directly as images), ad text, and HTML/video creative IDs.

## Usage
```bash
uv run adstransparency.py rasheed.ro
uv run adstransparency.py rasheed.ro evero.ro parfumat.ro esteban.ro     # batch
uv run adstransparency.py notino.ro --format json --limit 100 --samples 6
```
Output example:
```
● rasheed.ro — rulează: 100+ creative
   advertiser: SC. ZDR DIGITAL MARKET CONCEPT SRL (100)
   conținut: imagine 68 · html/video 32 · text/alt 0  |  format codes {1:56, 2:31, 3:13}
   activ: 2021-11-16 → 2026-06-10
     - [image] https://tpc.googlesyndication.com/archive/simgad/6857433978873040003 [348x134]
     - [html/video] creativeId 808420485264
● esteban.ro — NU rulează (0 anunțuri)
```

## Regions
`--region 2642` = **România** (default). The number is the Transparency Center's
internal geo anchor, NOT an ISO code. To get another country's code, open
adstransparency.google.com for that region once and read the value in the
`SearchCreatives` request payload (DevTools → Network).

## Decoding notes (for extending the script)
Response `["1"]` is the creatives array. Per creative:
- `"1"` advertiser id (AR…) · `"2"` creative id (CR…)
- `"3"` content: `{"3":{"2":"<img …>"}}` = uploaded image; `{"1":{"4":"…content.js…"}}` = HTML/responsive or video; text ads carry headline/description strings deeper in `"3"`.
- `"4"` format code (1/2/3 — mix of image/responsive/video; classify by *content*, more reliable than the code).
- `"6".1` first-shown unix · `"7".1` last-shown unix · `"12"` advertiser legal name · `"14"` domain.

To pull a single advertiser's **entire** library (not just by-domain), query by advertiser
id instead of domain — swap the `"3"."12"` filter for the advertiser-id filter and paginate
via the response's continuation token.

## Notes / limits
- Returns up to `--limit` creatives (default 100); the absolute total can be higher
  ("100+"). The UI's "~X anunțuri" count comes from a separate call.
- It's an **internal, undocumented** endpoint — if it ever rate-limits (a `/sorry`
  CAPTCHA) or changes shape, fall back to driving the UI with the chrome-devtools MCP:
  navigate to `adstransparency.google.com/?region=RO&domain=<d>` then `evaluate_script`
  the same `fetch` (same-origin). Official bulk data also exists via BigQuery
  (`bigquery-public-data.google_ads_transparency_center`).
- Public data only — read-only, nothing to confirm. Great for scouting before a launch
  (pairs with `gigi:google-ads-mcc` for your own accounts).
