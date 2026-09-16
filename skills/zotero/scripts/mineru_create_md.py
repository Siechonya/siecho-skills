#!/usr/bin/env python3
"""Create MinerU markdown for a Zotero PDF and check markdown readability."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def _env_path(name: str, default):
    """Read a path from the environment, falling back to a neutral default."""
    value = os.environ.get(name)
    return Path(value) if value else default


# Layout is caller-supplied. Nothing here is tied to the author's machine:
# --output-root / --stage-root override the two roots, and the model-cache
# directories stay unset so MinerU's own defaults apply unless you export
# HF_HOME / MODELSCOPE_CACHE / TORCH_HOME yourself.
DEFAULT_VAULT_ROOT = _env_path("ZOTERO_VAULT_ROOT", Path.cwd())
DEFAULT_OUTPUT_ROOT = _env_path("MINERU_OUTPUT_DIR", DEFAULT_VAULT_ROOT / "docs" / "mineru_output")
DEFAULT_STAGE_ROOT = _env_path("MINERU_STAGE_DIR", DEFAULT_VAULT_ROOT / "_mineru_stage")
DEFAULT_HF_HOME = _env_path("HF_HOME", None)
DEFAULT_MODELSCOPE_CACHE = _env_path("MODELSCOPE_CACHE", None)
DEFAULT_TORCH_HOME = _env_path("TORCH_HOME", None)
DEFAULT_CONDA_ENV = os.environ.get("MINERU_CONDA_ENV", "")
DEFAULT_MODEL_SOURCE = os.environ.get("MINERU_MODEL_SOURCE")
DEFAULT_MINERU_EXE = os.environ.get("MINERU_CMD", "mineru")
DEFAULT_VIRTUAL_VRAM_GB = None

MOJIBAKE_PATTERNS = [
    "\ufffd",  # replacement character
    "\u951f",  # common Chinese mojibake marker
    "\u951b",
    "\u923b",
    "\u9583",
    "\u9286",
    "\u00e2\u20ac",
    "\u00c3",
]


def sanitize_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]+', " ", name)
    name = re.sub(r"\s+", " ", name).strip().rstrip(".")
    return name or "paper"


def quality_check(md_path: Path) -> dict:
    text = md_path.read_text(encoding="utf-8", errors="replace")
    chars = len(text)
    letters = sum(ch.isalpha() for ch in text)
    ascii_printable = sum(32 <= ord(ch) < 127 for ch in text)
    replacement = text.count("\ufffd")
    mojibake_hits = sum(text.count(pattern) for pattern in MOJIBAKE_PATTERNS)
    alpha_ratio = letters / chars if chars else 0.0
    ascii_ratio = ascii_printable / chars if chars else 0.0
    mojibake_ratio = mojibake_hits / chars if chars else 0.0
    words = re.findall(r"[A-Za-z]+", text)
    long_words = [word for word in words if len(word) >= 16]
    long_word_ratio = len(long_words) / len(words) if words else 0.0

    contexts = []
    pattern_re = re.compile("|".join(re.escape(p) for p in MOJIBAKE_PATTERNS if p))
    for match in pattern_re.finditer(text):
        start = max(0, match.start() - 40)
        end = min(len(text), match.end() + 80)
        contexts.append(text[start:end].replace("\n", "\\n"))
        if len(contexts) >= 5:
            break

    # Verdicts only ever escalate: pass -> warn -> fail. A later check must never
    # soften an earlier one (a 100-char stub with one mojibake rune is still a fail).
    severity = {"pass": 0, "warn": 1, "fail": 2}
    status = "pass"
    reasons = []

    def escalate(level, reason):
        nonlocal status
        reasons.append(reason)
        if severity[level] > severity[status]:
            status = level

    if chars < 200:
        escalate("fail", "markdown is too short")
    if replacement > 0 or mojibake_ratio > 0.02:
        escalate("fail", "too many replacement/mojibake characters")
    elif mojibake_hits > 0:
        escalate("warn", "possible OCR or mojibake artifacts found")
    if long_word_ratio > 0.02:
        escalate("warn", "many unusually long alphabetic tokens; OCR may have joined adjacent words")
    if chars >= 500 and alpha_ratio < 0.20:
        escalate("fail", "low alphabetic text ratio")

    return {
        "path": str(md_path),
        "status": status,
        "reasons": reasons,
        "chars": chars,
        "lines": text.count("\n") + 1 if text else 0,
        "letters": letters,
        "alphaRatio": round(alpha_ratio, 4),
        "asciiPrintableRatio": round(ascii_ratio, 4),
        "replacementChars": replacement,
        "mojibakeHits": mojibake_hits,
        "longAlphabeticTokenRatio": round(long_word_ratio, 4),
        "sampleLongTokens": long_words[:10],
        "sampleBadContexts": contexts,
    }


def run_mineru(args: argparse.Namespace, stage_dir: Path, pdf_path: Path | None = None) -> None:
    pdf = pdf_path if pdf_path is not None else args.pdf
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    # Exported only when the caller asked for them; otherwise MinerU's own
    # defaults for the model caches stay in force.
    if args.hf_home:
        env["HF_HOME"] = str(args.hf_home)
    if args.modelscope_cache:
        env["MODELSCOPE_CACHE"] = str(args.modelscope_cache)
    if args.torch_home:
        env["TORCH_HOME"] = str(args.torch_home)
    if args.model_source:
        env["MINERU_MODEL_SOURCE"] = args.model_source
    if args.virtual_vram_gb:
        env["MINERU_VIRTUAL_VRAM_SIZE"] = str(args.virtual_vram_gb)

    conda_env = args.conda_env or DEFAULT_CONDA_ENV
    mineru_exe = args.mineru_cmd or DEFAULT_MINERU_EXE

    # conda is optional: with no environment name, call the MinerU CLI directly
    # from whatever environment is on PATH.
    if conda_env:
        command = ["conda", "run", "-n", conda_env, mineru_exe]
    else:
        command = [mineru_exe]
    command += [
        "-p",
        str(pdf),
        "-o",
        str(stage_dir),
        "-m",
        args.method,
    ]
    command.extend(["-b", args.backend])
    if args.lang:
        command.extend(["-l", args.lang])
    if args.start is not None:
        command.extend(["-s", str(args.start)])
    if args.end is not None:
        command.extend(["-e", str(args.end)])

    result = subprocess.run(
        command,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            json.dumps(
                {
                    "error": "mineru failed",
                    "returncode": result.returncode,
                    "command": command,
                    "stdout": result.stdout[-4000:],
                    "stderr": result.stderr[-4000:],
                },
                ensure_ascii=False,
                indent=2,
            )
        )


def find_generated_md(stage_dir: Path) -> Path:
    candidates = sorted(stage_dir.rglob("*.md"), key=lambda p: p.stat().st_size, reverse=True)
    if not candidates:
        raise FileNotFoundError(f"No markdown file generated under {stage_dir}")
    return candidates[0]


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    """Streaming SHA-256 of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(chunk_size), b""):
            digest.update(block)
    return digest.hexdigest()


