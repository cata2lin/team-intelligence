# Embedded frontend + the deploy/verify loop (React Router 7 + Polaris + App Bridge)

The backend patterns in this skill are only half the app. The embedded **frontend**
and the **deploy→verify loop** have their own set of traps that cost real hours the
first time. Every one below was learned the hard way shipping a production embedded
app (React Router 7 framework mode, Polaris 13, App Bridge, `@shopify/shopify-app-remix`).

## The #1 meta-rule: `tsc` green + build green ≠ it works. VERIFY VISUALLY.

`tsc --noEmit` only type-checks the files in your `tsconfig` `include` — typically
`app/services`, `app/lib`, `app/workers`. **Route and component `.tsx` files are NOT
type-checked** — Vite only *transpiles* them at build. So a route can reference a
deleted export, pass the wrong prop, or crash at render, and BOTH `tsc` and
`npm run build` stay green.

→ After **every** deploy, open the app in the Shopify admin (Chrome DevTools MCP),
**run the actual flow** (click the button, submit the form, open the editor), and
**screenshot** it. Logs alone lie: a route-level 404 or a render crash shows on the
*screen*, not necessarily in the server log. If the user says "there's an error" and
you only see clean logs — you're looking in the wrong place. Look at the pixels.

## Routing: framework mode = routes are EXPLICIT config

React Router 7 in framework mode (as configured here) uses **`app/routes.ts` as an
explicit registry** — it is NOT filesystem auto-routing. A new `app/routes/app.foo.tsx`
file **does nothing until you register it** in `routes.ts`. Symptom: a link/button
navigates and you get a hard 404 ("Add a source" 404'd, `/privacy` 404'd) even though
the route file clearly exists.

- Public/unauthenticated pages (legal, health, pixel ingest) go **top-level**, NOT under
  the `/app` auth layout — else they demand a Shopify session and can't render for a
  reviewer or a bot.
- More-specific routes must be registered **before** a splat (`auth/*`) to win the match.

## The auth catch-all must special-case the login path

