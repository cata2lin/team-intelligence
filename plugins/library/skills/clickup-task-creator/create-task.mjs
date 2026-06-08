#!/usr/bin/env node
/**
 * create-task.mjs — Create a fully-classified task in the company ClickUp
 * workspace via the ClickUp REST API v2. Zero dependencies (Node 18+ global fetch).
 *
 * Env (required):
 *   CLICKUP_API_TOKEN   personal token (pk_...), sent as the raw Authorization header
 *   CLICKUP_TEAM_ID     workspace/team id
 *
 * Usage:
 *   node create-task.mjs --list "<name>" | --list-id <id> --title "<title>" [options]
 *
 * Core options:
 *   --title <t>          (required) task name
 *   --list <name>        list name (resolved via the workspace hierarchy)
 *   --list-id <id>       list id (skips name resolution)
 *   --department <d>     Marketing | Customer Support | Warehouse | Finance | Product | Management | Development
 *   --area <a>           Ads | Creatives | ... (see SKILL.md)
 *   --complexity <1-5>   1 Very Easy · 2 Easy · 3 Medium · 4 Hard · 5 Strategic
 *   --priority <1-4>     1 Urgent · 2 High · 3 Normal · 4 Low
 *   --source <s>         Manager Request | Client Issue | Internal Process | Automation | Recurring Rule | External Trigger
 *   --brand <b>          ROSSI Nails | Nocturna | Belasil | Bonhaus
 *   --task-type <t>      Operational | Strategic | Repetitive | Urgent | Bug | Research
 *   --kpi <text>         KPI / Objective
 *   --assignee <q>       assignee by username or email (resolved to a numeric id)
 *   --reviewer <q>       reviewer by username or email (sets the Reviewer users field)
 *   --due <date>         due date, e.g. 2026-06-20 or an ISO datetime
 *   --time-estimate <m>  estimate in MINUTES
 *   --description <txt>  task description
 *   --tags <a,b,c>       comma-separated tag names
 *   --status <s>         override the list's default status
 *   --dry-run            print the resolved payload without creating
 *   --help               show this help
 */

const API = 'https://api.clickup.com/api/v2';
const TOKEN = process.env.CLICKUP_API_TOKEN;
const TEAM_ID = process.env.CLICKUP_TEAM_ID;

// --- arg parsing -----------------------------------------------------------
function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith('--')) continue;
    const key = a.slice(2);
    if (key === 'dry-run' || key === 'help') { out[key] = true; continue; }
    const val = argv[i + 1];
    if (val === undefined || val.startsWith('--')) { out[key] = true; continue; }
    out[key] = val; i++;
  }
  return out;
}
const args = parseArgs(process.argv.slice(2));

function help() {
  console.log(`create-task.mjs — create a classified ClickUp task

Required env: CLICKUP_API_TOKEN, CLICKUP_TEAM_ID
Required args: --title, and one of --list / --list-id
Company-required: --department --area --complexity --priority --source

Example:
  node create-task.mjs --list "Marketing Tasks" --title "Redo creatives" \\
    --department Marketing --area Creatives --brand "ROSSI Nails" \\
    --complexity 3 --priority 2 --source "Manager Request" --assignee iulian --due 2026-06-20

See SKILL.md for the full field reference. Use --dry-run to preview.`);
}

function die(msg) { console.error(`✗ ${msg}`); process.exit(1); }