def write_provenance(target_md: Path, pdf_path: Path, args: argparse.Namespace, quality: dict) -> Path:
    """Record which PDF produced this markdown, so lookups can be exact.

    Zotero's bridge matches a paper to its markdown by this record first; without
    it the only thing left is guessing from directory names, which produced false
    positives. Keep the file next to the markdown.
    """
    record = {
        "schema": 1,
        "pdf": {
            "path": str(pdf_path),
            "filename": pdf_path.name,
            "sizeBytes": pdf_path.stat().st_size,
            "sha256": sha256_file(pdf_path),
        },
        "markdown": {"file": target_md.name, "qualityStatus": quality.get("status")},
        "mineru": {
            "method": args.method,
            "backend": args.backend,
            "condaEnv": args.conda_env or None,
            "modelSource": args.model_source or None,
        },
        "generatedAtUtc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    provenance = target_md.parent / ".mineru-provenance.json"
    provenance.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return provenance


def copy_output(generated_md: Path, output_root: Path, paper_name: str, overwrite: bool) -> Path:
    existing_exact_dir = output_root / paper_name
    target_dir = existing_exact_dir if existing_exact_dir.is_dir() else output_root / sanitize_name(paper_name)
    if target_dir.exists() and not overwrite:
        raise FileExistsError(f"Target already exists; use --overwrite: {target_dir}")
    target_dir.mkdir(parents=True, exist_ok=True)

    target_md = target_dir / generated_md.name
    shutil.copy2(generated_md, target_md)

    images_dir = generated_md.parent / "images"
    if images_dir.is_dir():
        target_images = target_dir / "images"
        target_images.mkdir(exist_ok=True)
        for image in images_dir.iterdir():
            if image.is_file():
                shutil.copy2(image, target_images / image.name)

    return target_md


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run MinerU 3.x and copy checked markdown into the vault."
    )
    parser.add_argument("pdf", nargs="?", type=Path, help="PDF file to convert")
    parser.add_argument("--check-md", type=Path, help="Only quality-check an existing markdown file")
    parser.add_argument("--batch", type=Path, help="Batch convert all PDFs in a directory")
    parser.add_argument("--pattern", default="*.pdf", help="Glob pattern for --batch (default: *.pdf)")
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help=("Keep going after a conversion error instead of stopping the batch. A 'fail' "
              "quality verdict does not stop the batch either way; it is recorded per file."),
    )
    parser.add_argument("--method", choices=["auto", "txt", "ocr"], default="auto")
    parser.add_argument("--paper-name", help="Output folder name under docs/mineru_output")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--stage-root", type=Path, default=DEFAULT_STAGE_ROOT)
    parser.add_argument("--hf-home", type=Path, default=DEFAULT_HF_HOME, help="HF_HOME for MinerU model caches (default: MinerU's own)")
    parser.add_argument("--modelscope-cache", type=Path, default=DEFAULT_MODELSCOPE_CACHE, help="MODELSCOPE_CACHE for MinerU model caches (default: MinerU's own)")
    parser.add_argument("--torch-home", type=Path, default=DEFAULT_TORCH_HOME, help="TORCH_HOME for MinerU model caches (default: MinerU's own)")
    parser.add_argument("--conda-env", default=DEFAULT_CONDA_ENV or None, help="Conda env holding the MinerU CLI; omit to call mineru directly from PATH")
    parser.add_argument("--mineru-cmd", default=None, help="MinerU executable (default: mineru, or $MINERU_CMD)")
    parser.add_argument("--model-source", default=DEFAULT_MODEL_SOURCE, help="MINERU_MODEL_SOURCE, e.g. local/huggingface/modelscope; unset leaves MinerU's default")
    parser.add_argument("--backend", choices=["pipeline", "vlm-engine", "hybrid-engine"], default="pipeline")
    parser.add_argument(
        "--virtual-vram-gb",
        type=int,
        default=DEFAULT_VIRTUAL_VRAM_GB,
        help="MINERU_VIRTUAL_VRAM_SIZE; unset leaves MinerU's default",
    )
    parser.add_argument("--lang", help="Optional MinerU/PaddleOCR language hint")
    parser.add_argument("--start", type=int, help="First page index, zero-based")
    parser.add_argument("--end", type=int, help="Last page index, zero-based")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--keep-stage", action="store_true")
    return parser.parse_args()


