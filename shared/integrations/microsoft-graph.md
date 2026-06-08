# Microsoft Graph (OneDrive Personal + Excel Online)

Read/write access to xlsx files on OneDrive (consumer `live.com` accounts and
`onedrive.live.com` shares). Used for the influencer orders workflow on the
shared file `Influenceri Esteban- Belasil.xlsx`.

## App registration

- **Tenant model**: multi-tenant + personal MS accounts ("Any Entra ID Tenant
  + Personal Microsoft accounts").
- **Display name**: `arona-assistant-graph`
- **Application (client) ID**: `7c52a278-ae7a-4bb3-9244-3786c3d20a77`
- **Directory (tenant) ID**: `39a10883-0dcf-414e-9ab5-3f13843212c5` (ARONA SRL,
  unused — auth goes via `consumers` authority for personal-account files).
- **Object ID**: `45c52f2e-3d63-493a-a40f-3732918d08a9`
- **Portal**: <https://entra.microsoft.com> → App registrations → `arona-assistant-graph`

### Authentication / redirect URIs (Mobile and desktop platform)

- `http://localhost` (loopback for MSAL `acquire_token_interactive`)
- `https://login.microsoftonline.com/common/oauth2/nativeclient`
- `msal7c52a278-ae7a-4bb3-9244-3786c3d20a77://auth`
- **Allow public client flows** = **Yes**

### Delegated permissions

- `Files.ReadWrite.All`
- `User.Read` (default)
- `offline_access`, `openid`, `profile` — added implicitly by MSAL; do **not**
  list them in `SCOPES` or MSAL raises "reserved scope" error.

No client secret. No certificate. Public client only.

## Authority

Use `https://login.microsoftonline.com/consumers` for personal-account files
(the share URL pattern is `onedrive.live.com/personal/...` or `1drv.ms/...`).
For ARONA SRL work files use `.../39a10883-0dcf-414e-9ab5-3f13843212c5` or
`.../organizations`.

## Token storage

- **MSAL serialized cache**: `~/.config/microsoft/msal-cache.bin` (chmod 600).
  Contains both access + refresh tokens; survives restarts.
- Helper: [`scripts/microsoft_auth.py`](../scripts/microsoft_auth.py) →
  `from scripts.microsoft_auth import get_token`. First call opens browser
  for interactive sign-in; subsequent calls refresh silently.

The token is **not** stored in `secrets/credentials.env` — MSAL manages it.
Only the `client_id` is hard-coded in `microsoft_auth.py` (it's a public
identifier, not a secret).

## Working pattern

```python
import base64, requests
from scripts.microsoft_auth import get_token

H = {'Authorization': f'Bearer {get_token()}'}

# 1) Resolve a OneDrive share URL → driveItem
share_url = "https://1drv.ms/x/c/.../IQAn...?e=..."
sid = 'u!' + base64.urlsafe_b64encode(share_url.encode()).decode().rstrip('=')
item = requests.get(f'https://graph.microsoft.com/v1.0/shares/{sid}/driveItem',
                    headers=H).json()
drive_id, item_id = item['parentReference']['driveId'], item['id']

# 2) Workbook endpoints (Excel Online — gives full Excel fidelity)
base = f'https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}/workbook'

requests.get(f'{base}/worksheets', headers=H)                          # list sheets
requests.get(f"{base}/worksheets('Comenzi')/usedRange", headers=H)      # cells
requests.patch(f"{base}/worksheets('Comenzi')/range(address='B5')",
               headers=H, json={'values': [['Done']]})                  # write value
requests.patch(f"{base}/worksheets('Comenzi')/range(address='B5')/format/fill",
               headers=H, json={'color': '#C6EFCE'})                    # fill color
```

## File: Influenceri Esteban- Belasil.xlsx

- **Share URL**: <https://1drv.ms/x/c/bafd4bfc079b4528/IQAngzWEG5aQR5MAtw-Ok2TpAdRtXZ4s-9D4C4EoDvdYbAI?e=8OIznj>
- **driveId**: `BAFD4BFC079B4528`
- **itemId**: `BAFD4BFC079B4528!s84358327961b47909300b70f8e9364e9`
- **Worksheets**: `UPLOAD`, `Paid`, `Comenzi`, `Nubra`, `Esteban`, `GT`, `LabNoir`

## Common pitfalls

- **Don't include `offline_access` in `SCOPES`** for MSAL — it's a reserved
  scope; MSAL will raise. MSAL adds it automatically when using
  `PublicClientApplication`, so refresh tokens work.
- **Use `http://localhost`, not `https://localhost`**, for the loopback
  redirect.
- **Allow public client flows** must be **Yes** under Authentication →
  Advanced settings, otherwise `acquire_token_interactive` fails with
  AADSTS7000218.
- Excel `range(address='...')` accepts A1 with optional sheet prefix
  (`'Sheet1'!B5:D10`). When using `worksheets('Name')/range(...)` the sheet
  is implicit — don't double up.
- Format writes (`/format/fill`, `/format/font`, `/format/borders`) are
  separate PATCH calls per range.
- Prefer **session-scoped** workbook calls for batches: `POST .../createSession`
  with `{"persistChanges": true}` → use the `workbook-session-id` header on
  subsequent requests for ~10× speedup; close session at end.

## See also

- [`scripts/microsoft_auth.py`](../scripts/microsoft_auth.py) — auth helper.
- Microsoft Graph Excel docs: <https://learn.microsoft.com/en-us/graph/api/resources/excel>
