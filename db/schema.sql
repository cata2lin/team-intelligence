-- ============================================================================
-- SharedClaude — Arona team knowledge base
-- ----------------------------------------------------------------------------
-- The DB is the knowledge base; the NAS is shared file storage.
-- This file is DDL + non-sensitive seeds ONLY. No secret VALUES live here
-- (the `secrets` table is seeded with key names + descriptions; the actual
-- values are written into the DB out-of-band, never into git).
-- Idempotent: safe to re-run.
-- ============================================================================

-- ---- People & machines -----------------------------------------------------

CREATE TABLE IF NOT EXISTS employees (
  id          serial PRIMARY KEY,
  handle      text NOT NULL UNIQUE,          -- 'iulian', 'catalin', ...
  name        text NOT NULL,
  email       text,
  role        text,
  active      boolean NOT NULL DEFAULT true,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS machines (
  id           serial PRIMARY KEY,
  employee_id  integer REFERENCES employees(id) ON DELETE SET NULL,
  hostname     text NOT NULL,
  os           text,
  nas_mount    text,                         -- this machine's NAS_ROOT
  agent_path   text,                         -- local team-intelligence clone / agent path
  last_seen_at timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (employee_id, hostname)
);

-- ---- Capability registry: skills -------------------------------------------

CREATE TABLE IF NOT EXISTS skills (
  id                 serial PRIMARY KEY,
  plugin             text NOT NULL,          -- author namespace: core, iulian, ...
  name               text NOT NULL,
  author_employee_id integer REFERENCES employees(id) ON DELETE SET NULL,
  description        text,
  version            text,
  file_path          text,                   -- path in the repo
  status             text NOT NULL DEFAULT 'active',   -- active | deprecated | removed
  created_by         integer REFERENCES employees(id) ON DELETE SET NULL,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  UNIQUE (plugin, name)
);
CREATE INDEX IF NOT EXISTS skills_author_idx ON skills (author_employee_id);

-- ---- File registry (NAS + local agents) ------------------------------------

CREATE TABLE IF NOT EXISTS files (
  id          bigserial PRIMARY KEY,
  location    text NOT NULL,                 -- nas | local | repo | external
  path        text NOT NULL,
  machine_id  integer REFERENCES machines(id) ON DELETE SET NULL,  -- for local files
  name        text,
  category    text,                          -- xlsx | pdf | script | export | dataset | ...
  size_bytes  bigint,
  description text,
  source      text,                          -- where ported from, if applicable
  status      text NOT NULL DEFAULT 'active',-- active | archived | deleted
  created_by  integer REFERENCES employees(id) ON DELETE SET NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS files_unique_path
  ON files (location, COALESCE(machine_id, 0), path);
CREATE INDEX IF NOT EXISTS files_created_by_idx ON files (created_by);

-- ---- Secrets / env vars (replaces the NAS credentials.env) ------------------
-- Access is controlled by DB grants (same trust model the NAS file had).
-- kind='config' rows are non-sensitive defaults; kind='secret' rows are secrets.

CREATE TABLE IF NOT EXISTS secrets (
  id           serial PRIMARY KEY,
  key          text NOT NULL UNIQUE,
  value        text,                         -- may be empty until set
  service      text,                         -- postgres | shopify | openai | tom | dpd | ...
  kind         text NOT NULL DEFAULT 'secret',-- secret | config
  description  text,
  is_sensitive boolean NOT NULL DEFAULT true,
  created_by   integer REFERENCES employees(id) ON DELETE SET NULL,
  updated_by   integer REFERENCES employees(id) ON DELETE SET NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  rotated_at   timestamptz
);

-- ---- Per-user NAS login (file storage; replaces no secret, stays in the DB) -
-- Team-wide NAS host/share/base live in the `secrets` table as config rows
-- (NAS_HOST, NAS_SHARE, NAS_BASE). Each user's personal SMB login lives here so
-- Claude can mount their ClaudeShared/<handle> folder on any machine they log in.

CREATE TABLE IF NOT EXISTS nas_credentials (
  employee_id integer PRIMARY KEY REFERENCES employees(id) ON DELETE CASCADE,
  username    text NOT NULL,
  password    text NOT NULL,             -- access-controlled by DB grants
  updated_at  timestamptz NOT NULL DEFAULT now()
);

-- ---- Reference knowledge: IPs, URLs, hosts, docs, links --------------------

CREATE TABLE IF NOT EXISTS resources (
  id          serial PRIMARY KEY,
  category    text NOT NULL,                 -- ip | url | host | endpoint | doc | link | note
  label       text NOT NULL,
  value       text NOT NULL,                 -- the IP / URL / path / text
  service     text,
  description text,
  tags        text[],
  created_by  integer REFERENCES employees(id) ON DELETE SET NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (category, label)
);

-- ---- Sessions (optional roll-up of activity) -------------------------------

CREATE TABLE IF NOT EXISTS sessions (
  id          bigserial PRIMARY KEY,
  session_uid text UNIQUE,                   -- Claude session id if available
  employee_id integer REFERENCES employees(id) ON DELETE SET NULL,
  machine_id  integer REFERENCES machines(id) ON DELETE SET NULL,
  started_at  timestamptz NOT NULL DEFAULT now(),
  ended_at    timestamptz,
  summary     text
);

-- ---- Unified append-only activity / usage / change log ---------------------
-- The "always write here" target. Every create/modify/use/port is one row.

CREATE TABLE IF NOT EXISTS events (
  id          bigserial PRIMARY KEY,
  employee_id integer REFERENCES employees(id) ON DELETE SET NULL,
  machine_id  integer REFERENCES machines(id) ON DELETE SET NULL,
  session_uid text,
  entity_type text NOT NULL,                 -- skill | file | secret | resource | session | claude_md | other
  entity_id   bigint,                        -- loose ref to the entity's PK
  entity_name text,                          -- denormalized for resilience (e.g. 'catalin:excel-api-push')
  action      text NOT NULL,                 -- used | created | modified | removed | ported_in | ported_out | accessed | rotated | ...
  summary     text,
  details     jsonb,
  occurred_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS events_occurred_idx ON events (occurred_at DESC);
CREATE INDEX IF NOT EXISTS events_employee_idx ON events (employee_id);
CREATE INDEX IF NOT EXISTS events_entity_idx   ON events (entity_type, entity_id);
CREATE INDEX IF NOT EXISTS events_action_idx   ON events (action);
CREATE INDEX IF NOT EXISTS events_name_idx      ON events (entity_name);

-- ---- Convenience views ------------------------------------------------------

CREATE OR REPLACE VIEW v_recent_activity AS
  SELECT e.occurred_at,
         emp.handle  AS who,
         e.entity_type,
         e.entity_name,
         e.action,
         e.summary
  FROM events e
  LEFT JOIN employees emp ON emp.id = e.employee_id
  ORDER BY e.occurred_at DESC;

CREATE OR REPLACE VIEW v_skill_usage AS
  SELECT s.plugin,
         s.name,
         count(e.*) FILTER (WHERE e.action = 'used')     AS uses,
         count(e.*) FILTER (WHERE e.action = 'modified') AS edits,
         max(e.occurred_at)                              AS last_event
  FROM skills s
  LEFT JOIN events e
    ON e.entity_type = 'skill'
   AND e.entity_name = s.plugin || ':' || s.name
  GROUP BY s.plugin, s.name;

-- ---- Schema version ---------------------------------------------------------

CREATE TABLE IF NOT EXISTS kb_meta (
  key        text PRIMARY KEY,
  value      text,
  updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO kb_meta (key, value) VALUES ('schema_version', '1')
  ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now();

-- ============================================================================
-- Seeds (non-sensitive)
-- ============================================================================

INSERT INTO employees (handle, name, email) VALUES
  ('iulian',  'Iulian',  NULL),
  ('gigi',    'Gigi',    NULL),
  ('adriana', 'Adriana', NULL),
  ('andreea', 'Andreea', NULL),
  ('anne',    'Anne',    NULL),
  ('catalin', 'Catalin', 'carcucatalin@gmail.com')
ON CONFLICT (handle) DO NOTHING;

-- Known infrastructure / docs (agents add more over time)
INSERT INTO resources (category, label, value, service, description) VALUES
  ('host',     'prod-postgres',    '38.242.226.83:5432',                        'postgres',    'Shared production Postgres server (all app DBs + the SharedClaude KB)'),
  ('host',     'shared-claude-db', '38.242.226.83/SharedClaude',                'postgres',    'This knowledge-base database'),
  ('url',      'tom',              'https://tom.arona.ro',                      'tom',         'TOM central purchase-order hub'),
  ('endpoint', 'dpd-api',          'https://api.dpd.ro/v1',                     'dpd',         'DPD Romania courier API'),
  ('doc',      'claude-plugins',   'https://code.claude.com/docs/en/plugins.md','claude-code', 'Claude Code plugins reference'),
  ('doc',      'claude-skills',    'https://code.claude.com/docs/en/skills.md', 'claude-code', 'Claude Code skills reference'),
  ('doc',      'claude-mcp',       'https://code.claude.com/docs/en/mcp.md',    'claude-code', 'Claude Code MCP reference'),
  ('note',     'shopify-stores',   '11 stores / 9 brands',                      'shopify',     'Roster lives in metrics.shopify_stores joined to brands')
ON CONFLICT (category, label) DO NOTHING;

-- Secret/config key registry (names + descriptions; values set out-of-band).
-- kind='config' values below are non-sensitive defaults and ARE seeded.
INSERT INTO secrets (key, value, service, kind, is_sensitive, description) VALUES
  ('PG_HOST',                '38.242.226.83', 'postgres', 'config', false, 'Postgres host'),
  ('PG_PORT',                '5432',          'postgres', 'config', false, 'Postgres port'),
  ('PG_USER',                'scraper',       'postgres', 'config', false, 'Postgres user'),
  ('PG_PASSWORD',            NULL,            'postgres', 'secret', true,  'Postgres password'),
  ('DATABASE_URL_ARONA_BI',     NULL, 'postgres', 'secret', true, 'arona-bi DB (test)'),
  ('DATABASE_URL_TOM',          NULL, 'postgres', 'secret', true, 'tom DB (tom_wms)'),
  ('DATABASE_URL_AWBPRINT',     NULL, 'postgres', 'secret', true, 'AWBprint DB (read-only catalog)'),
  ('DATABASE_URL_GRANDIA',      NULL, 'postgres', 'secret', true, 'grandia-inventory DB (Grandia)'),
  ('DATABASE_URL_INVENTORYSYNC',NULL, 'postgres', 'secret', true, 'legacy InventorySync DB'),
  ('DATABASE_URL_METRICS',      NULL, 'postgres', 'secret', true, 'metrics DB'),
  ('DATABASE_URL_SCENTUM',      NULL, 'postgres', 'secret', true, 'scentum DB (Parfum_Iulian)'),
  ('DATABASE_URL_TRENDYOL',     NULL, 'postgres', 'secret', true, 'trendyol scraper DB'),
  ('DATABASE_URL_MATTERMOST',   NULL, 'postgres', 'secret', true, 'mattermost DB'),
  ('TOM_BASE_URL',           'https://tom.arona.ro', 'tom', 'config', false, 'TOM API base URL'),
  ('TOM_ARONA_BI_KEY_ID',    NULL, 'tom', 'secret', true, 'TOM HMAC key id (ARONA_BI source)'),
  ('TOM_ARONA_BI_SECRET',    NULL, 'tom', 'secret', true, 'TOM HMAC secret (ARONA_BI source)'),
  ('TOM_ARONA_BI_SOURCE',    'ARONA_BI', 'tom', 'config', false, 'TOM source id'),
  ('TOM_GRANDIA_KEY_ID',     NULL, 'tom', 'secret', true, 'TOM HMAC key id (GRANDIA source)'),
  ('TOM_GRANDIA_SECRET',     NULL, 'tom', 'secret', true, 'TOM HMAC secret (GRANDIA source)'),
  ('TOM_GRANDIA_SOURCE',     'GRANDIA', 'tom', 'config', false, 'TOM source id'),
  ('TOM_PERFUME_KEY_ID',     NULL, 'tom', 'secret', true, 'TOM HMAC key id (PERFUME source)'),
  ('TOM_PERFUME_SECRET',     NULL, 'tom', 'secret', true, 'TOM HMAC secret (PERFUME source)'),
  ('TOM_PERFUME_SOURCE',     'PERFUME', 'tom', 'config', false, 'TOM source id'),
  ('SHOPIFY_SHOP_DOMAIN',    NULL, 'shopify', 'secret', true, 'grandia-inventory shop domain'),
  ('SHOPIFY_CLIENT_ID',      NULL, 'shopify', 'secret', true, 'Shopify client id'),
  ('SHOPIFY_CLIENT_SECRET',  NULL, 'shopify', 'secret', true, 'Shopify client secret'),
  ('SHOPIFY_API_VERSION',    '2024-10', 'shopify', 'config', false, 'Shopify Admin API version'),
  ('META_APP_ID',            NULL, 'meta', 'secret', true, 'Meta app id'),
  ('META_APP_SECRET',        NULL, 'meta', 'secret', true, 'Meta app secret'),
  ('TIKTOK_APP_ID',          NULL, 'tiktok', 'secret', true, 'TikTok app id'),
  ('TIKTOK_APP_SECRET',      NULL, 'tiktok', 'secret', true, 'TikTok app secret'),
  ('GADS_SHEET_ID',          NULL, 'google-ads', 'secret', true, 'Google Ads spend sheet id'),
  ('GADS_GOOGLE_API_KEY',    NULL, 'google-ads', 'secret', true, 'Sheets API key for Google Ads spend'),
  ('GADS_SHEET_NAME',        'DailySpend', 'google-ads', 'config', false, 'Google Ads spend sheet tab'),
  ('GOOGLE_AI_API_KEY',      NULL, 'google-ai', 'secret', true, 'Google AI / Imagen key'),
  ('GEMINI_API_KEY',         NULL, 'google-ai', 'secret', true, 'Gemini key (arona-bi)'),
  ('OPENAI_API_KEY',         NULL, 'openai', 'secret', true, 'OpenAI key (grandia CQ)'),
  ('DPD_API_BASE',           'https://api.dpd.ro/v1', 'dpd', 'config', false, 'DPD API base'),
  ('DPD_RO_USERNAME',        NULL, 'dpd', 'secret', true, 'DPD RO account user'),
  ('DPD_RO_PASSWORD',        NULL, 'dpd', 'secret', true, 'DPD RO account password'),
  ('DPD_JG_USERNAME',        NULL, 'dpd', 'secret', true, 'DPD JG account user'),
  ('DPD_JG_PASSWORD',        NULL, 'dpd', 'secret', true, 'DPD JG account password'),
  ('GOOGLE_OAUTH_SCOPES',    'https://www.googleapis.com/auth/spreadsheets', 'google', 'config', false, 'Sheets OAuth scope'),
  ('INNGEST_EVENT_KEY',      NULL, 'inngest', 'secret', true, 'Inngest event key'),
  ('INNGEST_SIGNING_KEY',    NULL, 'inngest', 'secret', true, 'Inngest signing key'),
  ('BLOB_READ_WRITE_TOKEN_TOM',     NULL, 'vercel-blob', 'secret', true, 'Vercel Blob token (tom)'),
  ('BLOB_READ_WRITE_TOKEN_SCENTUM', NULL, 'vercel-blob', 'secret', true, 'Vercel Blob token (scentum)'),
  ('SCENTUM_AUTH_SECRET',    NULL, 'scentum', 'secret', true, 'NextAuth secret (scentum)'),
  ('GRANDIA_ADMIN_PASSWORD', NULL, 'grandia', 'secret', true, 'grandia-inventory admin password'),
  ('METRICS_ADMIN_EMAIL',    NULL, 'metrics', 'config', false, 'metrics admin email'),
  ('METRICS_ADMIN_PASSWORD', NULL, 'metrics', 'secret', true, 'metrics admin password'),
  ('NAS_HOST',  '192.168.10.107', 'nas', 'config', false, 'NAS host/IP (SMB)'),
  ('NAS_SHARE', 'IT_Dev',         'nas', 'config', false, 'NAS SMB share name'),
  ('NAS_BASE',  'ClaudeShared',   'nas', 'config', false, 'Base folder inside the share; each user gets a subfolder named by handle')
ON CONFLICT (key) DO NOTHING;
