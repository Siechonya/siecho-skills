# siecho-skills

A small collection of [agent skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)
that I use daily across Claude Code, Codex, OpenCode, DSH and Qoder. Each skill is a folder
with a `SKILL.md` (YAML frontmatter + instructions) plus any scripts and references it needs.

They are written for the generic case: nothing here assumes my machine, my paths, or my
hardware. Where a capability depends on software you must install yourself, the skill says
so explicitly.

## The skills

### `zotero`

Search and read a local Zotero library, generate citations, and convert Zotero PDFs into
**quality-checked MinerU markdown**.

- Reads Zotero's SQLite database directly through a bundled Python bridge: title/abstract
  search, PDF full-text search, author search, collections, metadata, notes, annotations,
  attachment and PDF lookup, APA/BibTeX citations, structured export.
- Converts PDFs to markdown via the **MinerU CLI** — single file or batch — and grades each
  result `pass` / `warn` / `fail` so bad OCR never gets summarized as if it were sound.
- Lookups do not guess. Each conversion records the source PDF's SHA-256, and a markdown is
  only reported as a match when that record exists **and** the file is still there; anything
  else comes back separately as an unverified candidate or an unlinked record.
- **Requires** a local Zotero library, Python 3, and a local MinerU installation. There is
  **no hosted-API path** in this skill. Paths come from environment variables
  (`ZOTERO_DB`, `ZOTERO_STORAGE`, `MINERU_OUTPUT_DIR`), and the MinerU call is configurable
  (conda env or plain CLI, model source, GPU knobs).

### `jupytext-pair`

Keep `.ipynb` notebooks in sync with paired `.py` files in `py:percent` format via Jupytext,
so notebooks can be patched and reviewed like ordinary source.

- Pairing, pulling notebook changes into the script, syncing the script back with output and
  cell-ID preservation, and validating the notebook as JSON.
- **Requires** Jupytext, in a conda environment or on `PATH` — the commands show both.

### `skill-management`

Set up and maintain **one canonical skill directory shared by several agents**, so a skill is
written once and every agent sees it. Includes `scripts/sync-skills.ps1`, a reconciler that
creates and repoints links and cleans up dead ones without ever deleting a real directory.

- Explains the layout, the three wiring strategies (native mount / whole-directory link /
  per-skill link), the rules that keep copies from drifting, and how to verify.
- **Windows-verified** (directory junctions, no admin rights). macOS/Linux are explicitly
  marked untested.

## Status

The instructions are the usable part; treat the scripts as young.

- The three `SKILL.md` files are the deliberately small surface: they say which workflow to run
  and when, and they state their own limits.
- The `zotero` scripts were reviewed after the first release and several real defects were
  fixed: quality verdicts could soften a failure, `storage:` paths missed the attachment key,
  and a bare same-year match could be reported as a confident answer. Regression tests now
  cover those cases.
- That chain has still **not** been exercised end to end on a Zotero + MinerU installation
  other than the author's. Read each skill's *Feasibility Notes* before depending on it, and
  do not treat script output as authoritative for anything you would have to defend.

## Testing

```bash
python -m unittest discover -s tests -v     # stdlib only; no Zotero or MinerU required
```

Covers the failure modes that have actually occurred: quality-verdict escalation, MinerU match
scoring (a bare year must not match), `storage:` path resolution under the attachment key,
batch argument propagation, BibTeX author/entry-type handling, and the skill-sync script
leaving a private skill alone when its name collides with the canonical set. Provenance
lookups and listings run against a minimal Zotero-shaped SQLite fixture, so the "a record is
not a match" and "an empty library matches nothing" cases are exercised directly.

Each of these tests was checked against the previous release: they fail on the code that still
had the bug, rather than passing on both.

Point the suite at another checkout with `ZOTERO_SCRIPTS_DIR` / `SYNC_SKILLS_SCRIPT`.

## Install

Skills are plain folders — "installing" one means putting it where your agent looks for
skills.

### Easiest: hand this repo to your agent

Paste this into Claude Code, Codex, OpenCode, or any agent that can read and write files:

> Clone `https://github.com/Siechonya/siecho-skills` into a directory of your choice, read
> each skill's `SKILL.md`, and install the ones I ask for into this agent's skills directory
> in the format this agent expects. Before installing `zotero`, tell me what it needs and
> which environment variables I have to set. Do not invent capabilities the skill does not
> have.

If you already use a shared skills directory across agents, also point out
`skill-management` — it is the one that explains how to keep them in sync.

### Manual

```bash
git clone https://github.com/Siechonya/siecho-skills
```

Then copy the skill folders you want into your agent's skills directory:

| Agent | Skills directory |
| --- | --- |
| Claude Code | `~/.claude/skills/` |
| Codex | `~/.codex/skills/` |
| OpenCode | `~/.config/opencode/skills/` (or add to `skills.paths` in the config) |
| DSH | `~/.agents/skills/` or `~/.dsh/skills/` |
| Qoder | `~/.qoder-cn/skills/` |
| Shared convention | `~/.agents/skills/` |

```bash
cp -r siecho-skills/skills/zotero ~/.claude/skills/
```

Restart the agent afterwards — skills are loaded at startup, not hot-reloaded.

## Notes before you install

- **`zotero` needs setup.** Set `ZOTERO_DB` (and usually `ZOTERO_STORAGE` /
  `MINERU_OUTPUT_DIR`) and have MinerU installed locally, or the conversion commands will
  fail. The bridge alone works without MinerU. Read that skill's *Requirements* and
  *Feasibility Notes* first.
- **Tested where?** Windows, on the versions noted in each skill's *Feasibility Notes*.
  Everything else is marked untested rather than claimed to work. Verify on your platform
  and adjust — the notes say exactly which parts are guesses.
- **No secrets.** No tokens, credentials, or private paths are included; anything
  machine-specific is an environment variable or a placeholder.

## License

MIT — see [LICENSE](LICENSE). Change it if you want something else.