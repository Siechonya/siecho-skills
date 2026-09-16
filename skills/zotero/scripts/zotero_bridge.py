#!/usr/bin/env python3
"""
Zotero Bridge - Codex integration with local Zotero library.
Reads directly from the Zotero SQLite database for fast, offline access.

Usage:
  python zotero_bridge.py search "<keywords>"              # Search papers by title/abstract
  python zotero_bridge.py get-paper <itemID>              # Get full metadata for a paper
  python zotero_bridge.py list-collections                 # List all collections
  python zotero_bridge.py list-papers [--collection <id>] [--limit N] [--offset N]
  python zotero_bridge.py get-attachments <itemID>        # Get attachment info for a paper
  python zotero_bridge.py find-pdf <itemID>               # Get full PDF path for a paper
  python zotero_bridge.py stats                           # Library statistics
  python zotero_bridge.py recent [--days N]                # Recently added/modified papers
  python zotero_bridge.py notes <itemID>                  # Get notes for a paper
  python zotero_bridge.py annotations <itemID>            # Get PDF annotations for a paper
  python zotero_bridge.py cite <itemID>                   # Generate citation from metadata
  python zotero_bridge.py export <itemID> [--format json|csl|bibtex]
  python zotero_bridge.py fulltext-search "<query>"       # Search inside PDF full-text content
  python zotero_bridge.py search-by-author "<name>"       # Search papers by author name
  python zotero_bridge.py list-tags                       # List all tags with counts
  python zotero_bridge.py mineru-find <itemID>            # Find MinerU markdown for a paper
  python zotero_bridge.py mineru-list                     # List MinerU outputs, split by match quality
  python zotero_bridge.py mineru-adopt <itemID> <dirname> # Record provenance for existing markdown
"""

import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta

# Force UTF-8 output to handle Unicode characters in paper titles
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')


def find_zotero_db():
    """Locate Zotero's SQLite database.

    Resolution order:
      1. $ZOTERO_DB
      2. profile folders under the Zotero data directory
      3. ~/Zotero/zotero.sqlite and ~/*/Zotero/zotero.sqlite
    """
    override = os.environ.get("ZOTERO_DB")
    if override and os.path.exists(override):
        return override

    profiles_dirs = [
        os.path.expandvars(r"%APPDATA%\Zotero\Zotero\Profiles"),
        os.path.expandvars(r"%LOCALAPPDATA%\Zotero\Zotero\Profiles"),
        os.path.expanduser("~/.zotero/zotero"),
    ]
    for profiles_dir in profiles_dirs:
        if not os.path.isdir(profiles_dir):
            continue
        for profile in os.listdir(profiles_dir):
            db = os.path.join(profiles_dir, profile, "zotero.sqlite")
            if os.path.exists(db):
                return db

    # Last resort: search home directory
    import glob as _glob
    for pattern in [
        os.path.expanduser("~/*/Zotero/zotero.sqlite"),
        os.path.expanduser("~/Zotero/zotero.sqlite"),
    ]:
        matches = _glob.glob(pattern)
        if matches:
            return matches[0]

    return None


def find_mineru_dir():
    """Locate the MinerU markdown output directory.

    Resolution order:
      1. $MINERU_OUTPUT_DIR
      2. $ZOTERO_VAULT_ROOT/docs/mineru_output
    """
    override = os.environ.get("MINERU_OUTPUT_DIR")
    if override and os.path.isdir(override):
        return override
    vault = os.environ.get("ZOTERO_VAULT_ROOT")
    if vault:
        candidate = os.path.join(vault, "docs", "mineru_output")
        if os.path.isdir(candidate):
            return candidate
    return None


def find_zotero_storage():
    """Find the Zotero storage directory (for resolving storage: URIs)."""
    override = os.environ.get("ZOTERO_STORAGE")
    if override and os.path.isdir(override):
        return override
    db_dir = os.path.dirname(DB_PATH or "")
    if db_dir:
        storage = os.path.join(db_dir, "storage")
        if os.path.isdir(storage):
            return storage
    return None


# Auto-detect paths. Each one can be overridden with an environment variable;
# that is the supported way to use this bridge on a machine other than the
# author's:
#   ZOTERO_DB           full path to zotero.sqlite
#   ZOTERO_STORAGE      full path to the Zotero "storage" directory
#   MINERU_OUTPUT_DIR   full path to the MinerU markdown output directory
#   ZOTERO_VAULT_ROOT   library root; MinerU output defaults to <root>/docs/mineru_output
DB_PATH = find_zotero_db() or os.environ.get("ZOTERO_DB")
STORAGE_PATH = find_zotero_storage() or os.environ.get("ZOTERO_STORAGE")
MINERU_DIR = find_mineru_dir() or os.environ.get("MINERU_OUTPUT_DIR")

MISSING_DB_MESSAGE = (
    "zotero.sqlite not found. Set ZOTERO_DB to its full path and retry; "
    "see SKILL.md (Requirements) for the other optional variables."
)


def resolve_db_path():
    """Locate the library at call time.

    Kept out of import time on purpose: a missing library should fail the command
    that needs it, not turn `import zotero_bridge` into a process exit.
    """
    global DB_PATH, STORAGE_PATH, MINERU_DIR
    if not DB_PATH:
        DB_PATH = find_zotero_db() or os.environ.get("ZOTERO_DB")
    if not DB_PATH:
        raise RuntimeError(MISSING_DB_MESSAGE)
    if not STORAGE_PATH:
        STORAGE_PATH = find_zotero_storage() or os.environ.get("ZOTERO_STORAGE")
    if not MINERU_DIR:
        MINERU_DIR = find_mineru_dir() or os.environ.get("MINERU_OUTPUT_DIR")
    return DB_PATH

# Field IDs from Zotero schema
FIELDS = {
    1: "title", 2: "abstractNote", 6: "date", 7: "language", 8: "shortTitle",
    13: "url", 14: "accessDate", 15: "rights", 16: "extra", 18: "seriesTitle",
    19: "volume", 21: "place", 23: "publisher", 25: "ISBN", 27: "number",
    30: "section", 32: "pages", 38: "publicationTitle", 40: "type",
    41: "series", 43: "edition", 44: "numPages", 45: "bookTitle",
    57: "proceedingsTitle", 58: "conferenceName", 59: "DOI", 60: "identifier",
    61: "repository", 64: "citationKey", 66: "subject", 76: "issue",
    78: "journalAbbreviation", 79: "ISSN", 92: "references", 94: "status",
    102: "reportNumber", 103: "reportType", 104: "institution",
    110: "thesisType", 111: "university", 113: "websiteTitle",
    114: "eventPlace", 115: "originalDate", 120: "PMID", 121: "PMCID"
}

