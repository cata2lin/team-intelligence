---
name: watch-youtube
description: >
  "Watch" (read) a YouTube video by extracting its transcript and presenting
  structured knowledge — summary, steps, concepts, warnings, tips. Use when a
  user shares a YouTube URL, asks about a video/tutorial/talk, wants competitor
  demo videos studied, or says "watch/find videos about X". Can also search
  YouTube and (on request) save the learned knowledge as a new skill.
allowed-tools: Bash(uv:*) Bash(python:*) Read Write Edit Glob
license: MIT
compatibility: Team runtime = uv (PEP 723 inline deps — no pip install needed). Upstream MIT skill (mcpmarket watch-youtube 1.0.0), adapted to uv + UTF-8 stdout.
metadata:
  mcpmarket-version: 1.0.0
---
# Watch YouTube

Extract knowledge from YouTube videos and present it in the conversation.

> Team runtime: scripts declare their deps inline (PEP 723) and run with `uv run`.
> No `pip install` needed; the first run installs deps automatically.

## Quick Reference

| User Intent                       | Action                               |
| --------------------------------- | ------------------------------------ |
| Shares YouTube URL(s)             | Extract and present knowledge        |
| "search/find videos about X"      | Search → show results → user picks   |
| "watch videos about X"            | Search → watch top results           |
| "save as skill" / "remember this" | Save extracted knowledge as SKILL.md |

---

## Step 1: Get Video(s)

### Option A: User Provides URL(s)

Extract transcript from each URL:

```bash
uv run {{SKILL_DIR}}/scripts/transcript.py "VIDEO_URL"
```

### Option B: User Wants to Search

```bash
uv run {{SKILL_DIR}}/scripts/search.py "QUERY" --max-results 5
```

Then either:

- **"watch videos about X"** → Auto-select top 3 (or top 1 if "a video")
- **"search/find videos about X"** → Show list, let user choose

After selection, extract transcripts for chosen video(s).

---

## Step 2: Extract Knowledge

Analyze the transcript(s) and extract the knowledge (either to use for the next steps or present to the user based on the context):

1. **Summary** - What the video teaches (2-3 sentences)
2. **Key Steps** - Actionable instructions with commands/code
3. **Important Concepts** - Core ideas explained clearly
4. **Warnings** - Common pitfalls mentioned
5. **Tips** - Pro recommendations from the video

For multiple videos: combine insights, deduplicate overlapping concepts, note differing perspectives.

---

## Step 3: Save as Skill (Only When Requested)

**Default behavior: Use knowledge for the ongoing tasks, do not save files.**

Only save when user explicitly asks with phrases like:

- "save this as a skill"
- "remember this for later"
- "make it a skill"

### To Save:

1. Read the extraction prompt: [references/extract-knowledge.md](references/extract-knowledge.md)

2. Read the appropriate template:
   - Single video: [assets/templates/skill-single.md](assets/templates/skill-single.md)
   - Multiple videos: [assets/templates/skill-series.md](assets/templates/skill-series.md)

3. Ask where to save:
   - **Project** (default): `.claude/skills/{skill-name}/SKILL.md`
   - **Personal**: `~/.claude/skills/{skill-name}/SKILL.md`

4. Save and confirm with skill name and how to invoke it (`/skill-name`)

5. Remind the user that the skill can only be loaded when Claude Code is reloaded

---

## Error Handling

| Error                     | Solution                  |
| ------------------------- | ------------------------- |
| "No transcript available" | Suggest alternative video |
| "Video not found"         | Ask user to verify URL    |
| "Module not found"        | Re-run with `uv run` (it installs inline deps automatically) |

### Setup

No setup needed. Deps are declared inline (PEP 723) and `uv run` installs them on
first use. If `uv` is somehow missing, re-run the team installer (`install.sh`).
