# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
clickup_report.py — Raportare READ-ONLY pe workspace-ul ClickUp al firmei (arona.ro).

Companion la clickup-task-creator (care DOAR creează). Aici DOAR citim: ce e
deschis, pe cine, pe ce listă/departament, ce e overdue, ce n-are responsabil,
și gunoiul fără due-date (de curățat).

NU scrie nimic în ClickUp. Enumeră spaces -> folders -> lists + listele
folderless, ia /list/{id}/task?include_closed=false&subtasks=true (paginat),
rezolvă assignee-ii prin /team și tabelează în consolă.

Folosire:
  uv run clickup_report.py --by-person          # backlog pe fiecare om
  uv run clickup_report.py --overdue            # taskuri cu due_date trecut
  uv run clickup_report.py --unassigned         # taskuri fără responsabil
  uv run clickup_report.py --junk               # taskuri fără due_date (candidați curățare)
  uv run clickup_report.py --list "Rapoarte"    # tot ce e deschis într-o listă
  uv run clickup_report.py --by-list            # sumar pe listă/departament
  uv run clickup_report.py --all                # dashboard complet (toate de mai sus)
  uv run clickup_report.py --stale 30           # deschise neatinse de 30+ zile
Opțiuni: --space "Proiecte" filtrează la un singur space; --limit N taie listele lungi.
"""
import sys, os, json, time, argparse, subprocess, urllib.parse, urllib.request, urllib.error

API = "https://api.clickup.com/api/v2"
NOW_MS = int(time.time() * 1000)


def secret(key):
    v = os.environ.get(key)
    if v:
        return v.strip()
    kb = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "core", "scripts", "kb.py")
    return subprocess.run(["uv", "run", kb, "secret-get", key], capture_output=True, text=True).stdout.strip()


def api_get(path, token, params=None):
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": token, "Content-Type": "application/json"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429:  # rate limited — back off
                time.sleep(2 + attempt * 2)
                continue
            raise
        except urllib.error.URLError:
            time.sleep(1 + attempt)
    raise RuntimeError("ClickUp API a eșuat după retry: " + path)


def deacc(s):
    for a, b in (("ă", "a"), ("â", "a"), ("î", "i"), ("ș", "s"), ("ş", "s"), ("ț", "t"), ("ţ", "t")):
        s = s.replace(a, b)
    return s.lower().strip()


# ---------- workspace enumeration ----------
def enumerate_lists(token, team, space_filter=None):
    """Întoarce [{id,name,space,folder}] pentru toate listele (folder + folderless)."""
    spaces = api_get(f"/team/{team}/space", token, {"archived": "false"}).get("spaces", [])
    lists = []
    for sp in spaces:
        if space_filter and deacc(space_filter) not in deacc(sp["name"]):
            continue
        sid, sname = sp["id"], sp["name"]
        folders = api_get(f"/space/{sid}/folder", token, {"archived": "false"}).get("folders", [])
        for f in folders:
            for l in f.get("lists", []):
                lists.append({"id": l["id"], "name": l["name"], "space": sname, "folder": f["name"]})
        flless = api_get(f"/space/{sid}/list", token, {"archived": "false"}).get("lists", [])
        for l in flless:
            lists.append({"id": l["id"], "name": l["name"], "space": sname, "folder": None})
    return lists


def fetch_tasks(token, team, lst):
    """Toate taskurile deschise dintr-o listă (paginat). Adaugă meta listă pe fiecare."""
    out, page = [], 0
    while True:
        d = api_get(f"/list/{lst['id']}/task", token,
                    {"include_closed": "false", "subtasks": "true", "page": page})
        ts = d.get("tasks", [])
        for t in ts:
            t["_list"] = lst["name"]
            t["_space"] = lst["space"]
            out.append(t)
        if d.get("last_page", True) or not ts:
            break
        page += 1
    return out


def members(token, team):
    d = api_get("/team", token)
    for tm in d.get("teams", []):
        if str(tm.get("id")) == str(team) or len(d["teams"]) == 1:
            return {str(m["user"]["id"]): (m["user"].get("username") or m["user"].get("email") or str(m["user"]["id"])).strip()
                    for m in tm.get("members", [])}
    return {}


# ---------- task helpers ----------
def due_ms(t):
    d = t.get("due_date")
    return int(d) if d not in (None, "", "null") else None


def updated_ms(t):
    d = t.get("date_updated")
    return int(d) if d not in (None, "", "null") else None


def assignee_names(t, mem):
    return [a.get("username", "").strip() or mem.get(str(a.get("id")), str(a.get("id"))) for a in t.get("assignees", [])]


def fmt_date(ms):
    if not ms:
        return "—"
    return time.strftime("%Y-%m-%d", time.localtime(ms / 1000))


def days_ago(ms):
    if not ms:
        return None
    return int((NOW_MS - ms) / 86400000)


def status_of(t):
    return (t.get("status") or {}).get("status", "?")


PRI = {"1": "Urgent", "2": "High", "3": "Normal", "4": "Low", None: "—"}


def pri_of(t):
    p = t.get("priority")
    if not p:
        return "—"
    return PRI.get(str(p.get("priority")), p.get("priority", "—"))


# ---------- report modes ----------
def collect(token, team, space_filter):
    lists = enumerate_lists(token, team, space_filter)
    mem = members(token, team)
    all_tasks = []
    for lst in lists:
        all_tasks.append((lst, fetch_tasks(token, team, lst)))
    return lists, mem, all_tasks


def flat(all_tasks):
    return [t for _, ts in all_tasks for t in ts]


def report_by_person(all_tasks, mem, limit):
    tasks = flat(all_tasks)
    by = {}
    unassigned = 0
    for t in tasks:
        names = assignee_names(t, mem)
        if not names:
            unassigned += 1
            continue
        for n in names:
            by.setdefault(n, []).append(t)
    print("\n=== ClickUp — backlog deschis PE PERSOANĂ ===")
    print("%-26s %6s %9s %9s" % ("persoană", "total", "overdue", "fără due"))
    print("-" * 54)
    for name in sorted(by, key=lambda n: -len(by[n])):
        ts = by[name]
        od = sum(1 for t in ts if due_ms(t) and due_ms(t) < NOW_MS)
        nd = sum(1 for t in ts if not due_ms(t))
        print("%-26s %6d %9d %9d" % (name[:26], len(ts), od, nd))
    print("-" * 54)
    print("%-26s %6d" % ("(neasignate)", unassigned))
    print("Total taskuri deschise: %d" % len(tasks))


def report_overdue(all_tasks, mem, limit):
    tasks = [t for t in flat(all_tasks) if due_ms(t) and due_ms(t) < NOW_MS]
    tasks.sort(key=lambda t: due_ms(t))
    print("\n=== ClickUp — OVERDUE (due_date trecut, %d taskuri) ===" % len(tasks))
    print("%-40s %-12s %-10s %-18s %s" % ("task", "due", "zile", "responsabil", "listă"))
    print("-" * 100)
    for t in tasks[:limit]:
        who = ", ".join(assignee_names(t, mem)) or "—"
        print("%-40s %-12s %-10s %-18s %s" % (
            t["name"][:40], fmt_date(due_ms(t)), "%dz" % days_ago(due_ms(t)), who[:18], t["_list"]))
    if len(tasks) > limit:
        print("... +%d (folosește --limit)" % (len(tasks) - limit))


def report_unassigned(all_tasks, mem, limit):
    tasks = [t for t in flat(all_tasks) if not t.get("assignees")]
    tasks.sort(key=lambda t: -(updated_ms(t) or 0))
    print("\n=== ClickUp — FĂRĂ RESPONSABIL (%d taskuri) ===" % len(tasks))
    print("%-46s %-12s %-12s %s" % ("task", "status", "due", "listă"))
    print("-" * 90)
    for t in tasks[:limit]:
        print("%-46s %-12s %-12s %s" % (t["name"][:46], status_of(t)[:12], fmt_date(due_ms(t)), t["_list"]))
    if len(tasks) > limit:
        print("... +%d (folosește --limit)" % (len(tasks) - limit))


def report_junk(all_tasks, mem, limit):
    # gunoi de curățat = fără due_date (nu se pot urmări) — cele mai vechi neatinse sus
    tasks = [t for t in flat(all_tasks) if not due_ms(t)]
    tasks.sort(key=lambda t: (updated_ms(t) or 0))
    print("\n=== ClickUp — JUNK / fără DUE DATE (candidați curățare, %d) ===" % len(tasks))
    print("%-44s %-12s %-10s %-16s %s" % ("task", "status", "ultim upd", "responsabil", "listă"))
    print("-" * 100)
    for t in tasks[:limit]:
        who = ", ".join(assignee_names(t, mem)) or "—"
        upd = updated_ms(t)
        age = "%dz" % days_ago(upd) if upd else "—"
        print("%-44s %-12s %-10s %-16s %s" % (t["name"][:44], status_of(t)[:12], age, who[:16], t["_list"]))
    if len(tasks) > limit:
        print("... +%d (folosește --limit)" % (len(tasks) - limit))


def report_stale(all_tasks, mem, limit, days):
    cutoff = NOW_MS - days * 86400000
    tasks = [t for t in flat(all_tasks) if (updated_ms(t) or NOW_MS) < cutoff]
    tasks.sort(key=lambda t: (updated_ms(t) or 0))
    print("\n=== ClickUp — STALE (neatinse de %d+ zile, %d taskuri) ===" % (days, len(tasks)))
    print("%-44s %-12s %-10s %-16s %s" % ("task", "status", "ultim upd", "responsabil", "listă"))
    print("-" * 100)
    for t in tasks[:limit]:
        who = ", ".join(assignee_names(t, mem)) or "—"
        print("%-44s %-12s %-10s %-16s %s" % (
            t["name"][:44], status_of(t)[:12], "%dz" % days_ago(updated_ms(t)), who[:16], t["_list"]))
    if len(tasks) > limit:
        print("... +%d (folosește --limit)" % (len(tasks) - limit))


def report_by_list(all_tasks, mem, limit):
    print("\n=== ClickUp — sumar PE LISTĂ / DEPARTAMENT ===")
    print("%-22s %-14s %6s %8s %8s %9s" % ("listă", "space", "total", "overdue", "fără due", "neasignat"))
    print("-" * 76)
    grand = [0, 0, 0, 0]
    for lst, ts in sorted(all_tasks, key=lambda x: -len(x[1])):
        od = sum(1 for t in ts if due_ms(t) and due_ms(t) < NOW_MS)
        nd = sum(1 for t in ts if not due_ms(t))
        un = sum(1 for t in ts if not t.get("assignees"))
        grand[0] += len(ts); grand[1] += od; grand[2] += nd; grand[3] += un
        print("%-22s %-14s %6d %8d %8d %9d" % (lst["name"][:22], lst["space"][:14], len(ts), od, nd, un))
    print("-" * 76)
    print("%-22s %-14s %6d %8d %8d %9d" % ("TOTAL", "", grand[0], grand[1], grand[2], grand[3]))


def report_list_detail(all_tasks, mem, limit, list_name):
    match = [(lst, ts) for lst, ts in all_tasks if deacc(list_name) in deacc(lst["name"])]
    if not match:
        avail = ", ".join(sorted(set(lst["name"] for lst, _ in all_tasks)))
        print("Nicio listă care să conțină '%s'. Disponibile: %s" % (list_name, avail))
        return
    for lst, ts in match:
        ts = sorted(ts, key=lambda t: (due_ms(t) is None, due_ms(t) or 0))
        print("\n=== Listă '%s' (%s) — %d taskuri deschise ===" % (lst["name"], lst["space"], len(ts)))
        print("%-42s %-12s %-11s %-8s %-16s" % ("task", "status", "due", "prio", "responsabil"))
        print("-" * 95)
        for t in ts[:limit]:
            who = ", ".join(assignee_names(t, mem)) or "—"
            print("%-42s %-12s %-11s %-8s %-16s" % (
                t["name"][:42], status_of(t)[:12], fmt_date(due_ms(t)), pri_of(t)[:8], who[:16]))
        if len(ts) > limit:
            print("... +%d (folosește --limit)" % (len(ts) - limit))


def main():
    ap = argparse.ArgumentParser(description="Raport READ-ONLY pe ClickUp (arona.ro).")
    ap.add_argument("--by-person", action="store_true", help="backlog pe fiecare om")
    ap.add_argument("--overdue", action="store_true", help="taskuri cu due_date trecut")
    ap.add_argument("--unassigned", action="store_true", help="taskuri fără responsabil")
    ap.add_argument("--junk", action="store_true", help="taskuri fără due_date (curățare)")
    ap.add_argument("--by-list", action="store_true", help="sumar pe listă/departament")
    ap.add_argument("--list", dest="list_name", help="detaliu pentru o listă anume")
    ap.add_argument("--stale", type=int, metavar="ZILE", help="deschise neatinse de N+ zile")
    ap.add_argument("--all", action="store_true", help="dashboard complet")
    ap.add_argument("--space", help="filtrează la un singur space (Departamente/Proiecte/Rapoarte/Documentatie)")
    ap.add_argument("--limit", type=int, default=30, help="maxim rânduri per listă (default 30)")
    a = ap.parse_args()

    token = secret("CLICKUP_API_TOKEN")
    team = secret("CLICKUP_TEAM_ID")
    if not token or not team:
        print("Lipsesc CLICKUP_API_TOKEN / CLICKUP_TEAM_ID din KB.", file=sys.stderr)
        sys.exit(1)

    any_mode = any([a.by_person, a.overdue, a.unassigned, a.junk, a.by_list, a.list_name, a.stale, a.all])
    if not any_mode:
        a.by_list = True  # default util: privire de ansamblu

    lists, mem, all_tasks = collect(token, team, a.space)
    total = sum(len(ts) for _, ts in all_tasks)
    print("Citit %d liste, %d taskuri deschise, %d membri." % (len(lists), total, len(mem)))

    if a.all or a.by_list:
        report_by_list(all_tasks, mem, a.limit)
    if a.all or a.by_person:
        report_by_person(all_tasks, mem, a.limit)
    if a.all or a.overdue:
        report_overdue(all_tasks, mem, a.limit)
    if a.all or a.unassigned:
        report_unassigned(all_tasks, mem, a.limit)
    if a.all or a.junk:
        report_junk(all_tasks, mem, a.limit)
    if a.stale:
        report_stale(all_tasks, mem, a.limit, a.stale)
    if a.list_name:
        report_list_detail(all_tasks, mem, a.limit, a.list_name)


if __name__ == "__main__":
    main()