# Research item types
RESEARCH_TYPES = [
    'journalArticle', 'conferencePaper', 'book', 'bookSection',
    'thesis', 'report', 'preprint', 'manuscript', 'patent',
    'presentation', 'webpage', 'document', 'magazineArticle',
    'newspaperArticle', 'encyclopediaArticle', 'dictionaryEntry',
    'blogPost', 'forumPost', 'interview', 'podcast', 'radioBroadcast',
    'tvBroadcast', 'videoRecording', 'film', 'hearing', 'case', 'statute',
    'bill', 'standard', 'dataset', 'computerProgram', 'map'
]


def get_db():
    """Get a read-only database connection with row factory."""
    path = resolve_db_path()
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=15.0)
    conn.row_factory = sqlite3.Row
    try:
        # Probe here: a library locked by a running Zotero fails at this point, which is
        # the one place that can explain it, instead of SQLITE_BUSY surfacing from inside
        # whichever command the caller ran.
        conn.execute("SELECT 1 FROM itemTypes LIMIT 1").fetchone()
    except sqlite3.OperationalError as error:
        conn.close()
        raise RuntimeError(
            f"could not read the Zotero library at {path}: {error}. "
            "Zotero keeps its library in a rollback-journal SQLite file, so a running "
            "Zotero (or an unfinished sync) blocks readers. Close Zotero and retry, or "
            "point ZOTERO_DB at a copy of the library."
        ) from error
    return conn


def get_field_value(cursor, item_id, field_name):
    """Get a single field value for an item."""
    cursor.execute("""
        SELECT idv.value FROM itemData id
        JOIN fields f ON id.fieldID = f.fieldID
        JOIN itemDataValues idv ON id.valueID = idv.valueID
        WHERE id.itemID = ? AND f.fieldName = ?
    """, (item_id, field_name))
    row = cursor.fetchone()
    return row['value'] if row else None


def get_item_fields(cursor, item_id, field_names):
    """Get multiple field values for an item."""
    cursor.execute("""
        SELECT f.fieldName, idv.value FROM itemData id
        JOIN fields f ON id.fieldID = f.fieldID
        JOIN itemDataValues idv ON id.valueID = idv.valueID
        WHERE id.itemID = ? AND f.fieldName IN ({})
    """.format(','.join(['?'] * len(field_names))), [item_id] + field_names)
    return {row['fieldName']: row['value'] for row in cursor.fetchall()}


def get_creators(cursor, item_id):
    """Get all creators for an item."""
    cursor.execute("""
        SELECT c.firstName, c.lastName, ct.creatorType, ic.orderIndex
        FROM itemCreators ic
        JOIN creators c ON ic.creatorID = c.creatorID
        JOIN creatorTypes ct ON ic.creatorTypeID = ct.creatorTypeID
        WHERE ic.itemID = ?
        ORDER BY ic.orderIndex
    """, (item_id,))
    creators = cursor.fetchall()
    result = []
    for cr in creators:
        name = f"{cr['firstName'] or ''} {cr['lastName'] or ''}".strip()
        result.append({
            "name": name,
            "firstName": cr['firstName'],
            "lastName": cr['lastName'],
            "type": cr['creatorType'],
            "order": cr['orderIndex']
        })
    return result


def format_authors(creators):
    """Format creator list into author string."""
    authors = [c for c in creators if c['type'] == 'author']
    if not authors:
        authors = creators  # fallback to all creators
    names = [a['name'] for a in sorted(authors, key=lambda x: x['order'])]
    if len(names) == 0:
        return "Unknown"
    elif len(names) == 1:
        return names[0]
    elif len(names) == 2:
        return f"{names[0]} & {names[1]}"
    else:
        return f"{names[0]} et al."


def get_item_type(cursor, item_id):
    """Get the type name for an item."""
    cursor.execute("""
        SELECT it.typeName FROM items i
        JOIN itemTypes it ON i.itemTypeID = it.itemTypeID
        WHERE i.itemID = ?
    """, (item_id,))
    row = cursor.fetchone()
    return row['typeName'] if row else None


def cmd_search(query):
    """Search papers by title and abstract."""
    conn = get_db()
    cursor = conn.cursor()

    search_terms = query.split()
    conditions = []
    params = []
    for term in search_terms:
        param_term = f"%{term}%"
        conditions.append("(idv.value LIKE ?)")
        params.append(param_term)

    where_clause = " AND ".join(conditions)

    cursor.execute(f"""
        SELECT DISTINCT i.itemID, i.dateAdded, i.dateModified, it.typeName
        FROM items i
        JOIN itemTypes it ON i.itemTypeID = it.itemTypeID
        JOIN itemData id ON i.itemID = id.itemID
        JOIN itemDataValues idv ON id.valueID = idv.valueID
        JOIN fields f ON id.fieldID = f.fieldID
        WHERE f.fieldName IN ('title', 'abstractNote')
        AND ({where_clause})
        AND it.typeName IN ({','.join(['?']*len(RESEARCH_TYPES))})
        ORDER BY i.dateAdded DESC
        LIMIT 30
    """, params + RESEARCH_TYPES)

    results = cursor.fetchall()
    if not results:
        print(json.dumps({"count": 0, "results": [], "query": query}))
        conn.close()
        return

    output = []
    for row in results:
        item_id = row['itemID']
        fields = get_item_fields(cursor, item_id, ['title', 'date', 'abstractNote', 'publicationTitle', 'DOI'])
        creators = get_creators(cursor, item_id)

        output.append({
            "itemID": item_id,
            "title": fields.get('title', 'N/A'),
            "year": str(fields.get('date', ''))[:4] if fields.get('date') else 'N/A',
            "authors": format_authors(creators),
            "creators": creators,
            "publication": fields.get('publicationTitle', ''),
            "doi": fields.get('DOI', ''),
            "abstract": (fields.get('abstractNote', '') or '')[:500],
            "type": row['typeName'],
            "dateAdded": row['dateAdded']
        })

    print(json.dumps({"count": len(output), "results": output, "query": query}, ensure_ascii=False, indent=2))
    conn.close()


