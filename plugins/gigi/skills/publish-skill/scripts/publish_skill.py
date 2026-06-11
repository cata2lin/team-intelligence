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
    fm = open(os.path.join(skill_path, "SKILL.md")).read()
    m = re.search(r"^description:\s*(.+)$", fm, re.M)
    desc = (m.group(1).strip() if m else f"{name} skill")[:300]

print(f"== publishing {author}:{name} ==")

# 1) register in KB (idempotent-ish; ignore if it errors)
kb = kb_path(repo)
if kb:
    run(["uv", "run", kb, "skill-register", "--plugin", author, "--name", name,
         "--author", author, "--description", desc, "--path", skill_path], check=False)

# 2) git branch + commit
branch = f"{author}/{name}-skill"
cur = run(["git", "-C", repo, "rev-parse", "--abbrev-ref", "HEAD"], quiet=True).stdout.strip()
if cur == A.base:
    run(["git", "-C", repo, "checkout", "-B", branch], quiet=True)
    branch_used = branch
else:
    branch_used = cur  # already on a feature branch; publish from it
run(["git", "-C", repo, "add", rel], quiet=True)
staged = run(["git", "-C", repo, "diff", "--cached", "--name-only"], quiet=True).stdout.strip()
if staged:
    msg = f"Add {author}:{name} skill\n\n{desc[:200]}\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
    run(["git", "-C", repo, "commit", "-q", "-m", msg], quiet=True)
    print(f"committed: {staged.splitlines()[0]} (+{len(staged.splitlines())-1} more)" if staged else "")
else:
    print("(nothing new to commit — publishing current branch state)")

# 3) push
run(["git", "-C", repo, "push", "-u", "origin", branch_used], quiet=True)
print(f"pushed branch {branch_used}")

env = dict(os.environ)
tok = gh_token(repo)
if not tok:
    sys.exit("No github.com credential in keychain. Do one `git push` first (it stores it), then re-run.")
env["GH_TOKEN"] = tok

# 4) PR (reuse if one already exists for the branch)
existing = subprocess.run(["gh", "pr", "view", branch_used, "--json", "url,number"],
                          cwd=repo, env=env, capture_output=True, text=True)
if existing.returncode == 0:
    import json; pr = json.loads(existing.stdout); pr_num, pr_url = pr["number"], pr["url"]
    print(f"reusing PR #{pr_num}")
else:
    body = (f"Adds the `{author}:{name}` skill.\n\n{desc}\n\n"
            "🤖 Generated with [Claude Code](https://claude.com/claude-code)")
    r = run(["gh", "pr", "create", "--base", A.base, "--head", branch_used,
             "--title", f"Add {author}:{name} skill", "--body", body], cwd=repo, env=env, quiet=True)
    pr_url = r.stdout.strip().splitlines()[-1]
    pr_num = pr_url.rstrip("/").split("/")[-1]
print(f"PR: {pr_url}")

# 5) merge + sync
if A.no_merge:
    print("--no-merge: PR left open for review. Done.")
else:
    run(["gh", "pr", "merge", str(pr_num), "--squash"], cwd=repo, env=env)
    run(["git", "-C", repo, "checkout", A.base], quiet=True)
    run(["git", "-C", repo, "pull", "--ff-only", "origin", A.base], quiet=True)
    run(["git", "-C", repo, "branch", "-D", branch_used], check=False, quiet=True)
    print(f"MERGED into {A.base} + synced local. Team gets {author}:{name} on next plugin update.")

# 6) log
if kb:
    action = "shared" if not A.no_merge else "pr_opened"
    run(["uv", "run", kb, "log", "--type", "skill", "--action", action, "--name", f"{author}:{name}",
         "--summary", f"Published {author}:{name} via publish-skill ({'merged' if not A.no_merge else 'PR open'})"],
        check=False, quiet=True)
