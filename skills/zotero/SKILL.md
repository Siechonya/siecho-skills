---
name: zotero
description: Search and read a local Zotero reference library, produce citation drafts, and convert Zotero-held PDFs into MinerU markdown with a heuristic quality check. Use when the task concerns the user's own Zotero library or the PDFs stored in it - their saved papers, references, bibliography, literature review over their collection, paper metadata, authors, abstracts, PDF full-text search, notes, annotations, or citation/reference export from it - or when the user explicitly asks to convert a Zotero paper's PDF to markdown, find or create MinerU markdown for a Zotero paper, batch-convert Zotero PDFs, or quality-check existing MinerU output. Do not use it for generic PDF-to-Markdown conversion of files that are not in Zotero, and do not use it to format references the user has not stored in Zotero.
---

# Zotero Library Integration

Read a local Zotero library through the bundled Python bridge, and turn Zotero PDFs into
checked MinerU markdown with the second bundled script.

The bridge handles Zotero's normalized schema, creator joins, field resolution, Unicode
output, `storage:` attachment paths, and PDF full-text indexes.

## Requirements

Set the two required variables before running anything; the bridge reads them at import.

| Variable | Required | Meaning |
| --- | --- | --- |
| `ZOTERO_DB` | yes | Full path to `zotero.sqlite` |
| `ZOTERO_STORAGE` | recommended | Full path to the Zotero `storage` directory (resolves `storage:` attachment paths) |
| `MINERU_OUTPUT_DIR` | for MinerU | Full path to the MinerU markdown output directory |
| `ZOTERO_VAULT_ROOT` | optional | Library root; MinerU output defaults to `<root>/docs/mineru_output` |

If `ZOTERO_DB` is unset the bridge tries the usual Zotero data-directory locations and then
exits with a message telling you to set it. When Zotero's own auto-detection does not match
your layout, set the variable explicitly — that is the supported path.

```powershell
$env:PYTHONIOENCODING = "utf-8"
$env:ZOTERO_DB = "C:\Users\<you>\Zotero\zotero.sqlite"
$env:ZOTERO_STORAGE = "C:\Users\<you>\Zotero\storage"
python "<SKILL_DIR>/scripts/zotero_bridge.py" stats
```

On PowerShell, always set `PYTHONIOENCODING=utf-8` so non-ASCII titles survive.

## Bridge Script

```text
<SKILL_DIR>/scripts/zotero_bridge.py
```

Replace `<SKILL_DIR>` with the absolute path to this skill folder. Do not query the Zotero
SQLite database directly unless the bridge cannot support the task.

## Commands

| Command | Purpose |
| --- | --- |
| `search "<query>"` | Search title and abstract; ranked papers with itemID, title, authors, year, snippets. |
| `fulltext-search "<query>"` | Search Zotero's indexed PDF full text. |
| `search-by-author "<name>"` | Find papers by author first or last name. |
| `get-paper <id>` | Full metadata, collections, attachments, notes, annotations. |
| `list-collections` | List collections with paper counts. |
| `list-papers [--collection <id>] [--limit N] [--offset N]` | Browse papers. |
| `find-pdf <id>` | Absolute path to a paper's PDF. |
| `get-attachments <id>` | All attachments for a paper. |
| `stats` | Library counts, years, item types, top authors, collections. |
| `recent [--days N]` | Recently added or modified papers. |
| `notes <id>` / `annotations <id>` | Zotero notes / PDF annotations. |
| `cite <id>` | Citation **draft** only: mapped BibTeX entry type and a full author list, but not publication-grade. |
| `export <id> [--format json\|csl]` | Structured metadata export. |
| `mineru-find <id>` | Look up a paper's MinerU markdown by recorded provenance. |
| `mineru-list` | List MinerU output directories, split into provenance matches and heuristic candidates. |
| `mineru-adopt <id> <dirname>` | Record provenance for markdown created before provenance existed. |

## MinerU Markdown Creation

Use `<SKILL_DIR>/scripts/mineru_create_md.py` when `mineru-find <id>` finds nothing, or
when the user asks to convert one of their Zotero PDFs to markdown.

The script shells out to the **MinerU CLI** and then quality-checks the markdown it
produced. Modes:

| Mode | Use for |
| --- | --- |
| `txt` | Modern text PDFs; fastest, usually best for recent papers. |
| `ocr` | Scanned and old papers; slower, fewer missing/joined words. |
| `auto` | Unknown PDF type; let MinerU choose. |

```powershell
python "<SKILL_DIR>/scripts/mineru_create_md.py" "<pdf-path>" --method auto --overwrite
```

Result lands in `--output-root` (default `$MINERU_OUTPUT_DIR`, else
`$ZOTERO_VAULT_ROOT/docs/mineru_output`, else `<cwd>/docs/mineru_output`) as
`<paper-name>/` with the markdown and its `images/` folder.

Convert an existing markdown without re-running MinerU:

```powershell
python "<SKILL_DIR>/scripts/mineru_create_md.py" --check-md "<markdown-path>"
```

Batch-convert a directory:

```powershell
python "<SKILL_DIR>/scripts/mineru_create_md.py" --batch "<directory-of-pdfs>" --method auto --overwrite
```

`--pattern "**/*.pdf"` recurses. Batch mode is deliberately sequential — exactly one MinerU
process per PDF — so concurrent jobs cannot contend for GPU memory. Output is a JSON
summary with per-file `pass`, `warn`, `fail`, or `error`. It stops at the first failure
unless `--continue-on-error` is set.

Quality verdicts, read literally: `pass` means none of the checks found a problem — not that
formulas, tables and page completeness were verified. `warn` means inspect `sampleBadContexts`
before relying on it. `fail` means do not use it; rerun with a different `--method`.

### Matching a paper to its markdown

`mineru-find` is exact by default: it hashes the paper's PDF and looks for a
`.mineru-provenance.json` written by `mineru_create_md.py`, so the answer is the markdown that
came from that file. Markdown converted before provenance existed has none, so it is reported
under `candidates` instead — ranked by directory-name similarity — and must be confirmed by
reading it before anything from it is quoted. Record an existing directory explicitly with
`mineru-adopt <itemID> <dirname>`, which is the point where a human asserts the pairing.
A shared year is deliberately not enough to count as a match.

### How MinerU is invoked (and what you must provide)

The script calls `mineru` and expects **MinerU installed locally as a CLI**. It does
**not** talk to a hosted MinerU API — there is no API path in this skill. Relevant knobs:

| Flag | Default | Notes |
| --- | --- | --- |
| `--conda-env` | unset → call `mineru` directly from PATH | Set it to run through `conda run -n <env> mineru` |
| `--mineru-cmd` | `mineru` (or `$MINERU_CMD`) | Use when the CLI has another name or path |
| `--model-source` | unset → MinerU's own default | `MINERU_MODEL_SOURCE`; the authoring setup used `local` with pre-downloaded models |
| `--hf-home`, `--modelscope-cache`, `--torch-home` | unset → MinerU's own defaults | Only exported when you pass them |
| `--virtual-vram-gb` | unset → MinerU's own default | `MINERU_VIRTUAL_VRAM_SIZE`; cap pipeline batch sizing on small GPUs |
| `--backend` | `pipeline` | `pipeline`, `vlm-engine`, `hybrid-engine` |

If `mineru` is missing, the conversion fails with the captured stderr rather than a silent
result — install MinerU first, or skip the conversion commands and use the bridge alone.

## Common Workflows

- "Search my Zotero for X" → `search "X"`, then show a concise numbered list with itemIDs.
- "Search inside my PDFs for X" → `fulltext-search "X"`; title/abstract search is not enough.
- Author questions → `search-by-author "<name>"`.
- Details about paper `N` → `get-paper N`; summarize title, authors, year, venue, DOI,
  abstract, PDF availability, notes, annotations.
- Citations → `cite N`, present APA and BibTeX.
- Library overview → `stats`, then drill into `list-collections` / `list-papers` / `search`.
- Collection questions → `list-collections` for the ID, then `list-papers --collection <id>`.
- Literature review over the user's own collection → combine `search` and `fulltext-search`,
  then `get-paper` and `cite` for the most relevant papers.
- Deep reading → `mineru-find N` and read the markdown. If none, `find-pdf N`, convert it,
  inspect the quality verdict, then `mineru-find N` again.
- "Convert this PDF to markdown" → run `mineru_create_md.py` with the PDF path, inspect the
  verdict, rerun with `--method ocr` or `--method txt` if it failed.
- "Batch convert folder X" → `--batch`, then review the JSON summary and call out every
  `warn`, `fail`, and `error`.

## Reporting Rules

- Always include itemIDs when mentioning papers.
- Present bridge output as tables or lists, not raw JSON dumps.
- Batch broad discovery before deep `get-paper` calls.
- Prefer `fulltext-search` for methods, equations, findings, or concepts that appear only
  in paper bodies.
- If a command finds nothing, report the exact command and the bridge error/absence.
- For deep reading, prefer `mineru-find` plus reading the markdown over raw PDF.
- Always check the quality verdict before presenting MinerU content; never summarize a
  `fail` result as if it were reliable.

## Feasibility Notes

Written from a setup that was actually exercised; do not assume more than that.

- **Verified:** Windows, Zotero desktop with a local SQLite library, Python 3, MinerU as a
  local CLI, and a single NVIDIA GPU for the `pipeline` backend.
- **Not verified:** macOS and Linux end to end; CPU-only MinerU; `vlm-engine` and
  `hybrid-engine` backends; MinerU versions other than 3.4.x. The path-resolution code has
  POSIX fallbacks, but nothing beyond Windows was run. Verify on your platform before
  relying on it, and expect to adjust paths (or the script) rather than assume parity.
- **Not implemented:** a hosted MinerU API path, and any cloud/OCR service. Adding one is a
  real change to `mineru_create_md.py`, not a configuration flag.
- The quality checker is heuristic (character counts, mojibake markers, long-token ratios).
  It flags suspicious output; it does not prove correctness.