def cmd_get_paper(item_id):
    """Get full metadata for a specific paper."""
    conn = get_db()
    cursor = conn.cursor()

    # Verify item exists
    item_type = get_item_type(cursor, item_id)
    if not item_type:
        print(json.dumps({"error": f"Item {item_id} not found"}))
        conn.close()
        return

    # Get item-level data
    cursor.execute("SELECT * FROM items WHERE itemID = ?", (item_id,))
    item = cursor.fetchone()

    # Get all field values
    cursor.execute("""
        SELECT f.fieldName, idv.value FROM itemData id
        JOIN fields f ON id.fieldID = f.fieldID
        JOIN itemDataValues idv ON id.valueID = idv.valueID
        WHERE id.itemID = ?
    """, (item_id,))
    fields = {row['fieldName']: row['value'] for row in cursor.fetchall()}

    # Get creators
    creators = get_creators(cursor, item_id)

    # Get attachments
    cursor.execute("""
        SELECT itemID, parentItemID, linkMode, contentType, path
        FROM itemAttachments WHERE parentItemID = ?
    """, (item_id,))
    attachments = [dict(row) for row in cursor.fetchall()]

    # Get notes
    cursor.execute("""
        SELECT itemID, note, title FROM itemNotes WHERE parentItemID = ?
    """, (item_id,))
    notes = [dict(row) for row in cursor.fetchall()]

    # Get annotations
    cursor.execute("""
        SELECT itemID, type, authorName, text, comment, pageLabel
        FROM itemAnnotations WHERE parentItemID = ?
    """, (item_id,))
    annotations = [dict(row) for row in cursor.fetchall()]

    # Get collection membership
    cursor.execute("""
        SELECT c.collectionID, c.collectionName
        FROM collectionItems ci
        JOIN collections c ON ci.collectionID = c.collectionID
        WHERE ci.itemID = ?
    """, (item_id,))
    collections = [dict(row) for row in cursor.fetchall()]

    result = {
        "itemID": item_id,
        "key": item['key'],
        "type": item_type,
        "title": fields.get('title', 'N/A'),
        "authors": format_authors(creators),
        "creators": creators,
        "date": fields.get('date', ''),
        "year": str(fields.get('date', ''))[:4] if fields.get('date') else None,
        "abstract": fields.get('abstractNote', ''),
        "publicationTitle": fields.get('publicationTitle', ''),
        "journalAbbreviation": fields.get('journalAbbreviation', ''),
        "volume": fields.get('volume', ''),
        "issue": fields.get('issue', ''),
        "pages": fields.get('pages', ''),
        "doi": fields.get('DOI', ''),
        "url": fields.get('url', ''),
        "publisher": fields.get('publisher', ''),
        "place": fields.get('place', ''),
        "ISBN": fields.get('ISBN', ''),
        "ISSN": fields.get('ISSN', ''),
        "language": fields.get('language', ''),
        "extra": fields.get('extra', ''),
        "citationKey": fields.get('citationKey', ''),
        "dateAdded": item['dateAdded'],
        "dateModified": item['dateModified'],
        "collections": collections,
        "attachments": attachments,
        "notes": notes,
        "annotations": annotations,
        "allFields": fields
    }

    print(json.dumps(result, ensure_ascii=False, indent=2))
    conn.close()


