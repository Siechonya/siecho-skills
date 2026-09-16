---
name: skill-management
description: Set up and maintain one canonical skill directory shared by several coding agents (Claude Code, Codex, OpenCode, DSH, Qoder, and similar). Use when the user wants to add, edit, rename, remove, install, or sync a skill across agents; when a skill is duplicated, missing, or stale in one agent; when skills live in several diverging copies; or when the user says "unified skills", "skill source of truth", "share skills between agents", or "sync my skills".
---

# Skill Management Across Agents

Coding agents each read skills from their own directory. Left alone, you end up with the
same skill copied into four places, drifting apart, until nobody knows which copy is real.

The fix is one real directory plus per-agent *links* into it, so a skill is edited once and
every agent sees the change.

## 1. Pick one canonical directory

Any path works. A shared convention is friendlier to agents that already read it:

```text
~/.agents/skills/<skill-name>/SKILL.md
```

Put every shared skill there and nowhere else. Per-skill layout:

```text
<canonical>/<skill-name>/SKILL.md     # frontmatter: name + description (both required)
```

Other files in the folder (`scripts/`, `references/`, assets) are yours to use; only
`SKILL.md` is the contract.

## 2. Find where each agent reads skills

Typical locations. **Confirm against your agent's version before trusting this table** —
these move between releases, and only the first two were verified for this skill.

| Agent | Skills directory | Notes |
| --- | --- | --- |
| Any agent following the shared convention | `~/.agents/skills` | Read natively by some agents |
| Claude Code | `~/.claude/skills` | Also reads `~/.agents/skills` in recent versions |
| Codex | `~/.codex/skills` | Ships its own `.system/` skills inside it |
| OpenCode | `~/.config/opencode/skill(s)` | Config key `skills.paths` can add more |
| DSH | `~/.dsh/skills` and `~/.agents/skills` | Scanned together, so do not populate both (see below) |
| Qoder | `~/.qoder-cn/skills` | |

Search for the real thing rather than guessing:

```powershell
Get-ChildItem $HOME -Recurse -Depth 4 -Directory -Filter skills -ErrorAction SilentlyContinue
```

## 3. Wire each entry to the canonical directory

Three strategies. Pick per agent based on whether it owns skills of its own.

**a. Native mount — nothing to do.** Some agents already read the canonical path. Pointing
them at it twice causes duplicate-catalog warnings, so do not also link them.

**b. Whole-directory link.** For agents with no skills of their own: delete the real
directory and replace it with a link to the canonical one. New skills then appear with no
further work.

```cmd
rmdir "C:\Users\<you>\.someagent\skills"
mklink /J "C:\Users\<you>\.someagent\skills" "C:\Users\<you>\.agents\skills"
```

**c. Per-skill links.** For agents that keep their own skills (Codex `.system`, or a
Claude-only skill): leave the directory real and link each canonical skill into it. New
skills need a re-run of the sync step.

Either way, the link target is the canonical folder — so **the same skill must not be
reachable through two different entry directories on one agent**. Where that happens (DSH
scans `~/.dsh/skills` and `~/.agents/skills` in one pass), leave one side empty or the agent
will collect every skill twice and warn about it.

## 4. Keep it in sync

`scripts/sync-skills.ps1` is a template that reconciles per-skill-link roots with the
canonical directory. Edit its CONFIG block (canonical path, whole-link roots, per-skill
roots, and each agent's `Keep` list of private skills), then:

```powershell
pwsh -File scripts/sync-skills.ps1 -DryRun   # preview
pwsh -File scripts/sync-skills.ps1           # apply
```

It creates missing links, repoints wrong ones, and removes links whose skill was deleted. It
**never deletes a real directory**: anything real that it does not recognise is reported and
left alone. The script uses Windows junctions (`mklink /J`, no admin needed). On macOS/Linux
use `ln -s` by hand — the script refuses to run there, and its logic is untested off Windows.

## Rules worth enforcing

1. **Edit only the canonical directory.** Never author a new skill inside an agent's entry
   folder: it will not be shared, and it shadows the canonical one.
2. **Re-run the sync step after adding, renaming, or deleting a skill.** Whole-link and
   native-mount agents pick it up for free; per-skill-link agents do not.
3. **No real directories inside an entry folder.** They silently decouple from the
   canonical source and produce two same-named skills.
4. **Keep each agent's private skills on its own side**, listed in the script's `Keep`. Do
   not copy them into the canonical directory: they are agent-specific, and some ship with
   the agent itself (Codex `.system/`, Claude plugin skills). If a name appears in both the
   canonical set and `Keep`, the script reports the collision and leaves the entry alone —
   that is ambiguous ownership, and renaming one side is the only clean fix.
5. **`name` should match the folder name.** Some agents key the skill by frontmatter `name`,
   others by folder; matching satisfies both. A deliberate exception used as a compatibility
   trigger is fine — document it.
6. **Restart the agent after changing skills.** Skills load at startup; they are not
   hot-reloaded.
7. **Back up before the first switch.** You are replacing directories with links; a copy
   makes any surprise reversible.

## Verification

```powershell
# the same file through every entry
Get-FileHash "$HOME\.agents\skills\my-skill\SKILL.md",
              "$HOME\.codex\skills\my-skill\SKILL.md" |
  Select-Object Path, Hash

# one entry appears to be a link, not a copy
Get-Item "$HOME\.codex\skills\my-skill" | Select-Object Name, LinkType, Target

# OpenCode's own view of what it loaded
opencode debug skill
```

## Feasibility Notes

- **Verified:** Windows, directory junctions (no administrator rights needed), and
  PowerShell 7 for the sync script; the canonical-plus-links layout was exercised with
  several agents on one machine.
- **Not verified:** macOS and Linux end to end (symlink behaviour, agent paths, the sync
  script); Windows without `cmd.exe` available to PowerShell; agents not listed above; and
  any agent version other than the ones tested. The table of entry paths is a starting
  point, not a guarantee — check your own install.
- **Windows scripts and UTF-8:** the sync script must be saved as UTF-8 **with BOM**. Without
  a BOM, PowerShell reads non-ASCII comments as ANSI and fails to parse the file.
- **Not covered:** enforcing the convention. A skill only runs when a model chooses to read
  it. To make the rule binding, put a one-line pointer to this skill in each agent's
  always-loaded instruction file (`AGENTS.md`, `CLAUDE.md`, or an equivalent).