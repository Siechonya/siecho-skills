"""Minimal regression tests for the bundled skill scripts.

These cover the pure logic that has actually broken before, and they need neither
Zotero nor MinerU. Run with:

    python -m unittest discover -s tests -v
"""

import argparse
import importlib.util
import io
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
# Overridable so the same suite can be pointed at another checkout (for example a
# machine-local skills directory) without editing the file.
SCRIPTS = pathlib.Path(os.environ.get("ZOTERO_SCRIPTS_DIR",
                                     ROOT / "skills" / "zotero" / "scripts"))
SYNC_SKILLS_SCRIPT = pathlib.Path(os.environ.get(
    "SYNC_SKILLS_SCRIPT", ROOT / "skills" / "skill-management" / "scripts" / "sync-skills.ps1"))


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


bridge = load("zotero_bridge", "zotero_bridge.py")
mineru = load("mineru_create_md", "mineru_create_md.py")


class QualityVerdictTests(unittest.TestCase):
    """A later check must never soften an earlier one (pass -> warn -> fail only)."""

    def verdict(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "sample.md"
            path.write_text(text, encoding="utf-8")
            return mineru.quality_check(path)["status"]

    def test_short_document_with_mild_mojibake_stays_fail(self):
        # 101 characters, one U+00C3 mojibake rune, below the 2% mojibake ratio.
        # The old code marked this "fail" for being short, then a later branch set
        # "warn" unconditionally, downgrading it. A pure U+FFFD sample does NOT catch
        # that: it trips the replacement-character branch, so both versions say "fail".
        self.assertEqual(self.verdict("a " * 50 + "\u00c3"), "fail")

    def test_replacement_characters_fail(self):
        self.assertEqual(self.verdict("\ufffd" * 101), "fail")

    def test_tiny_document_fails(self):
        self.assertEqual(self.verdict("x"), "fail")

    def test_clean_document_passes(self):
        text = ("This is a plausible paragraph of extracted paper text. " * 20)
        self.assertEqual(self.verdict(text), "pass")

    def test_long_token_run_warns_only(self):
        words = " ".join(["averyveryverylongtokenindeed"] * 12 + ["normal"] * 12)
        self.assertEqual(self.verdict(words * 10), "warn")


class MineruMatchScoringTests(unittest.TestCase):
    """A bare year match must never be enough to claim a paper."""

    def setUp(self):
        self.tokens = bridge.title_tokens("Turbulent flow classification with wavelets")
        self.assertTrue(self.tokens)

    def test_year_alone_is_below_threshold(self):
        score, _ = bridge.score_mineru_dir("2021", self.tokens, "2021", None)
        self.assertLess(score, bridge.MATCH_MIN_SCORE)

    def test_shared_title_tokens_reach_threshold(self):
        score, reasons = bridge.score_mineru_dir(
            "turbulent flow classification", self.tokens, "2021", None)
        self.assertGreaterEqual(score, bridge.MATCH_MIN_SCORE)
        self.assertTrue(reasons)

    def test_pdf_filename_match_reaches_threshold(self):
        score, _ = bridge.score_mineru_dir("smith-2021-paper", self.tokens, "2021", "smith-2021-paper")
        self.assertGreaterEqual(score, bridge.MATCH_MIN_SCORE)

    def test_unrelated_title_is_ignored_in_ranking(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = bridge.MINERU_DIR
            bridge.MINERU_DIR = tmp
            try:
                # same year, unrelated topic, matching markdown present
                other = pathlib.Path(tmp) / "2019"
                other.mkdir()
                (other / "botany.md").write_text("plant taxonomy notes", encoding="utf-8")

                ranked = bridge.rank_mineru_candidates(
                    "Turbulent flow classification with wavelets", "2019", None)
                self.assertEqual(ranked, [])
            finally:
                bridge.MINERU_DIR = old


class AttachmentPathTests(unittest.TestCase):
    """`storage:name.pdf` resolves under the attachment's own key."""

    def setUp(self):
        # resolve_attachment_path reads the module global, so point it at the fixture.
        self._saved_storage = bridge.STORAGE_PATH

    def tearDown(self):
        bridge.STORAGE_PATH = self._saved_storage

    def test_keyed_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge.STORAGE_PATH = tmp
            key = "ABCD1234"
            folder = pathlib.Path(tmp) / key
            folder.mkdir()
            pdf = folder / "paper.pdf"
            pdf.write_bytes(b"%PDF-1.4")
            self.assertEqual(
                bridge.resolve_attachment_path("storage:paper.pdf", key),
                str(pdf),
            )

    def test_flat_legacy_layout_still_resolves(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge.STORAGE_PATH = tmp
            pdf = pathlib.Path(tmp) / "paper.pdf"
            pdf.write_bytes(b"%PDF-1.4")
            self.assertEqual(
                bridge.resolve_attachment_path("storage:paper.pdf", "NOKEY"),
                str(pdf),
            )

    def test_missing_file_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge.STORAGE_PATH = tmp
            self.assertIsNone(bridge.resolve_attachment_path("storage:absent.pdf", "ABCD"))

    def test_absolute_path_passthrough(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = pathlib.Path(tmp) / "loose.pdf"
            pdf.write_bytes(b"%PDF-1.4")
            self.assertEqual(bridge.resolve_attachment_path(str(pdf), None), str(pdf))


class ProvenanceTests(unittest.TestCase):
    """Conversion records which PDF produced a markdown, so lookups can be exact."""

    def make_args(self, stage_root, output_root, **overrides):
        values = dict(
            pdf=None, check_md=None, batch=None, pattern="*.pdf",
            continue_on_error=False, method="auto", paper_name=None,
            output_root=output_root, stage_root=stage_root,
            hf_home=None, modelscope_cache=None, torch_home=None,
            conda_env=None, mineru_cmd=None, model_source=None,
            backend="pipeline", virtual_vram_gb=None, lang=None,
            start=None, end=None, overwrite=True, keep_stage=False,
        )
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_vram_none_no_longer_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            pdf = tmp_path / "paper.pdf"
            pdf.write_bytes(b"%PDF-1.4 fake")
            args = self.make_args(tmp_path / "stage", tmp_path / "out")
            args.pdf = pdf

            def fake_run_mineru(args, stage_dir, pdf_path=None):
                (stage_dir / "paper.md").write_text("body text " * 60, encoding="utf-8")

            original = mineru.run_mineru
            mineru.run_mineru = fake_run_mineru
            try:
                result = mineru.convert_single(args)
            finally:
                mineru.run_mineru = original

            self.assertEqual(result["status"], "pass")
            record = json.loads(pathlib.Path(result["provenance"]).read_text(encoding="utf-8"))
            self.assertEqual(record["pdf"]["sha256"], mineru.sha256_file(pdf))
            self.assertEqual(record["markdown"]["file"], "paper.md")

    def test_batch_namespace_keeps_new_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            batch_dir = tmp_path / "pdfs"
            batch_dir.mkdir()
            (batch_dir / "a.pdf").write_bytes(b"%PDF-1.4")
            args = self.make_args(tmp_path / "stage", tmp_path / "out")
            args.batch = batch_dir
            args.model_source = "local"
            args.mineru_cmd = "mineru-custom"

            seen = {}

            def fake_convert(single_args):
                seen["namespace"] = single_args
                return {"status": "pass", "pdf": str(single_args.pdf)}

            original = mineru.convert_single
            mineru.convert_single = fake_convert
            buffer = io.StringIO()
            real_stdout = sys.stdout
            try:
                sys.stdout = buffer  # batch_convert prints its JSON summary
                mineru.batch_convert(args)
            finally:
                sys.stdout = real_stdout
                mineru.convert_single = original

            namespace = seen["namespace"]
            # Rebuilding the namespace by hand used to drop these two.
            self.assertEqual(namespace.model_source, "local")
            self.assertEqual(namespace.mineru_cmd, "mineru-custom")
            self.assertEqual(str(namespace.pdf), str(batch_dir / "a.pdf"))


class CitationTests(unittest.TestCase):
    """BibTeX keeps the full author list and a truthful entry type."""

    CREATORS = [
        {"name": "A One", "firstName": "A", "lastName": "One", "type": "author", "order": 0},
        {"name": "B Two", "firstName": "B", "lastName": "Two", "type": "author", "order": 1},
        {"name": "C Three", "firstName": "C", "lastName": "Three", "type": "author", "order": 2},
    ]

    def test_full_author_list_in_bibtex_form(self):
        self.assertEqual(
            bridge.format_bibtex_authors(self.CREATORS),
            "One, A and Two, B and Three, C",
        )

    def test_display_form_still_abbreviates(self):
        # The reading string keeps its old shape; only BibTeX needed the full list.
        self.assertEqual(bridge.format_authors(self.CREATORS), "A One et al.")

    def test_entry_type_mapping_is_not_always_article(self):
        self.assertEqual(bridge.BIBTEX_ENTRY_TYPES["book"], "book")
        self.assertEqual(bridge.BIBTEX_ENTRY_TYPES["thesis"], "phdthesis")
        self.assertEqual(bridge.BIBTEX_ENTRY_TYPES["journalArticle"], "article")


if __name__ == "__main__":
    unittest.main()

PWSH = shutil.which("pwsh") or shutil.which("powershell")


@unittest.skipUnless(PWSH, "PowerShell not available")
class SyncScriptKeepTests(unittest.TestCase):
    """A private skill sharing a canonical name must not be relinked away.

    Regression: the create/repoint loop ran before consulting Keep, so a private
    directory whose name also existed in the canonical set was replaced by a link.
    """

    CONFIG_START = "# ------------------------------- CONFIG -------------------------------"
    CONFIG_END = "# ----------------------------------------------------------------------"

    def test_private_entry_with_colliding_name_is_untouched(self):
        source = SYNC_SKILLS_SCRIPT
        # -sig on both ends: the script ships with a BOM and PowerShell needs exactly
        # one. Reading it as plain utf-8 keeps the BOM as a \ufeff character and the
        # write then adds a second one, which stops PowerShell parsing <# as a comment.
        text = source.read_text(encoding="utf-8-sig")
        if self.CONFIG_START not in text or self.CONFIG_END not in text:
            self.skipTest("script CONFIG markers not found")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            canonical = tmp_path / "canonical"
            root = tmp_path / "agent"

            # canonical holds one ordinary skill plus one that collides with the
            # agent's private entry.
            for name in ("alpha", "cc-web"):
                folder = canonical / name
                folder.mkdir(parents=True)
                (folder / "SKILL.md").write_text(
                    f"---\nname: {name}\ndescription: test\n---\n\nbody\n", encoding="utf-8")

            # The agent's private skill, as a LINK to somewhere else. A plain directory
            # is protected by the script's real-directory guard, so it would not exercise
            # the bug: the old create/repoint loop saw any *link* with a name present in
            # the canonical set and repointed it, silently replacing the private skill.
            elsewhere = tmp_path / "elsewhere" / "cc-web"
            elsewhere.mkdir(parents=True)
            (elsewhere / "SKILL.md").write_text(
                "---\nname: cc-web\ndescription: PRIVATE, do not touch\n---\n\nprivate\n",
                encoding="utf-8")
            root.mkdir(parents=True, exist_ok=True)
            private = root / "cc-web"
            made = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(private), str(elsewhere)],
                capture_output=True, text=True,
            )
            if made.returncode != 0:
                self.skipTest("could not create a junction: " + made.stderr.strip())

            config = (
                f"$Canonical = '{canonical}'\n"
                "$WholeLinkRoots = @()\n"
                f"$PerSkillRoots = @( @{{ Path = '{root}'; Keep = @('cc-web') }} )\n"
            )
            start = text.index(self.CONFIG_START)
            end = text.index(self.CONFIG_END) + len(self.CONFIG_END)
            patched = text[:start] + self.CONFIG_START + "\n" + config + text[end:]

            script = tmp_path / "sync-test.ps1"
            script.write_text(patched, encoding="utf-8-sig")

            completed = subprocess.run(
                [PWSH, "-NoProfile", "-File", str(script)],
                capture_output=True, text=True,
            )
            output = (completed.stdout or "") + (completed.stderr or "")

            # the private link still points where it did, with its content intact
            self.assertEqual(
                os.path.realpath(private), os.path.realpath(elsewhere),
                "the private junction was repointed at the canonical skill",
            )
            self.assertIn("PRIVATE, do not touch",
                          (private / "SKILL.md").read_text(encoding="utf-8"))

            # the ordinary canonical skill did get linked
            linked = root / "alpha"
            self.assertTrue(linked.exists(), "canonical skill was not linked; script output:\n" + output)
            self.assertIn("collides with a private Keep entry", output)

def build_library(db_path, papers):
    """Create a minimal Zotero-shaped SQLite file.

    Only the tables the bridge actually reads for lookups and listings: enough for
    get_item_fields, the attachment query, and the research-item scan.
    """
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE itemTypes (itemTypeID INTEGER PRIMARY KEY, typeName TEXT);
        CREATE TABLE items (itemID INTEGER PRIMARY KEY, itemTypeID INTEGER, key TEXT);
        CREATE TABLE itemAttachments (itemID INTEGER, parentItemID INTEGER,
                                      contentType TEXT, path TEXT);
        CREATE TABLE fields (fieldID INTEGER PRIMARY KEY, fieldName TEXT);
        CREATE TABLE itemDataValues (valueID INTEGER PRIMARY KEY, value TEXT);
        CREATE TABLE itemData (itemID INTEGER, fieldID INTEGER, valueID INTEGER);
        INSERT INTO itemTypes (itemTypeID, typeName) VALUES (1, 'journalArticle'), (2, 'attachment');
        INSERT INTO fields (fieldID, fieldName) VALUES (1, 'title'), (2, 'date');
        """
    )
    value_id = 0
    for paper in papers:
        conn.execute("INSERT INTO items (itemID, itemTypeID, key) VALUES (?, 1, ?)",
                     (paper["itemID"], paper["key"]))
        for field_id, value in ((1, paper["title"]), (2, paper.get("date", ""))):
            value_id += 1
            conn.execute("INSERT INTO itemDataValues (valueID, value) VALUES (?, ?)",
                         (value_id, value))
            conn.execute("INSERT INTO itemData (itemID, fieldID, valueID) VALUES (?, ?, ?)",
                         (paper["itemID"], field_id, value_id))
        for index, attachment in enumerate(paper.get("attachments", []), start=1):
            # An attachment is an item in its own right: its key names the storage
            # folder, and the bridge joins itemAttachments to items to read it.
            attachment_item_id = 1000 + paper["itemID"] * 10 + index
            conn.execute(
                "INSERT INTO items (itemID, itemTypeID, key) VALUES (?, 2, ?)",
                (attachment_item_id, "ATTKEY%03d" % index),
            )
            conn.execute(
                "INSERT INTO itemAttachments (itemID, parentItemID, contentType, path) "
                "VALUES (?, ?, 'application/pdf', ?)",
                (attachment_item_id, paper["itemID"], attachment["path"]),
            )
    conn.commit()
    conn.close()
    return db_path


def write_provenance(directory, pdf_path, quality="pass", markdown_name="paper.md",
                     markdown_present=True):
    """Write a provenance record the way mineru_create_md.py would."""
    directory.mkdir(parents=True, exist_ok=True)
    digest = mineru.sha256_file(pdf_path)
    if markdown_present:
        (directory / markdown_name).write_text("body " * 80, encoding="utf-8")
    record = {
        "schema": 1,
        "pdf": {
            "path": str(pdf_path),
            "filename": pdf_path.name,
            "sizeBytes": pdf_path.stat().st_size,
            "sha256": digest,
        },
        "markdown": {"file": markdown_name, "qualityStatus": quality},
        "mineru": {"method": "auto", "backend": "pipeline", "condaEnv": None, "modelSource": None},
    }
    import json as _json
    (directory / ".mineru-provenance.json").write_text(
        _json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


class ProvenanceRecordTests(unittest.TestCase):
    """A provenance record is evidence about a file that may no longer be there."""

    def setUp(self):
        self._saved_mineru = bridge.MINERU_DIR
        self._saved_db = bridge.DB_PATH
        self._saved_storage = bridge.STORAGE_PATH

    def tearDown(self):
        bridge.MINERU_DIR = self._saved_mineru
        bridge.DB_PATH = self._saved_db
        bridge.STORAGE_PATH = self._saved_storage

    def test_missing_markdown_is_recorded_not_hidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            pdf = tmp_path / "paper.pdf"
            pdf.write_bytes(b"%PDF-1.4 payload")
            output = tmp_path / "mineru"
            write_provenance(output / "paper", pdf, quality="fail", markdown_present=False)
            bridge.MINERU_DIR = str(output)

            records = bridge.provenance_records()
            self.assertEqual(len(records), 1)
            self.assertFalse(records[0]["markdownExists"])
            self.assertEqual(records[0]["qualityStatus"], "fail")


class MineruLookupTests(unittest.TestCase):
    """cmd_mineru_find must not hand back a path that is gone, and must not guess."""

    def setUp(self):
        self._saved_mineru = bridge.MINERU_DIR
        self._saved_db = bridge.DB_PATH
        self._saved_storage = bridge.STORAGE_PATH

    def tearDown(self):
        bridge.MINERU_DIR = self._saved_mineru
        bridge.DB_PATH = self._saved_db
        bridge.STORAGE_PATH = self._saved_storage

    def run_find(self, item_id):
        buffer = io.StringIO()
        real_stdout = sys.stdout
        try:
            sys.stdout = buffer
            bridge.cmd_mineru_find(item_id)
        finally:
            sys.stdout = real_stdout
        return json.loads(buffer.getvalue())

    def setup_case(self, tmp_path, markdown_present):
        pdf = tmp_path / "turbulent-flow.pdf"
        pdf.write_bytes(b"%PDF-1.4 payload")
        output = tmp_path / "mineru"
        write_provenance(output / "turbulent-flow", pdf, quality="fail",
                         markdown_present=markdown_present)
        db = build_library(tmp_path / "zotero.sqlite", [
            {"itemID": 1, "key": "ITEMKEY1",
             "title": "Turbulent flow classification with wavelets", "date": "2021-03-01",
             "attachments": [{"path": str(pdf)}]},
        ])
        bridge.MINERU_DIR = str(output)
        bridge.DB_PATH = str(db)
        bridge.STORAGE_PATH = None
        return pdf

    def test_deleted_markdown_is_not_reported_as_a_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            self.setup_case(tmp_path, markdown_present=False)
            result = self.run_find(1)
            self.assertEqual(result["match"], "provenance-markdown-missing")
            self.assertIsNone(result["markdownPath"])
            self.assertFalse(result["markdownExists"])
            self.assertEqual(result["qualityStatus"], "fail")
            self.assertIn("missing", result["note"])

    def test_present_markdown_matches_and_carries_the_grade(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            self.setup_case(tmp_path, markdown_present=True)
            result = self.run_find(1)
            self.assertEqual(result["match"], "provenance")
            self.assertTrue(result["markdownExists"])
            self.assertEqual(result["qualityStatus"], "fail")
            self.assertTrue(os.path.isfile(result["markdownPath"]))
            self.assertIn("qualityWarning", result)


class MineruListTests(unittest.TestCase):
    """`matched` must mean "tied to a PDF in THIS library", nothing weaker."""

    def setUp(self):
        self._saved_mineru = bridge.MINERU_DIR
        self._saved_db = bridge.DB_PATH
        self._saved_storage = bridge.STORAGE_PATH

    def tearDown(self):
        bridge.MINERU_DIR = self._saved_mineru
        bridge.DB_PATH = self._saved_db
        bridge.STORAGE_PATH = self._saved_storage

    def run_list(self):
        buffer = io.StringIO()
        real_stdout = sys.stdout
        try:
            sys.stdout = buffer
            bridge.cmd_mineru_list()
        finally:
            sys.stdout = real_stdout
        return json.loads(buffer.getvalue())

    def test_empty_library_yields_no_matches(self):
        # The original bug: a provenance file alone counted as a match, so an empty
        # library still reported matchedCount 1.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            pdf = tmp_path / "paper.pdf"
            pdf.write_bytes(b"%PDF-1.4 payload")
            output = tmp_path / "mineru"
            write_provenance(output / "paper", pdf, quality="pass")
            db = build_library(tmp_path / "zotero.sqlite", [])

            bridge.MINERU_DIR = str(output)
            bridge.DB_PATH = str(db)
            bridge.STORAGE_PATH = None

            summary = self.run_list()
            self.assertEqual(summary["matchedCount"], 0)
            self.assertEqual(summary["unlinkedCount"], 1)
            self.assertEqual(summary["unlinked"][0]["reason"],
                             "no PDF in this library matches the recorded hash")
            self.assertNotIn("itemID", summary["unlinked"][0])

    def test_library_pdf_is_matched_with_its_item_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            pdf = tmp_path / "turbulent-flow.pdf"
            pdf.write_bytes(b"%PDF-1.4 payload")
            output = tmp_path / "mineru"
            write_provenance(output / "turbulent-flow", pdf, quality="pass")
            db = build_library(tmp_path / "zotero.sqlite", [
                {"itemID": 7, "key": "ITEMKEY7", "title": "Turbulent flow classification",
                 "date": "2021-01-01", "attachments": [{"path": str(pdf)}]},
            ])

            bridge.MINERU_DIR = str(output)
            bridge.DB_PATH = str(db)
            bridge.STORAGE_PATH = None

            summary = self.run_list()
            self.assertEqual(summary["matchedCount"], 1)
            self.assertEqual(summary["unlinkedCount"], 0)
            self.assertEqual(summary["matched"][0]["itemID"], 7)
            self.assertTrue(os.path.isfile(summary["matched"][0]["markdownFile"]))