def cmd_list_collections():
    """List all collections with paper counts."""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT c.collectionID, c.collectionName, c.parentCollectionID,
               COUNT(ci.itemID) as itemCount
        FROM collections c
        LEFT JOIN collectionItems ci ON c.collectionID = ci.collectionID
        GROUP BY c.collectionID
        ORDER BY c.collectionName
    """)

    collections = []
    for row in cursor.fetchall():
        collections.append({
            "collectionID": row['collectionID'],
            "name": row['collectionName'],
            "parentID": row['parentCollectionID'],
            "itemCount": row['itemCount']
        })

    print(json.dumps({"collections": collections}, ensure_ascii=False, indent=2))
    conn.close()


def cmd_list_papers(collection_id=None, limit=20, offset=0):
    """List papers, optionally filtered by collection."""
    conn = get_db()
    cursor = conn.cursor()

    if collection_id:
        cursor.execute(f"""
            SELECT DISTINCT i.itemID, i.dateAdded, it.typeName
            FROM items i
            JOIN itemTypes it ON i.itemTypeID = it.itemTypeID
            JOIN collectionItems ci ON i.itemID = ci.itemID
            WHERE ci.collectionID = ?
            AND it.typeName IN ({','.join(['?']*len(RESEARCH_TYPES))})
            ORDER BY i.dateAdded DESC
            LIMIT ? OFFSET ?
        """, [collection_id] + RESEARCH_TYPES + [limit, offset])
    else:
        cursor.execute(f"""
            SELECT i.itemID, i.dateAdded, it.typeName
            FROM items i
            JOIN itemTypes it ON i.itemTypeID = it.itemTypeID
            WHERE it.typeName IN ({','.join(['?']*len(RESEARCH_TYPES))})
            ORDER BY i.dateAdded DESC
            LIMIT ? OFFSET ?
        """, RESEARCH_TYPES + [limit, offset])

    results = cursor.fetchall()
    papers = []
    for row in results:
        item_id = row['itemID']
        fields = get_item_fields(cursor, item_id, ['title', 'date', 'publicationTitle', 'DOI'])
        creators = get_creators(cursor, item_id)

        papers.append({
            "itemID": item_id,
            "title": fields.get('title', 'N/A'),
            "year": str(fields.get('date', ''))[:4] if fields.get('date') else 'N/A',
            "authors": format_authors(creators),
            "publication": fields.get('publicationTitle', ''),
            "doi": fields.get('DOI', ''),
            "type": row['typeName'],
            "dateAdded": row['dateAdded']
        })

    print(json.dumps({"count": len(papers), "limit": limit, "offset": offset, "papers": papers}, ensure_ascii=False, indent=2))
    conn.close()


# Matching weights for pairing a paper with a MinerU output directory. A bare
# year match scores 1, deliberately below MATCH_MIN_SCORE: scoring a year as 3
# against a threshold of 3 made any same-year directory "the" answer, which can
# hand a model the wrong paper while looking successful.
MATCH_MIN_SCORE = 6
MATCH_TITLE_TOKEN_WEIGHT = 3
MATCH_FILENAME_WEIGHT = 10
MATCH_YEAR_WEIGHT = 1
TITLE_TOKEN_MIN_LENGTH = 4
TITLE_STOPWORDS = {
    "with", "from", "that", "this", "these", "those", "into", "over", "under",
    "using", "used", "based", "toward", "towards", "between", "through", "their",
    "there", "when", "which", "while", "study", "analysis", "review", "effect",
    "effects", "novel", "approach",
}


def resolve_attachment_path(path, attachment_key):
    """Resolve one attachment row's `path` to a real filesystem path.

    A Zotero `storage:name.pdf` value lives at <storage>/<attachment key>/name.pdf.
    The flat <storage>/name.pdf layout only exists in very old libraries, so it is
    tried second.
    """
    if not path:
        return None
    path = str(path)
    if not path.startswith("storage:"):
        return path if os.path.exists(path) else None

    base = STORAGE_PATH or os.environ.get("ZOTERO_STORAGE")
    if not base:
        return None

    rel_path = path.replace("storage:", "", 1).replace("\\", "/").lstrip("/")
    candidates = []
    if attachment_key:
        candidates.append(os.path.join(base, attachment_key, rel_path))
    candidates.append(os.path.join(base, rel_path))
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return None


def title_tokens(text):
    """Lowercased content words of a title or directory name."""
    words = re.split(r"[^0-9a-z]+", str(text or "").lower())
    return {w for w in words if len(w) >= TITLE_TOKEN_MIN_LENGTH and w not in TITLE_STOPWORDS}


def score_mineru_dir(dirname, paper_tokens, year, pdf_stem):
    """Score one MinerU directory name against a paper. Returns (score, reasons)."""
    reasons = []
    score = 0

    shared = set(paper_tokens) & title_tokens(dirname)
    if shared:
        score += MATCH_TITLE_TOKEN_WEIGHT * len(shared)
        reasons.append("shares title token(s): " + ", ".join(sorted(shared)))

    if pdf_stem and pdf_stem.lower() in dirname.lower():
        score += MATCH_FILENAME_WEIGHT
        reasons.append("PDF filename appears in the directory name")

    if year and str(year) in dirname:
        score += MATCH_YEAR_WEIGHT
        reasons.append("year " + str(year) + " appears in the directory name")

    return score, reasons


def rank_mineru_candidates(title, year, pdf_stem, limit=5):
    """Heuristic candidates for a paper, best first. Never authoritative."""
    if not MINERU_DIR or not os.path.isdir(MINERU_DIR):
        return []
    paper_tokens = title_tokens(title)
    ranked = []
    for entry in os.scandir(MINERU_DIR):
        if not entry.is_dir():
            continue
        score, reasons = score_mineru_dir(entry.name, paper_tokens, year, pdf_stem)
        if score < MATCH_MIN_SCORE:
            continue
        md_files = [f.path for f in os.scandir(entry.path) if f.is_file() and f.name.endswith(".md")]
        ranked.append({
            "directory": entry.path,
            "dirname": entry.name,
            "markdownFile": md_files[0] if md_files else None,
            "score": score,
            "reasons": reasons,
        })
    ranked.sort(key=lambda item: item["score"], reverse=True)
    return ranked[:limit]


def sha256_file(path, chunk_size=1 << 20):
    """Streaming SHA-256 of a file."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk_size), b""):
            digest.update(block)
    return digest.hexdigest()


def provenance_index():
    """Map PDF sha256 -> recorded MinerU output, over every output directory."""
    index = {}
    if not MINERU_DIR or not os.path.isdir(MINERU_DIR):
        return index
    for entry in os.scandir(MINERU_DIR):
        if not entry.is_dir():
            continue
        record_path = os.path.join(entry.path, ".mineru-provenance.json")
        if not os.path.isfile(record_path):
            continue
        try:
            with open(record_path, encoding="utf-8") as handle:
                record = json.load(handle)
        except (OSError, ValueError):
            continue
        digest = (record.get("pdf") or {}).get("sha256")
        if not digest:
            continue
        markdown_name = (record.get("markdown") or {}).get("file")
        index[digest] = {
            "directory": entry.path,
            "dirname": entry.name,
            "markdownFile": os.path.join(entry.path, markdown_name) if markdown_name else None,
            "provenance": record_path,
            "pdf": (record.get("pdf") or {}).get("path"),
        }
    return index


def cmd_get_attachments(item_id):
    """Get attachment paths for a paper."""
    conn = get_db()
    cursor = conn.cursor()

    # The attachment's own item key names its storage folder, so it has to come
    # back with the row.
    cursor.execute("""
        SELECT ia.itemID, ia.parentItemID, ia.linkMode, ia.contentType, ia.path,
               ia.storageModTime, i.key AS attachmentKey
        FROM itemAttachments ia
        JOIN items i ON i.itemID = ia.itemID
        WHERE ia.parentItemID = ?
    """, (item_id,))
    attachments = [dict(row) for row in cursor.fetchall()]

    for att in attachments:
        att['resolvedPath'] = resolve_attachment_path(att.get('path'), att.get('attachmentKey'))

    print(json.dumps({"itemID": item_id, "attachments": attachments}, ensure_ascii=False, indent=2))
    conn.close()


