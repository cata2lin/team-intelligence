# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Publish a team skill end-to-end: register -> branch -> commit -> push -> PR ->
merge -> sync main -> log. One command, no manual git/gh steps.

    uv run publish_skill.py --path plugins/<you>/skills/<name>
    uv run publish_skill.py --path plugins/<you>/skills/<name> --no-merge   # PR only, for review

Auth: reuses the GitHub credential already in the macOS keychain (the one `git
push` uses) — no `gh auth login` needed. Never prints the token.

Safety: this pushes and (by default) merges to `main`. Running it IS the
confirmation to publish. Use --no-merge when the change wants review first.
"""
import argparse, os, re, subprocess, sys, shutil

def run(cmd, cwd=None, env=None, check=True, quiet=False):
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if r.returncode != 0 and check:
        sys.exit(f"FAILED: {' '.join(cmd)}\n{r.stdout}\n{r.stderr}")
    if not quiet and r.stdout.strip():
        print(r.stdout.strip())
    return r

def gh_token(repo):
    """GitHub token. Team convention: secrets live in the SharedClaude DB, so try
    `kb.py secret-get GITHUB_TOKEN` first; fall back to the macOS keychain
    credential that `git push` uses. Never printed."""
    kb = kb_path(repo)
    if kb:
        for key in ("GITHUB_TOKEN", "GH_TOKEN"):
            v = subprocess.run(["uv", "run", kb, "secret-get", key],
                               capture_output=True, text=True).stdout.strip()
            if v:
                return v
    r = subprocess.run(["git", "credential", "fill"], cwd=repo, text=True,
                       input="protocol=https\nhost=github.com\n\n", capture_output=True)
    m = re.search(r"^password=(.+)$", r.stdout, re.M)
    return m.group(1) if m else None

def kb_path(repo):
    cand = os.path.join(repo, "plugins", "core", "scripts", "kb.py")
    return cand if os.path.exists(cand) else None

ap = argparse.ArgumentParser()
ap.add_argument("--path", required=True, help="skill dir, e.g. plugins/gigi/skills/shopify-seo")
ap.add_argument("--no-merge", action="store_true", help="open the PR but don't merge (review first)")
ap.add_argument("--base", default="main")
ap.add_argument("--description", default=None, help="override KB description (else read SKILL.md frontmatter)")
A = ap.parse_args()

if not shutil.which("gh"):
    sys.exit("gh CLI not found. Install once: brew install gh")

skill_path = os.path.abspath(A.path)
if not os.path.isfile(os.path.join(skill_path, "SKILL.md")):
    sys.exit(f"No SKILL.md in {skill_path}")
repo = run(["git", "-C", skill_path, "rev-parse", "--show-toplevel"], quiet=True).stdout.strip()
rel = os.path.relpath(skill_path, repo)

# derive author + name from plugins/<author>/skills/<name>
parts = rel.split(os.sep)
try:
    author = parts[parts.index("plugins") + 1]
    name = parts[parts.index("skills") + 1]
except (ValueError, IndexError):
    sys.exit(f"Path must look like plugins/<author>/skills/<name>; got {rel}")

# description from SKILL.md frontmatter if not given
desc = A.description
if not desc:
    fm = open(os.path.join(skill_path, "SKILL.md"), encoding="utf-8").read()
    m = re.search(r"^description:\s*(.+)$", fm, re.M)
    desc = (m.group(1).strip() if m else f"{name} skill")[:300]

print(f"== publishing {author}:{name} ==")

# 1) register in KB (idempotent-ish; ignore if it errors)
kb = kb_path(repo)
if kb:
    run(["uv", "run", kb, "skill-register", "--plugin", author, "--name", name,
         "--author", author, "--description", desc, "--path", skill_path], check=False)

# auth up front (needed to inspect PR state before we pick a branch)
import json
env = dict(os.environ)
tok = gh_token(repo)
if not tok:
    sys.exit("No GitHub token. Put GITHUB_TOKEN in the KB (kb.py secret-set GITHUB_TOKEN …) "
             "or do one `git push` first so it lands in the keychain.")
env["GH_TOKEN"] = tok

def pr_state(br):
    r = subprocess.run(["gh", "pr", "view", br, "--json", "state,number,url"],
                       cwd=repo, env=env, capture_output=True, text=True)
    return json.loads(r.stdout) if r.returncode == 0 else None

# 2) pick a branch — never reuse one whose PR is already MERGED/CLOSED (you can't
#    re-merge a closed PR, so a new commit there would silently never reach main)
base_branch = f"{author}/{name}-skill"
cur = run(["git", "-C", repo, "rev-parse", "--abbrev-ref", "HEAD"], quiet=True).stdout.strip()
if cur == A.base:
    branch_used, n = base_branch, 2
    while True:
        pr = pr_state(branch_used)
        if not pr or pr["state"] == "OPEN":
            break
        branch_used = f"{base_branch}-{n}"; n += 1
    run(["git", "-C", repo, "checkout", "-B", branch_used], quiet=True)
else:
    branch_used = cur  # already on a feature branch; publish from it

# 3) commit + push
run(["git", "-C", repo, "add", rel], quiet=True)
staged = run(["git", "-C", repo, "diff", "--cached", "--name-only"], quiet=True).stdout.strip()
if staged:
    msg = f"Add {author}:{name} skill\n\n{desc[:200]}\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
    run(["git", "-C", repo, "commit", "-q", "-m", msg], quiet=True)
    print(f"committed: {staged.splitlines()[0]} (+{len(staged.splitlines())-1} more)")
else:
    print("(nothing new to commit — publishing current branch state)")
run(["git", "-C", repo, "push", "-u", "origin", branch_used], quiet=True)
print(f"pushed branch {branch_used}")

# 4) PR — reuse only an OPEN one, else create
pr = pr_state(branch_used)
if pr and pr["state"] == "OPEN":
    pr_num, pr_url = pr["number"], pr["url"]
    print(f"reusing open PR #{pr_num}")
else:
    body = (f"Adds the `{author}:{name}` skill.\n\n{desc}\n\n"
            "🤖 Generated with [Claude Code](https://claude.com/claude-code)")
    r = run(["gh", "pr", "create", "--base", A.base, "--head", branch_used,
             "--title", f"Add {author}:{name} skill", "--body", body], cwd=repo, env=env, quiet=True)
    pr_url = r.stdout.strip().splitlines()[-1]
    pr_num = pr_url.rstrip("/").split("/")[-1]
print(f"PR: {pr_url}")

# 5) merge + sync + clean
if A.no_merge:
    print("--no-merge: PR left open for review. Done.")
else:
    run(["gh", "pr", "merge", str(pr_num), "--squash"], cwd=repo, env=env)
    run(["git", "-C", repo, "checkout", A.base], quiet=True)
    run(["git", "-C", repo, "pull", "--ff-only", "origin", A.base], quiet=True)
    run(["git", "-C", repo, "branch", "-D", branch_used], check=False, quiet=True)
    run(["git", "-C", repo, "push", "origin", "--delete", branch_used], check=False, quiet=True, env=env)
    print(f"MERGED into {A.base} + synced local, branch cleaned. Team gets {author}:{name} on next plugin update.")

# 6) log
if kb:
    action = "shared" if not A.no_merge else "pr_opened"
    run(["uv", "run", kb, "log", "--type", "skill", "--action", action, "--name", f"{author}:{name}",
         "--summary", f"Published {author}:{name} via publish-skill ({'merged' if not A.no_merge else 'PR open'})"],
        check=False, quiet=True)