A splat `auth.$.tsx` loader that blindly calls `authenticate.admin(request)` **throws
on the configured login path** ("Detected call to shopify.authenticate.admin() from
configured login path"). Detect the login path and call `login(request)` there instead;
only non-login paths get `authenticate.admin`.

## Embedded iframe caching: `Cache-Control: no-store` on the HTML document

The app runs in an **iframe** inside Shopify admin. Without `Cache-Control: no-store`
on the **HTML document response**, the iframe caches the old page and a hard-refresh on
the admin does **not** bust it → your deploys silently don't appear ("it's the same").

Worse, if a clean rebuild deletes the old hashed JS chunks (`rm -rf build`), a client
still holding cached HTML requests `/assets/app.optimize-OLD.js` → **404 → the feature
breaks** (a button hangs, an edit inserts `null`, a tab won't change). The server log
tell is `No route matches URL "/assets/…"`.

Fix — one header, inherited by every route:

```tsx
// app/root.tsx
export function headers() {
  return { "Cache-Control": "no-store, must-revalidate" };
}
```

After this, every update lands on a normal reload. Existing clients must break their
cache **once** (incognito / clear cache / close-reopen the app). Verify the header with
`get_network_request` on the iframe document.

## React error #31 = `onClick={fn}` passing the SyntheticEvent

`<Button onClick={runThing}>` where `runThing(arg?)` — React passes the **event object**
as the first arg → `runThing(event)`. If `arg` feeds a `setState` that later renders
(`setBusyMsg(event)`), React throws **#31 "Objects are not valid as a React child"** and
**the whole component tree crashes** with a blank "Application Error". The stack shows
event-shaped keys (`_reactName`, `nativeEvent`, `target`, `_targetInst`).

Rule: for ANY handler that takes arguments, wrap it — `onClick={() => runThing()}`, never
`onClick={runThing}`. Belt-and-suspenders: type-guard any state that can reach render
(`setBusyMsg(typeof x === "string" ? x : "…")`).

## Width in embedded is YOUR `maxWidth` wrapper, not App Bridge

App Bridge fills the frame; Polaris `<Page fullWidth>` only affects the page chrome.
If a page looks boxed-in with big symmetric dead margins, the cap is almost always **your
own** centered `maxWidth` div (a shared `ContentWidth`-style wrapper). Two failure modes:

1. A centered `maxWidth: 960` body under a **full-width** page title/header reads as
   *broken* (narrow island misaligned beneath a wide header), not as "narrow".
2. Going full-width then exposes sparse tables: a 3-column `IndexTable` stretched to the
   frame gets a ~1000px dead gap mid-row (last column right-aligned, content left).

Decide once — full-width or a comfortable measure — and apply it consistently. If you go
full-width and a data table looks sparse, **make one column greedy** (`flex:1 1 auto;
min-width:0` on the title cell's inner, `width:100%` on the flex row) so it eats the
slack and the status/action cluster hugs the right edge — don't re-cap just that table.
A `maxWidth` div DOES visually cap; the frame does not override it.

## The deploy pipeline (rsync → conditional install/migrate → build → restart)

Order matters; skip a step only when nothing in that category changed:

```
rsync -az --delete app/ prisma/  →  box:/opt/app/
# on the box, in order:
npm install                       # ONLY if package.json changed
npx prisma db push --skip-generate # ONLY if schema.prisma changed  (never a destructive reset)
npx prisma generate               # if schema changed
npm run build
pm2 restart <app>-web --update-env
```

- A broken `routes.ts`/import fails the boot → **every** route 500s, not just the new
  one. So if `/api/health` + one public page both return 200 after restart, the server
  booted clean.
- `--update-env` so rotated secrets are picked up.

## Cost-guard: tier the model, cap output, never charge on failure

LLM features on a credit budget: route cheap/mechanical calls to a **workhorse** model
(Haiku) and only the flagship generation (the long article draft) to a **hard** model
(Sonnet); cap `max_output_tokens`; and **never spend a credit when the call fails** —
gate the credit debit on a validated, non-empty result (guard against the model
returning `null`/empty which otherwise gets stringified into the UI as literal "null").

## Quick checklist after any embedded-frontend change

1. `tsc --noEmit` (catches services/lib/workers only) + `npm run build`.
2. Deploy (pipeline above).
3. Open in Shopify admin, **run the flow**, **screenshot** — desktop AND mobile (390px).
4. If "nothing changed": check the iframe doc `Cache-Control`, look for `/assets/…` 404s.
5. New route not resolving → is it registered in `routes.ts`?


## More bug classes from a full production audit (42 confirmed bugs, one app)

These joined the list after an adversarial multi-agent audit of a live app — prime any
review with them:

- **UNGUARDED-CHARGE (LLM/credit apps)** — parse and validate the model output FIRST;
  write the usage ledger + move credit balances only AFTER a validated, non-empty result.
  Failed calls log a zero-cost row. Watch return types: a helper that returns a string
  pool-name will happily satisfy `if (!spent.ok)` forever (undefined is falsy) while the
  decrement already ran — charging on every click and never succeeding.
- **`fetcher.Form` vs `useActionData`** — responses to fetcher submits land in
  `fetcher.data`, never in `useActionData`. Success/error banners keyed to the wrong one
  are dead code: failures become total silent no-ops. Read `fetcher.data ?? actionData`.
- **BullMQ jobIds** — no `:` allowed (Shopify gids contain `gid://` → sanitize). And a
  deterministic jobId + completed-job retention makes a deliberate re-run a SILENT NO-OP
  (adds with an existing jobId are ignored): `removeOnComplete: true` on queues where the
  user can legitimately click again.
- **DOMOutputSpec children are TEXT NODES** (Tiptap/ProseMirror renderHTML) — an HTML
  string child publishes the literal markup (`<strong>` visible in the storefront). Build
  nested spec arrays.
- **React NodeView drift** — local state seeded once from node attrs desyncs on undo/redo
  and external edits (re-sync via an attrs fingerprint while not editing). ProseMirror
  reuses widget DOM by KEY: a widget that paints from host storage at creation needs a
  version in its key or it never repaints. Positions captured at draw/submit time go stale
  the moment the doc changes — resolve fresh at click time (`view.posAtDOM`) or freeze the
  editor (`setEditable(false)`) for the duration of an async round-trip.
- **External OAuth from an embedded app** — the provider redirects TOP-LEVEL with no
  embedded session, so `authenticate.admin` in the callback can never succeed (tokens
  silently never stored). The trust anchor is an HMAC-SIGNED `state` (app secret, short
  TTL, shop domain inside), minted in the authenticated UI; consent buttons should link
  STRAIGHT to the provider with `target="_top"` (URL built server-side in the loader — no
  intermediate app-route hop, which would die on the missing session); the callback
  validates state → stores tokens → redirects top-level back into
  `admin.shopify.com/store/{handle}/apps/{apiKey}/…`.
- **Destructive actions in lists** — every list of user-created things needs a delete;
  use two separate confirm buttons (icon → "Yes, delete / Cancel", auto-cancel timer).
  A single state-morphing button drops the second click on a detached node, and
  `window.confirm` is unreliable inside the iframe.

## The debug method that found them

1. **Finders per subsystem** (7-8 parallel agents: routes, actions, editor extensions,
   services, workers, nav/shell), primed with the bug classes above, told to report only
   user-reachable bugs with a concrete repro.
2. **Adversarial verification of every finding** ("try to REFUTE it — trace the full
   path; default to not-real") — kills the plausible-but-wrong ~15%.
3. **Fix in waves on disjoint file sets**, tsc + build gated, deploy, then **drive the
   changed flows in the browser** — reproduce the USER'S exact gesture (their button, not
   your URL), and read the pixels, not just the logs.