def cmd_find_pdf(item_id):
    """Find the PDF path for a paper."""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT ia.itemID, ia.path, ia.contentType, i.key AS attachmentKey
        FROM itemAttachments ia
        JOIN items i ON i.itemID = ia.itemID
        WHERE ia.parentItemID = ? AND ia.contentType = 'application/pdf'
        LIMIT 1
    """, (item_id,))
    row = cursor.fetchone()

    if not row:
        print(json.dumps({"itemID": item_id, "pdfPath": None, "error": "No PDF attachment found"}))
    else:
        path = resolve_attachment_path(row['path'], row['attachmentKey'])
        print(json.dumps({
            "itemID": item_id,
            "pdfPath": path,
            "attachmentKey": row['attachmentKey'],
            "exists": bool(path) and os.path.exists(path)
        }, ensure_ascii=False, indent=2))

    conn.close()


def cmd_stats():
    """Get library statistics."""
    conn = get_db()
    cursor = conn.cursor()

    stats = {}

    # Total items by type
    cursor.execute(f"""
        SELECT it.typeName, COUNT(*) as cnt
        FROM items i
        JOIN itemTypes it ON i.itemTypeID = it.itemTypeID
        WHERE it.typeName IN ({','.join(['?']*len(RESEARCH_TYPES))})
        GROUP BY it.typeName
        ORDER BY cnt DESC
    """, RESEARCH_TYPES)
    stats['byType'] = {row['typeName']: row['cnt'] for row in cursor.fetchall()}
    stats['totalPapers'] = sum(stats['byType'].values())

    # Total items
    cursor.execute("SELECT COUNT(*) FROM items")
    stats['totalItems'] = cursor.fetchone()[0]

    # By year
    cursor.execute("""
        SELECT CAST(SUBSTR(idv.value, 1, 4) AS INTEGER) as year, COUNT(DISTINCT i.itemID) as cnt
        FROM items i
        JOIN itemTypes it ON i.itemTypeID = it.itemTypeID
        JOIN itemData id ON i.itemID = id.itemID
        JOIN fields f ON id.fieldID = f.fieldID
        JOIN itemDataValues idv ON id.valueID = idv.valueID
        WHERE f.fieldName = 'date'
        AND it.typeName IN (SELECT typeName FROM itemTypes)
        AND idv.value GLOB '[0-9][0-9][0-9][0-9]*'
        GROUP BY year
        ORDER BY year
    """)
    stats['byYear'] = {str(row['year']): row['cnt'] for row in cursor.fetchall()}

    # Top authors (by paper count)
    cursor.execute("""
        SELECT c.firstName, c.lastName, COUNT(DISTINCT ic.itemID) as cnt
        FROM itemCreators ic
        JOIN creators c ON ic.creatorID = c.creatorID
        JOIN creatorTypes ct ON ic.creatorTypeID = ct.creatorTypeID
        WHERE ct.creatorType = 'author'
        GROUP BY ic.creatorID
        ORDER BY cnt DESC
        LIMIT 10
    """)
    stats['topAuthors'] = [
        {"name": f"{row['firstName'] or ''} {row['lastName'] or ''}".strip(), "papers": row['cnt']}
        for row in cursor.fetchall()
    ]

    # Collections
    cursor.execute("SELECT COUNT(*) FROM collections")
    stats['totalCollections'] = cursor.fetchone()[0]

    # PDFs
    cursor.execute("SELECT COUNT(*) FROM itemAttachments WHERE contentType = 'application/pdf'")
    stats['totalPDFs'] = cursor.fetchone()[0]

    # Notes
    cursor.execute("SELECT COUNT(*) FROM itemNotes")
    stats['totalNotes'] = cursor.fetchone()[0]

    print(json.dumps(stats, ensure_ascii=False, indent=2))
    conn.close()


def cmd_recent(days=7):
    """Get recently added or modified papers."""
    conn = get_db()
    cursor = conn.cursor()

    cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

    cursor.execute(f"""
        SELECT i.itemID, i.dateAdded, i.dateModified, it.typeName
        FROM items i
        JOIN itemTypes it ON i.itemTypeID = it.itemTypeID
        WHERE (i.dateAdded >= ? OR i.dateModified >= ?)
        AND it.typeName IN ({','.join(['?']*len(RESEARCH_TYPES))})
        ORDER BY MAX(i.dateAdded, i.dateModified) DESC
        LIMIT 30
    """, [cutoff, cutoff] + RESEARCH_TYPES)

    results = cursor.fetchall()
    papers = []
    for row in results:
        item_id = row['itemID']
        fields = get_item_fields(cursor, item_id, ['title', 'date'])
        creators = get_creators(cursor, item_id)

        papers.append({
            "itemID": item_id,
            "title": fields.get('title', 'N/A'),
            "year": str(fields.get('date', ''))[:4] if fields.get('date') else 'N/A',
            "authors": format_authors(creators),
            "type": row['typeName'],
            "dateAdded": row['dateAdded'],
            "dateModified": row['dateModified']
        })

    print(json.dumps({"days": days, "count": len(papers), "papers": papers}, ensure_ascii=False, indent=2))
    conn.close()


def cmd_notes(item_id):
    """Get notes for a paper."""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT itemID, note, title FROM itemNotes WHERE parentItemID = ?
    """, (item_id,))
    notes = [dict(row) for row in cursor.fetchall()]

    print(json.dumps({"itemID": item_id, "notes": notes}, ensure_ascii=False, indent=2))
    conn.close()