// --- API helper ------------------------------------------------------------
let lastCall = 0;
async function api(method, path, body) {
  // gentle pacing (~6/s) to stay well under the 100/min limit
  const since = Date.now() - lastCall;
  if (since < 160) await new Promise((r) => setTimeout(r, 160 - since));
  lastCall = Date.now();

  const res = await fetch(`${API}${path}`, {
    method,
    headers: { Authorization: TOKEN, 'Content-Type': 'application/json' },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  const text = await res.text();
  let json;
  try { json = text ? JSON.parse(text) : {}; } catch { json = { raw: text }; }
  if (!res.ok) {
    const err = json?.err || json?.ECODE || text || res.statusText;
    throw new Error(`ClickUp ${res.status} on ${method} ${path}: ${err}`);
  }
  return json;
}

// --- helpers ---------------------------------------------------------------
const norm = (s) => String(s ?? '').toLowerCase().trim().replace(/[_\-/\\]+/g, ' ').replace(/\s+/g, ' ').replace(/[^a-z0-9 ]/g, '');

/** Canonical company field → name aliases used to match a list's custom fields. */
const FIELD_ALIASES = {
  department: ['department', 'dept', 'departament'],
  area: ['area', 'functional area', 'process area', 'category'],
  complexity: ['complexity', 'complexitate', 'difficulty', 'effort'],
  taskType: ['task type', 'tasktype', 'type', 'tip task', 'tip'],
  source: ['source', 'task source', 'sursa', 'origin'],
  brand: ['brand', 'marca', 'brand name'],
  kpi: ['kpi', 'kpi objective', 'objective', 'goal', 'target', 'obiectiv'],
  reviewer: ['reviewer', 'approver', 'review by', 'qa'],
  nrTel: ['nr tel', 'telefon', 'phone', 'phone number', 'tel'],
};

/** Find a list's custom field object for a canonical key, by fuzzy name match. */
function findField(fields, canonicalKey) {
  const aliases = FIELD_ALIASES[canonicalKey].map(norm);
  return fields.find((f) => {
    const n = norm(f.name);
    return aliases.some((al) => n === al || n.includes(al) || al.includes(n));
  }) || null;
}

/** Resolve a dropdown/labels option uuid by matching the desired label. */
function resolveOption(field, desired) {
  const opts = field.type_config?.options ?? [];
  const want = norm(desired);
  const match = opts.find((o) => norm(o.name ?? o.label) === want)
    || opts.find((o) => norm(o.name ?? o.label).includes(want) || want.includes(norm(o.name ?? o.label)));
  return match ?? null;
}

/** Walk the workspace hierarchy and return every list as { id, name, path }. */
async function collectLists() {
  const lists = [];
  const { spaces = [] } = await api('GET', `/team/${TEAM_ID}/space`);
  for (const space of spaces) {
    const [foldersRes, folderless] = await Promise.all([
      api('GET', `/space/${space.id}/folder`).catch(() => ({ folders: [] })),
      api('GET', `/space/${space.id}/list`).catch(() => ({ lists: [] })),
    ]);
    for (const l of folderless.lists ?? []) lists.push({ id: l.id, name: l.name, path: `${space.name} › ${l.name}` });
    for (const folder of foldersRes.folders ?? []) {
      for (const l of folder.lists ?? []) lists.push({ id: l.id, name: l.name, path: `${space.name} › ${folder.name} › ${l.name}` });
    }
  }
  return lists;
}

async function resolveListId(name) {
  const lists = await collectLists();
  const want = norm(name);
  const exact = lists.filter((l) => norm(l.name) === want);
  const partial = lists.filter((l) => norm(l.name) !== want && norm(l.name).includes(want));
  const hit = exact[0] ?? partial[0];
  if (!hit) {
    die(`No list matches "${name}". Available lists:\n  ` + lists.map((l) => l.path).join('\n  '));
  }
  if (exact.length + partial.length > 1) {
    console.error(`! Multiple lists match "${name}"; using "${hit.path}". Use --list-id to be explicit.`);
  }
  return hit.id;
}

async function resolveMemberId(query) {
  const { teams = [] } = await api('GET', '/team');
  const team = teams.find((t) => String(t.id) === String(TEAM_ID)) ?? teams[0];
  const want = norm(query);
  const members = (team?.members ?? []).map((m) => m.user);
  const hit =
    members.find((u) => norm(u.email) === want) ||
    members.find((u) => norm(u.username) === want) ||
    members.find((u) => norm(u.username).includes(want) || norm(u.email).includes(want));
  if (!hit) die(`No workspace member matches "${query}". Members: ${members.map((u) => u.username).join(', ')}`);
  return hit.id;
}

/** Build the custom_fields[] payload from provided taxonomy values. */
function buildCustomFields(fields, provided, warnings) {
  const out = [];
  for (const [key, raw] of Object.entries(provided)) {
    if (raw === undefined || raw === null || raw === '') continue;
    const field = findField(fields, key);
    if (!field) { warnings.push(`Field for "${key}" not found on this list — skipped.`); continue; }
    const type = field.type;
    if (type === 'drop_down') {
      const opt = resolveOption(field, raw);
      if (!opt) { warnings.push(`No "${field.name}" option matches "${raw}" — skipped.`); continue; }
      out.push({ id: field.id, value: opt.id });
    } else if (type === 'labels') {
      const opt = resolveOption(field, raw);
      if (!opt) { warnings.push(`No "${field.name}" label matches "${raw}" — skipped.`); continue; }
      out.push({ id: field.id, value: [opt.id] });
    } else if (type === 'number' || type === 'currency') {
      const n = Number(raw);
      if (Number.isNaN(n)) { warnings.push(`"${field.name}" expects a number, got "${raw}" — skipped.`); continue; }
      out.push({ id: field.id, value: n });
    } else if (type === 'text' || type === 'short_text' || type === 'url' || type === 'email') {
      out.push({ id: field.id, value: String(raw) });
    } else {
      warnings.push(`"${field.name}" is type "${type}" — set it manually; skipped.`);
    }
  }
  return out;
}

// --- main ------------------------------------------------------------------
async function main() {
  if (args.help) return help();
  if (!TOKEN) die('CLICKUP_API_TOKEN is not set.');
  if (!TEAM_ID) die('CLICKUP_TEAM_ID is not set.');
  if (!args.title) die('--title is required.');
  if (!args['list'] && !args['list-id']) die('Provide --list "<name>" or --list-id <id>.');

  const listId = args['list-id'] ? String(args['list-id']) : await resolveListId(args['list']);

  // Discover the list's custom fields
  const { fields = [] } = await api('GET', `/list/${listId}/field`);

  const warnings = [];
  const customFields = buildCustomFields(fields, {
    department: args.department,
    area: args.area,
    complexity: args.complexity,
    taskType: args['task-type'],
    source: args.source,
    brand: args.brand,
    kpi: args.kpi,
    nrTel: args['nr-tel'],
  }, warnings);

  // Resolve assignee
  const assignees = [];
  if (args.assignee) assignees.push(await resolveMemberId(args.assignee));

  // Due date (ms)
  let due_date;
  if (args.due) {
    const ms = Date.parse(args.due);
    if (Number.isNaN(ms)) die(`--due "${args.due}" is not a valid date.`);
    due_date = ms;
  }

  const payload = {
    name: args.title,
    ...(args.description ? { description: args.description } : {}),
    ...(assignees.length ? { assignees } : {}),
    ...(args.priority ? { priority: Number(args.priority) } : {}),
    ...(due_date !== undefined ? { due_date, due_date_time: /[T:]/.test(args.due) } : {}),
    ...(args['time-estimate'] ? { time_estimate: Number(args['time-estimate']) * 60_000 } : {}),
    ...(args.tags ? { tags: String(args.tags).split(',').map((t) => t.trim()).filter(Boolean) } : {}),
    ...(args.status ? { status: args.status } : {}),
    check_required_custom_fields: false,
    ...(customFields.length ? { custom_fields: customFields } : {}),
  };

  if (args['dry-run']) {
    console.log('— DRY RUN —');
    console.log('list id:', listId);
    console.log(JSON.stringify(payload, null, 2));
    if (warnings.length) console.log('\nwarnings:\n  ' + warnings.join('\n  '));
    return;
  }

  const task = await api('POST', `/list/${listId}/task`, payload);

  // Reviewer is a "users" custom field — set it after creation.
  if (args.reviewer) {
    const reviewerField = findField(fields, 'reviewer');
    if (reviewerField) {
      const uid = await resolveMemberId(args.reviewer);
      try {
        await api('POST', `/task/${task.id}/field/${reviewerField.id}`, { value: { add: [uid], rem: [] } });
      } catch (e) { warnings.push(`Could not set Reviewer: ${e.message}`); }
    } else {
      warnings.push('No "Reviewer" field on this list — reviewer skipped.');
    }
  }

  console.log(`✓ Created task: ${task.name}`);
  console.log(`  id:  ${task.id}`);
  console.log(`  url: ${task.url ?? `https://app.clickup.com/t/${task.id}`}`);
  console.log(`  custom fields set: ${customFields.length}`);
  if (warnings.length) console.log('  warnings:\n    ' + warnings.join('\n    '));
}

main().catch((e) => die(e.message));
