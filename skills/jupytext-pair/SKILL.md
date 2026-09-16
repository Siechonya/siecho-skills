---
name: jupytext-pair
description: Create, pair, edit, or sync Jupytext-paired .ipynb and .py notebook files. Use when the user mentions Jupytext, py:percent, percent-format notebooks, notebook sync, .ipynb to .py pairing, or when editing a .py file that is paired with an .ipynb and should be synchronized afterward.
---

# Jupytext Notebook Pairing and Sync

Keep `.ipynb` notebooks synchronized with plain `.py` files in `py:percent` format. Prefer
editing the `.py` file: it patches safely and reviews in diffs, while `.ipynb` JSON does not.

## Environment

Run Jupytext however you already have it. Three shapes work; pick one and use it
consistently for every command below.

```powershell
# 1. A dedicated conda environment (what the authoring setup used, env named "jupytext")
conda run -n jupytext jupytext <subcommand>

# 2. Any environment where jupytext is already importable
conda run -n <your-env> jupytext <subcommand>

# 3. Jupytext directly on PATH
jupytext <subcommand>
```

If Jupytext is not installed yet, install it into the environment you intend to use
(`pip install jupytext` inside that environment). Keep notebook sync in its own
environment if you like — nothing here depends on a particular name.

In the commands below, replace `JUPYTEXT` with whichever prefix you chose, e.g.
`conda run -n jupytext jupytext` or plain `jupytext`.

## Workflow

1. Pair a notebook that has no `.py` beside it:

```powershell
JUPYTEXT --set-formats ipynb,py:percent <file>.ipynb
```

This creates `<file>.py` and writes Jupytext pairing metadata into the notebook.

2. Before editing, pull notebook-side changes into the script:

```powershell
JUPYTEXT --sync <file>.ipynb
```

3. Edit `<file>.py`. Cell boundaries are `# %%` for code and `# %% [markdown]` for markdown.

4. Sync the script back:

```powershell
JUPYTEXT --sync <file>.py
```

If Jupytext says `--update` is needed to preserve outputs and cell IDs:

```powershell
JUPYTEXT --to ipynb --update --output <file>.ipynb <file>.py
```

5. Confirm the notebook is still valid JSON:

```powershell
python -c "import json; json.load(open('<file>.ipynb', encoding='utf-8')); print('OK')"
```

## Editing Rules

- Use context-rich patches on `.py`; include nearby lines so the target is unambiguous.
- Avoid broad replacements of short strings such as `[]`, `:`, `#`, `# %%`, `df`, or bare
  identifiers — `# %%` appears on every cell boundary.
- Keep edits inside the intended cell unless the user explicitly asks for broader changes.
- If an edit target is not found, reread the area: the paired file may have changed after a
  sync or a formatter run.
- Do not hand-edit `.ipynb` JSON for ordinary code changes when a paired `.py` exists. Sync
  first, patch the `.py`, sync back.

## Quick Reference

| Task | Command |
| --- | --- |
| First-time pairing | `JUPYTEXT --set-formats ipynb,py:percent <file>.ipynb` |
| Pull notebook to script | `JUPYTEXT --sync <file>.ipynb` |
| Push script to notebook | `JUPYTEXT --sync <file>.py` |
| Push preserving outputs and IDs | `JUPYTEXT --to ipynb --update --output <file>.ipynb <file>.py` |
| Force notebook from script | `JUPYTEXT --to ipynb <file>.py` |

## Feasibility Notes

- **Verified:** Windows PowerShell with a conda-provided Jupytext, driving `py:percent`
  pairs. The workflow itself is Jupytext's own and is not platform-specific.
- **Not verified:** macOS/Linux shells end to end; other pairing formats (`md`, `myscript`,
  `percent` variants other than `py:percent`); notebooks that are paired through a project
  `jupytext.toml` rather than per-file metadata. These should work, but nothing here was
  run against them — check before depending on them.
- The commands assume the paired `.py` sits next to the notebook and that both are named
  after the notebook. Other layouts need explicit paths.