def cmd_annotations(item_id):
    """Get PDF annotations for a paper."""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT itemID, type, authorName, text, comment, pageLabel, sortIndex
        FROM itemAnnotations WHERE parentItemID = ?
        ORDER BY sortIndex
    """, (item_id,))
    annotations = [dict(row) for row in cursor.fetchall()]

    # Map annotation type codes
    type_map = {0: "highlight", 1: "underline", 2: "note", 3: "image", 4: "ink"}
    for ann in annotations:
        ann['typeName'] = type_map.get(ann['type'], f"unknown({ann['type']})")

    print(json.dumps({"itemID": item_id, "count": len(annotations), "annotations": annotations}, ensure_ascii=False, indent=2))
    conn.close()


BIBTEX_ENTRY_TYPES = {
    "journalArticle": "article",
    "conferencePaper": "inproceedings",
    "book": "book",
    "bookSection": "incollection",
    "thesis": "phdthesis",
    "report": "techreport",
    "manuscript": "unpublished",
}


def format_bibtex_authors(creators):
    """Full author list in BibTeX `and` form.

    The display helper collapses to `A & B` / `A et al.`, which loses authors and
    is not valid BibTeX. This keeps every author in order.
    """
    authors = [c for c in creators if c['type'] == 'author'] or creators
    names = []
    for author in sorted(authors, key=lambda item: item['order']):
        last = (author['lastName'] or '').strip()
        first = (author['firstName'] or '').strip()
        if last:
            names.append(f"{last}, {first}".rstrip(', '))
        elif (author['name'] or '').strip():
            names.append(author['name'].strip())
    return " and ".join(names) if names else "Unknown"


def cmd_cite(item_id):
    """Generate a simplified citation draft.

    This is a draft, not publication-grade output: entry type and the author list
    are handled, but LaTeX-special characters are not escaped and the APA string is
    informal. See the `warning` field in the response.
    """
    conn = get_db()
    cursor = conn.cursor()

    item_type = get_item_type(cursor, item_id)
    if not item_type:
        print(json.dumps({"error": f"Item {item_id} not found"}))
        conn.close()
        return

    fields = get_item_fields(cursor, item_id, [
        'title', 'date', 'publicationTitle', 'volume', 'issue',
        'pages', 'DOI', 'publisher', 'place', 'bookTitle', 'citationKey'
    ])
    creators = get_creators(cursor, item_id)

    authors = format_authors(creators)
    year = str(fields.get('date', ''))[:4] if fields.get('date') else '(n.d.)'
    title = fields.get('title', 'Untitled')
    journal = fields.get('publicationTitle', '')
    volume = fields.get('volume', '')
    issue = fields.get('issue', '')
    pages = fields.get('pages', '')
    doi = fields.get('DOI', '')

    # Informal APA-ish string, for reading only.
    citation = f"{authors} ({year}). {title}."
    if journal:
        citation += f" *{journal}*"
        if volume:
            citation += f", {volume}"
            if issue:
                citation += f"({issue})"
        if pages:
            citation += f", {pages}"
        citation += "."
    if doi:
        citation += f" https://doi.org/{doi}"

    entry_type = BIBTEX_ENTRY_TYPES.get(item_type, "misc")
    container = journal or fields.get('bookTitle', '')
    container_field = "journal" if item_type == "journalArticle" else (
        "booktitle" if item_type == "bookSection" else "howpublished")
    key = fields.get('citationKey') or f"item_{item_id}"

    lines = [
        f"@{entry_type}{{{key},",
        f"  author = {{{format_bibtex_authors(creators)}}},",
        f"  title = {{{title}}},",
        f"  year = {{{year}}},",
    ]
    if container:
        lines.append(f"  {container_field} = {{{container}}},")
    for name, value in (
        ("volume", volume), ("number", issue), ("pages", pages),
        ("publisher", fields.get('publisher', '')), ("doi", doi),
    ):
        if value:
            lines.append(f"  {name} = {{{value}}},")
    lines.append("}")

    result = {
        "itemID": item_id,
        "itemType": item_type,
        "draft": True,
        "citation": citation,
        "apa": citation,
        "bibtex": "\n".join(lines),
        "warning": (
            "Simplified citation draft. Entry type is mapped from the Zotero item type and "
            "the author list is complete, but LaTeX-special characters are NOT escaped, the "
            "APA string is informal rather than APA 7, and no CSL style is applied. Verify "
            "against the target venue's requirements before using it in a submission."
        ),
    }

    print(json.dumps(result, ensure_ascii=False, indent=2))
    conn.close()


def cmd_export(item_id, fmt='json'):
    """Export paper data in various formats."""
    conn = get_db()
    cursor = conn.cursor()

    item_type = get_item_type(cursor, item_id)
    fields = get_item_fields(cursor, item_id, [
        'title', 'date', 'abstractNote', 'publicationTitle', 'volume',
        'issue', 'pages', 'DOI', 'url', 'publisher', 'place',
        'citationKey', 'extra', 'ISSN', 'ISBN', 'language'
    ])
    creators = get_creators(cursor, item_id)

    if fmt == 'json':
        result = {
            "type": item_type,
            **{k: v for k, v in fields.items() if v},
            "creators": creators,
            "citationKey": fields.get('citationKey', '')
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif fmt == 'csl':
        # Basic CSL-JSON format
        csl = {
            "id": str(item_id),
            "type": item_type,
            "title": fields.get('title', ''),
            "DOI": fields.get('DOI', ''),
            "URL": fields.get('url', ''),
            "publisher": fields.get('publisher', ''),
            "container-title": fields.get('publicationTitle', ''),
            "volume": fields.get('volume', ''),
            "issue": fields.get('issue', ''),
            "page": fields.get('pages', ''),
            "issued": {"date-parts": [[int(str(fields.get('date', ''))[:4])]]} if fields.get('date') else None,
            "author": [
                {"given": c['firstName'], "family": c['lastName']}
                for c in creators if c['type'] == 'author'
            ]
        }
        print(json.dumps(csl, ensure_ascii=False, indent=2))

    conn.close()


def cmd_fulltext_search(query):
    """Search inside PDF full-text content using Zotero's indexed words."""
    conn = get_db()
    cursor = conn.cursor()

    search_words = [w.lower().strip() for w in query.split() if len(w.strip()) >= 2]

    if not search_words:
        print(json.dumps({"count": 0, "results": [], "query": query, "error": "Query too short"}))
        conn.close()
        return

    # Find itemIDs that contain ALL search words in their fulltext
    word_placeholders = ','.join(['?'] * len(search_words))
    cursor.execute(f"""
        SELECT fiw.itemID, COUNT(DISTINCT fw.wordID) as word_matches
        FROM fulltextItemWords fiw
        JOIN fulltextWords fw ON fiw.wordID = fw.wordID
        WHERE LOWER(fw.word) IN ({word_placeholders})
        GROUP BY fiw.itemID
        HAVING COUNT(DISTINCT LOWER(fw.word)) >= ?
        ORDER BY word_matches DESC
        LIMIT 20
    """, search_words + [len(search_words)])

    item_matches = {row['itemID']: row['word_matches'] for row in cursor.fetchall()}

    if not item_matches:
        print(json.dumps({"count": 0, "results": [], "query": query}))
        conn.close()
        return

    # Now get parent items (papers) for these fulltext itemIDs
    # fulltextItems.itemID refers to attachment items; we need their parents
    results = []
    for ft_item_id, word_count in item_matches.items():
        # Check if this is an attachment - find the parent paper
        cursor.execute("""
            SELECT parentItemID FROM itemAttachments WHERE itemID = ?
        """, (ft_item_id,))
        att_row = cursor.fetchone()
        paper_id = att_row['parentItemID'] if att_row else ft_item_id

        if not paper_id:
            continue

        # Get paper metadata
        item_type = get_item_type(cursor, paper_id)
        if item_type not in RESEARCH_TYPES and item_type != 'attachment':
            continue

        fields = get_item_fields(cursor, paper_id, ['title', 'date', 'abstractNote', 'publicationTitle', 'DOI'])
        creators = get_creators(cursor, paper_id)

        # Get fulltext info for this attachment
        cursor.execute("SELECT indexedPages, totalPages FROM fulltextItems WHERE itemID = ?", (ft_item_id,))
        ft_info = cursor.fetchone()

        results.append({
            "itemID": paper_id,
            "title": fields.get('title', 'N/A'),
            "year": str(fields.get('date', ''))[:4] if fields.get('date') else 'N/A',
            "authors": format_authors(creators),
            "publication": fields.get('publicationTitle', ''),
            "doi": fields.get('DOI', ''),
            "fulltextWordMatches": word_count,
            "indexedPages": ft_info['indexedPages'] if ft_info else None,
            "totalPages": ft_info['totalPages'] if ft_info else None
        })

    # Deduplicate by paper ID
    seen = set()
    unique_results = []
    for r in results:
        if r['itemID'] not in seen:
            seen.add(r['itemID'])
            unique_results.append(r)

    unique_results.sort(key=lambda x: x['fulltextWordMatches'], reverse=True)

    print(json.dumps({
        "count": len(unique_results),
        "results": unique_results,
        "query": query,
        "note": "Searches inside PDF full-text content indexed by Zotero"
    }, ensure_ascii=False, indent=2))
    conn.close()