def convert_single(args: argparse.Namespace) -> dict:
    """Convert a single PDF and return the result dict."""
    pdf_path = args.pdf.resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(pdf_path)
    if args.virtual_vram_gb is not None and args.virtual_vram_gb <= 0:
        raise ValueError("--virtual-vram-gb must be a positive integer")

    paper_name = args.paper_name or pdf_path.stem
    stage_dir = args.stage_root / f"{sanitize_name(pdf_path.stem)}-{int(time.time())}"
    stage_dir.mkdir(parents=True, exist_ok=False)

    try:
        run_mineru(args, stage_dir, pdf_path)
        generated_md = find_generated_md(stage_dir)
        target_md = copy_output(generated_md, args.output_root, paper_name, args.overwrite)
        output_quality = quality_check(target_md)
        provenance_path = write_provenance(target_md, pdf_path, args, output_quality)
        result = {
            "status": output_quality["status"],
            "method": args.method,
            "engine": "mineru-3.x",
            "backend": args.backend,
            "virtualVramGB": args.virtual_vram_gb,
            "pdf": str(pdf_path),
            "targetMarkdown": str(target_md),
            "outputRoot": str(args.output_root),
            "stageDir": str(stage_dir),
            "quality": output_quality,
            "provenance": str(provenance_path),
            "notes": [
                "If status is warn/fail, inspect sampleBadContexts and rerun with --method ocr or --method txt as appropriate."
            ],
        }
        return result
    finally:
        if not args.keep_stage and stage_dir.exists():
            shutil.rmtree(stage_dir)


def batch_convert(args: argparse.Namespace) -> int:
    """Convert all PDFs in a directory and print a JSON summary."""
    batch_dir = args.batch.resolve()
    if not batch_dir.is_dir():
        raise FileNotFoundError(f"Batch directory not found: {batch_dir}")

    pdf_files = sorted(batch_dir.glob(args.pattern))
    if not pdf_files:
        print(json.dumps({"status": "error", "error": f"No files matching '{args.pattern}' in {batch_dir}"}))
        return 1

    results = []
    summary = {
        "total": len(pdf_files), "pass": 0, "warn": 0, "fail": 0, "error": 0,
        # True when an error aborted the run early, so a caller can tell a truncated
        # batch from a complete one.
        "stoppedEarly": False,
        "results": results,
    }

    for i, pdf_path in enumerate(pdf_files, 1):
        print(f"\r[{i}/{len(pdf_files)}] {pdf_path.name}...", file=sys.stderr, end="", flush=True)
        try:
            # Copy the parsed options and override only the per-file fields.
            # Rebuilding the namespace by hand silently dropped every flag added
            # after it was written (model_source, mineru_cmd, ...).
            single_args = argparse.Namespace(**vars(args))
            single_args.pdf = pdf_path
            single_args.check_md = None
            single_args.batch = None
            single_args.paper_name = None
            single_result = convert_single(single_args)
            results.append(single_result)
            summary[single_result["status"]] += 1
        except Exception as exc:
            results.append({"pdf": str(pdf_path), "status": "error", "error": str(exc)})
            summary["error"] += 1
            if not args.continue_on_error:
                summary["stoppedEarly"] = True
                print(file=sys.stderr)  # newline after progress
                print(json.dumps(summary, ensure_ascii=False, indent=2))
                return 1

    print(file=sys.stderr)  # final newline after progress
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["error"] == 0 and summary["fail"] == 0 else 2


def main() -> int:
    args = parse_args()

    if args.check_md:
        print(json.dumps(quality_check(args.check_md), ensure_ascii=False, indent=2))
        return 0

    if args.batch:
        return batch_convert(args)

    if not args.pdf:
        raise SystemExit("Provide a PDF path, --check-md, or --batch <dir>.")
    args.pdf = args.pdf.resolve()

    result = convert_single(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"pass", "warn"} else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        raise SystemExit(1)