def cmd_list_tags():
    """List all tags with item counts."""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT t.name, COUNT(DISTINCT it.itemID) as itemCount
        FROM tags t
        JOIN itemTags it ON t.tagID = it.tagID
        GROUP BY t.tagID
        ORDER BY itemCount DESC
        LIMIT 50
    """)

    tags = [{"tag": row['name'], "count": row['itemCount']} for row in cursor.fetchall()]

    print(json.dumps({"tags": tags, "totalUnique": len(tags)}, ensure_ascii=False, indent=2))
    conn.close()


def cmd_search_by_author(name):
    """Search papers by author name."""
    conn = get_db()
    cursor = conn.cursor()

    search_term = f"%{name}%"

    cursor.execute(f"""
        SELECT DISTINCT i.itemID, i.dateAdded, it.typeName
        FROM items i
        JOIN itemTypes it ON i.itemTypeID = it.itemTypeID
        JOIN itemCreators ic ON i.itemID = ic.itemID
        JOIN creators c ON ic.creatorID = c.creatorID
        WHERE (c.firstName LIKE ? OR c.lastName LIKE ?)
        AND it.typeName IN ({','.join(['?']*len(RESEARCH_TYPES))})
        ORDER BY i.dateAdded DESC
        LIMIT 30
    """, [search_term, search_term] + RESEARCH_TYPES)

    results = cursor.fetchall()
    papers = []
    for row in results:
        item_id = row['itemID']
        fields = get_item_fields(cursor, item_id, ['title', 'date', 'publicationTitle', 'DOI'])
        creators = get_creators(cursor, item_id)

        papers.append({
            "itemID": item_id,
            "title": fields.get('title', 'N/A'),
            "year": str(fields.get('date', ''))[:4] if fields.get('date') else 'N/A',
            "authors": format_authors(creators),
            "allCreators": creators,
            "publication": fields.get('publicationTitle', ''),
            "doi": fields.get('DOI', ''),
            "type": row['typeName'],
            "dateAdded": row['dateAdded']
        })

    print(json.dumps({"count": len(papers), "results": papers, "authorQuery": name}, ensure_ascii=False, indent=2))
    conn.close()


def cmd_mineru_find(item_id):
    """Find MinerU markdown output for a paper.

    Exact match only: the PDF's SHA-256 must equal the one recorded in a
    `.mineru-provenance.json` written by mineru_create_md.py (or by mineru-adopt).
    Directory-name guessing is reported separately as `candidates`, because a bare
    year match used to be enough to return an unrelated paper as the answer.
    """
    conn = get_db()
    cursor = conn.cursor()

    fields = get_item_fields(cursor, item_id, ['title', 'date'])
    title = fields.get('title', '')
    year = str(fields.get('date', ''))[:4] if fields.get('date') else ''

    if not MINERU_DIR or not os.path.isdir(MINERU_DIR):
        print(json.dumps({"itemID": item_id, "markdownPath": None, "error": "MinerU output directory not found"}))
        conn.close()
        return

    cursor.execute("""
        SELECT ia.path, i.key AS attachmentKey
        FROM itemAttachments ia
        JOIN items i ON i.itemID = ia.itemID
        WHERE ia.parentItemID = ? AND ia.contentType = 'application/pdf'
        LIMIT 1
    """, (item_id,))
    pdf_row = cursor.fetchone()

    pdf_path = None
    pdf_filename = ''
    if pdf_row:
        pdf_path = resolve_attachment_path(pdf_row['path'], pdf_row['attachmentKey'])
        if pdf_row['path']:
            pdf_filename = os.path.splitext(os.path.basename(str(pdf_row['path'])))[0]

    exact = None
    if pdf_path and os.path.isfile(pdf_path):
        exact = provenance_index().get(sha256_file(pdf_path))

    result = {
        "itemID": item_id,
        "title": title,
        "year": year or None,
        "pdfPath": pdf_path,
        "match": "provenance" if exact else "none",
        "markdownPath": exact["markdownFile"] if exact else None,
        "candidates": rank_mineru_candidates(title, year, pdf_filename),
        "mineruDir": MINERU_DIR,
    }
    if not exact:
        result["note"] = (
            "No provenance record matched this PDF, so `candidates` are heuristic "
            "directory-name guesses and must be confirmed as the right paper before any "
            "of them is quoted. Converting through mineru_create_md.py records provenance; "
            "`mineru-adopt <itemID> <dirname>` records it for an existing directory."
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    conn.close()


def cmd_mineru_list():
    """List MinerU output directories, split by how they were matched."""
    conn = get_db()
    cursor = conn.cursor()

    if not MINERU_DIR or not os.path.isdir(MINERU_DIR):
        print(json.dumps({"error": "MinerU output directory not found", "checked": MINERU_DIR}))
        conn.close()
        return

    cursor.execute(f"""
        SELECT i.itemID FROM items i
        JOIN itemTypes it ON i.itemTypeID = it.itemTypeID
        WHERE it.typeName IN ({','.join(['?']*len(RESEARCH_TYPES))})
    """, RESEARCH_TYPES)
    all_items = [row['itemID'] for row in cursor.fetchall()]

    paper_index = []
    for item_id in all_items:
        fields = get_item_fields(cursor, item_id, ['title', 'date'])
        paper_index.append({
            "itemID": item_id,
            "title": fields.get('title') or '',
            "tokens": title_tokens(fields.get('title') or ''),
            "year": str(fields.get('date', ''))[:4] if fields.get('date') else '',
        })

    by_dirname = {hit["dirname"]: hit for hit in provenance_index().values()}

    matched = []
    candidates = []
    unmatched = []

    for entry in os.scandir(MINERU_DIR):
        if not entry.is_dir():
            continue
        dirname = entry.name

        if dirname in by_dirname:
            hit = by_dirname[dirname]
            matched.append({
                "dirname": dirname,
                "path": entry.path,
                "markdownFile": hit["markdownFile"],
                "match": "provenance",
            })
            continue

        best_id, best_score, best_reasons = None, 0, []
        for paper in paper_index:
            score, reasons = score_mineru_dir(dirname, paper["tokens"], paper["year"], None)
            if score > best_score:
                best_id, best_score, best_reasons = paper["itemID"], score, reasons

        if best_score >= MATCH_MIN_SCORE:
            candidates.append({
                "dirname": dirname,
                "path": entry.path,
                "itemID": best_id,
                "score": best_score,
                "reasons": best_reasons,
            })
        else:
            unmatched.append(dirname)

    print(json.dumps({
        "matched": matched,
        "candidates": candidates,
        "unmatched": unmatched,
        "totalMineru": len(matched) + len(candidates) + len(unmatched),
        "matchedCount": len(matched),
        "candidateCount": len(candidates),
        "unmatchedCount": len(unmatched),
        "note": (
            "`matched` comes from recorded provenance. `candidates` are heuristic "
            "directory-name matches (score >= " + str(MATCH_MIN_SCORE) + "); confirm each one "
            "before treating it as the paper's markdown."
        ),
    }, ensure_ascii=False, indent=2))
    conn.close()


def cmd_mineru_adopt(item_id, dirname):
    """Record provenance for an existing MinerU directory.

    For markdown created before provenance recording existed. The caller asserts
    that this directory came from this paper's PDF -- which is exactly the judgement
    the automatic matcher must not make by itself.
    """
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT ia.path, i.key AS attachmentKey
        FROM itemAttachments ia
        JOIN items i ON i.itemID = ia.itemID
        WHERE ia.parentItemID = ? AND ia.contentType = 'application/pdf'
        LIMIT 1
    """, (item_id,))
    row = cursor.fetchone()
    if not row:
        print(json.dumps({"error": "No PDF attachment found", "itemID": item_id}, ensure_ascii=False))
        conn.close()
        return

    pdf_path = resolve_attachment_path(row['path'], row['attachmentKey'])
    if not pdf_path or not os.path.isfile(pdf_path):
        print(json.dumps({"error": "PDF file not found on disk", "itemID": item_id, "path": row['path']}, ensure_ascii=False))
        conn.close()
        return

    target_dir = dirname if os.path.isabs(dirname) else os.path.join(MINERU_DIR or '', dirname)
    if not os.path.isdir(target_dir):
        print(json.dumps({"error": "Target directory not found", "target": target_dir}, ensure_ascii=False))
        conn.close()
        return

    md_files = sorted(f for f in os.listdir(target_dir) if f.endswith(".md"))
    record = {
        "schema": 1,
        "pdf": {
            "path": pdf_path,
            "filename": os.path.basename(pdf_path),
            "sizeBytes": os.path.getsize(pdf_path),
            "sha256": sha256_file(pdf_path),
        },
        "markdown": {"file": md_files[0] if md_files else None, "qualityStatus": None},
        "mineru": {"method": None, "backend": None, "condaEnv": None, "modelSource": None},
        "adopted": True,
        "generatedAtUtc": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    out = os.path.join(target_dir, ".mineru-provenance.json")
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False, indent=2)

    print(json.dumps({
        "itemID": item_id,
        "pdfPath": pdf_path,
        "target": target_dir,
        "provenance": out,
        "sha256": record["pdf"]["sha256"],
    }, ensure_ascii=False, indent=2))
    conn.close()


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]

    if command == "search" and len(sys.argv) >= 3:
        cmd_search(sys.argv[2])
    elif command == "get-paper" and len(sys.argv) >= 3:
        cmd_get_paper(int(sys.argv[2]))
    elif command == "list-collections":
        cmd_list_collections()
    elif command == "list-papers":
        collection_id = None
        limit = 20
        offset = 0
        args = sys.argv[2:]
        i = 0
        while i < len(args):
            if args[i] == "--collection" and i + 1 < len(args):
                collection_id = int(args[i + 1])
                i += 2
            elif args[i] == "--limit" and i + 1 < len(args):
                limit = int(args[i + 1])
                i += 2
            elif args[i] == "--offset" and i + 1 < len(args):
                offset = int(args[i + 1])
                i += 2
            else:
                i += 1
        cmd_list_papers(collection_id, limit, offset)
    elif command == "get-attachments" and len(sys.argv) >= 3:
        cmd_get_attachments(int(sys.argv[2]))
    elif command == "find-pdf" and len(sys.argv) >= 3:
        cmd_find_pdf(int(sys.argv[2]))
    elif command == "stats":
        cmd_stats()
    elif command == "recent":
        days = 7
        for i, arg in enumerate(sys.argv[2:], 2):
            if arg == "--days" and i + 1 < len(sys.argv):
                days = int(sys.argv[i + 1])
        cmd_recent(days)
    elif command == "notes" and len(sys.argv) >= 3:
        cmd_notes(int(sys.argv[2]))
    elif command == "annotations" and len(sys.argv) >= 3:
        cmd_annotations(int(sys.argv[2]))
    elif command == "cite" and len(sys.argv) >= 3:
        cmd_cite(int(sys.argv[2]))
    elif command == "fulltext-search" and len(sys.argv) >= 3:
        cmd_fulltext_search(sys.argv[2])
    elif command == "search-by-author" and len(sys.argv) >= 3:
        cmd_search_by_author(sys.argv[2])
    elif command == "list-tags":
        cmd_list_tags()
    elif command == "mineru-find" and len(sys.argv) >= 3:
        cmd_mineru_find(int(sys.argv[2]))
    elif command == "mineru-list":
        cmd_mineru_list()
    elif command == "mineru-adopt" and len(sys.argv) >= 4:
        cmd_mineru_adopt(int(sys.argv[2]), sys.argv[3])
    elif command == "export" and len(sys.argv) >= 3:
        fmt = "json"
        if "--format" in sys.argv:
            idx = sys.argv.index("--format")
            if idx + 1 < len(sys.argv):
                fmt = sys.argv[idx + 1]
        cmd_export(int(sys.argv[2]), fmt)
    else:
        print(f"Unknown command: {command}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        # Our own diagnostic errors (missing library, locked library) read better as
        # JSON than as a traceback, and the skill asks callers to report them verbatim.
        print(json.dumps({"error": str(error)}, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(1)
