"""SQLite storage for LLM analysis results.

Parallel to fulltext_cache.db — stores LLM outputs permanently.
The deploy pipeline reads from both DBs to generate policy_db.json.

Schema (plan D — chapter split 2026-04-20):
    results: composite PK (policy_id, chapter_id).
        chapter_id = '' for whole-bill / legacy rows; = '{policy_id}--{ord:02d}-{slug}'
        for federal omnibus bills split per §2 of the chapter-split spec.
        Empty string rather than NULL so composite PK uniqueness is enforced
        consistently (SQLite treats NULLs as distinct in UNIQUE constraints).
    chapters: manifest of structural markers for split bills. Populated before
        LLM calls dispatch so a crash leaves resumable state.

Existing 2.6 GB DB (504k rows, all with chapter_id implicit '') must be migrated
by `scripts/migrate_llm_results_chapter_split.py` before this module can open
it. open_results_db detects the old single-column PK and refuses to continue
rather than silently mutate production data.

Usage:
    from extraction.llm_results_db import open_results_db, upsert_result, get_result

    conn = open_results_db()
    upsert_result(conn, {"policy_id": "...", "composite_score": 3.2, ...})
    result = get_result(conn, "trial-both-IL-SB54-...")
"""

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "output" / "llm_results.db"


class SchemaMigrationRequired(RuntimeError):
    """Raised when an existing DB is on the pre-chapter-split schema."""


def _results_has_chapter_id(conn: sqlite3.Connection) -> bool:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(results)").fetchall()}
    return "chapter_id" in cols


def _results_table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='results'"
    ).fetchone()
    return row is not None


def open_results_db(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open (or create) the LLM results database with WAL mode.

    Fresh DBs get the post-chapter-split schema (composite PK + chapters table).
    Existing pre-migration DBs raise SchemaMigrationRequired — callers must run
    `scripts/migrate_llm_results_chapter_split.py` first.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    if _results_table_exists(conn) and not _results_has_chapter_id(conn):
        conn.close()
        raise SchemaMigrationRequired(
            f"{db_path} is on the pre-chapter-split schema (no chapter_id column). "
            "Run `python scripts/migrate_llm_results_chapter_split.py` first."
        )

    conn.execute("""CREATE TABLE IF NOT EXISTS results (
        policy_id TEXT NOT NULL,
        chapter_id TEXT NOT NULL DEFAULT '',
        result_json TEXT,
        model TEXT,
        composite_score REAL,
        design_type TEXT,
        rd INTEGER DEFAULT 0,
        did INTEGER DEFAULT 0,
        policy_topic TEXT,
        policy_family TEXT,
        error TEXT,
        analyzed_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (policy_id, chapter_id)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_design ON results(design_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_score ON results(composite_score)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_topic ON results(policy_topic)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_policy_id ON results(policy_id)")

    conn.execute("""CREATE TABLE IF NOT EXISTS chapters (
        chapter_id TEXT PRIMARY KEY,
        policy_id  TEXT NOT NULL,
        marker     TEXT NOT NULL,
        ord        INTEGER NOT NULL,
        char_start INTEGER NOT NULL,
        char_end   INTEGER NOT NULL,
        char_len   INTEGER NOT NULL,
        created_at TEXT DEFAULT (datetime('now'))
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chapters_policy ON chapters(policy_id)")

    conn.commit()
    return conn


def _chapter_id_key(result: Dict[str, Any]) -> str:
    """Normalize result['chapter_id'] for DB storage.

    None (non-split / legacy path from chapter_runner) → ''
    str (split chapter) → pass through
    """
    ch = result.get("chapter_id")
    return "" if ch is None else str(ch)


def upsert_result(conn: sqlite3.Connection, result: Dict[str, Any],
                  model: str = "") -> None:
    """Insert or replace a single LLM result.

    `result['chapter_id']` is None for whole-bill / legacy rows (stored as '')
    and a chapter_id string like 'govinfo-bills-...--01-title_ii' for split
    chapters. The composite PK `(policy_id, chapter_id)` lets a split bill hold
    multiple rows under the same policy_id.
    """
    pid = result.get("policy_id", "")
    if not pid:
        return
    conn.execute(
        """INSERT OR REPLACE INTO results
           (policy_id, chapter_id, result_json, model, composite_score,
            design_type, rd, did, policy_topic, policy_family, error)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            pid,
            _chapter_id_key(result),
            json.dumps(result, ensure_ascii=False),
            model,
            _safe_float(result.get("composite_score")),
            result.get("design_type", ""),
            1 if _is_true(result.get("rd_candidate") or result.get("rd")) else 0,
            1 if _is_true(result.get("did_candidate") or result.get("did")) else 0,
            result.get("policy_topic", ""),
            result.get("policy_family", ""),
            result.get("error", ""),
        ),
    )
    conn.commit()


def upsert_results_batch(conn: sqlite3.Connection, results: List[Dict[str, Any]],
                         model: str = "", batch_size: int = 500) -> int:
    """Batch upsert. Returns count written."""
    count = 0
    for i in range(0, len(results), batch_size):
        batch = results[i:i + batch_size]
        conn.executemany(
            """INSERT OR REPLACE INTO results
               (policy_id, chapter_id, result_json, model, composite_score,
                design_type, rd, did, policy_topic, policy_family, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    r.get("policy_id", ""),
                    _chapter_id_key(r),
                    json.dumps(r, ensure_ascii=False),
                    model,
                    _safe_float(r.get("composite_score")),
                    r.get("design_type", ""),
                    1 if _is_true(r.get("rd_candidate") or r.get("rd")) else 0,
                    1 if _is_true(r.get("did_candidate") or r.get("did")) else 0,
                    r.get("policy_topic", ""),
                    r.get("policy_family", ""),
                    r.get("error", ""),
                )
                for r in batch
                if r.get("policy_id")
            ],
        )
        conn.commit()
        count += len(batch)
    return count


def get_result(conn: sqlite3.Connection, policy_id: str,
               chapter_id: str = "") -> Optional[Dict[str, Any]]:
    """Get a single LLM result row.

    Defaults to chapter_id='' (whole-bill / legacy). Pass a chapter_id string
    to fetch a specific split chapter. For "get all chapters of this bill"
    use get_results_for_bill.
    """
    row = conn.execute(
        "SELECT result_json FROM results WHERE policy_id = ? AND chapter_id = ?",
        (policy_id, chapter_id),
    ).fetchone()
    if row and row[0]:
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            return None
    return None


def get_results_for_bill(conn: sqlite3.Connection,
                         policy_id: str) -> List[Dict[str, Any]]:
    """Return all result rows for a policy_id (one per chapter, or one for whole-bill)."""
    rows = conn.execute(
        "SELECT result_json FROM results WHERE policy_id = ?",
        (policy_id,),
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for row in rows:
        if row and row[0]:
            try:
                out.append(json.loads(row[0]))
            except json.JSONDecodeError:
                pass
    return out


def delete_whole_bill_row(conn: sqlite3.Connection, policy_id: str) -> int:
    """Delete the chapter_id='' row for a bill.

    Called by chapter_runner right before writing split-chapter rows, to
    prevent the old whole-bill result from ghosting alongside new chapter
    results. Returns rowcount deleted (0 or 1).
    """
    cur = conn.execute(
        "DELETE FROM results WHERE policy_id = ? AND chapter_id = ''",
        (policy_id,),
    )
    conn.commit()
    return cur.rowcount


def get_analyzed_ids(conn: sqlite3.Connection, prefix: str = "",
                     errors_only: bool = False) -> Set[str]:
    """Get distinct policy_ids already analyzed.

    Plan D note: after chapter split, a bill may have N rows, all under the
    same policy_id. The set returned dedupes naturally; "has this bill been
    analyzed?" semantics are preserved.
    """
    if errors_only:
        q = "SELECT policy_id FROM results WHERE error != '' AND error IS NOT NULL"
    elif prefix:
        q = f"SELECT policy_id FROM results WHERE policy_id LIKE '{prefix}%' AND (error = '' OR error IS NULL)"
    else:
        q = "SELECT policy_id FROM results WHERE error = '' OR error IS NULL"
    return {r[0] for r in conn.execute(q).fetchall()}


def get_unanalyzed_ids(conn: sqlite3.Connection, all_ids: Set[str]) -> Set[str]:
    """Given a set of policy_ids, return those not yet successfully analyzed."""
    analyzed = get_analyzed_ids(conn)
    return all_ids - analyzed


def stats(conn: sqlite3.Connection) -> dict:
    """Quick summary stats. `bills` is distinct policy_id count (plan D)."""
    total = conn.execute("SELECT COUNT(*) FROM results").fetchone()[0]
    bills = conn.execute("SELECT COUNT(DISTINCT policy_id) FROM results").fetchone()[0]
    success = conn.execute("SELECT COUNT(*) FROM results WHERE error = '' OR error IS NULL").fetchone()[0]
    errors = total - success
    rd = conn.execute("SELECT COUNT(*) FROM results WHERE rd = 1").fetchone()[0]
    did = conn.execute("SELECT COUNT(*) FROM results WHERE did = 1").fetchone()[0]
    chapters_n = conn.execute("SELECT COUNT(*) FROM chapters").fetchone()[0]
    return {"total": total, "bills": bills, "success": success, "errors": errors,
            "rd": rd, "did": did, "chapters": chapters_n}


def export_all_results(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Export all successful results as parsed JSON dicts."""
    rows = conn.execute(
        "SELECT result_json FROM results WHERE error = '' OR error IS NULL"
    ).fetchall()
    results = []
    for row in rows:
        try:
            results.append(json.loads(row[0]))
        except (json.JSONDecodeError, TypeError):
            pass
    return results


# --- chapter manifest helpers (plan D — chapter split) ---

def write_chapter_manifest(conn: sqlite3.Connection, policy_id: str,
                           chapters: Iterable[Any]) -> int:
    """Persist the chapter split manifest for a bill BEFORE any LLM call fires.

    Called by chapter_runner.extract_with_chapters as the `chapter_writer`
    callback. Accepts Chapter dataclass instances from chapter_split.py; reads
    the minimal metadata (not the chapter text) so the manifest stays cheap.

    Upsert semantics: re-running a split re-writes the manifest rows for that
    bill. Returns count written.
    """
    rows = []
    for ch in chapters:
        rows.append((
            ch.chapter_id,
            policy_id,
            ch.marker,
            int(ch.ord),
            int(ch.char_start),
            int(ch.char_end),
            int(ch.char_len),
        ))
    if not rows:
        return 0
    conn.executemany(
        """INSERT OR REPLACE INTO chapters
           (chapter_id, policy_id, marker, ord, char_start, char_end, char_len)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    return len(rows)


def get_chapter_manifest(conn: sqlite3.Connection,
                         policy_id: str) -> List[Dict[str, Any]]:
    """Return the chapter manifest rows for a policy_id, ordered by `ord`.

    Empty list for non-split bills. Used by the deploy pipeline to emit
    chapter metadata into policy_db.json and by crash-recovery queries to
    find chapters with no corresponding results row.
    """
    rows = conn.execute(
        """SELECT chapter_id, policy_id, marker, ord,
                  char_start, char_end, char_len, created_at
           FROM chapters WHERE policy_id = ? ORDER BY ord""",
        (policy_id,),
    ).fetchall()
    cols = ["chapter_id", "policy_id", "marker", "ord",
            "char_start", "char_end", "char_len", "created_at"]
    return [dict(zip(cols, r)) for r in rows]


def get_unresolved_chapters(conn: sqlite3.Connection) -> List[str]:
    """chapters with no results row yet — crash-recovery query (§ 4.3 spec)."""
    rows = conn.execute(
        """SELECT c.chapter_id
           FROM chapters c
           LEFT JOIN results r
             ON r.policy_id = c.policy_id AND r.chapter_id = c.chapter_id
           WHERE r.chapter_id IS NULL"""
    ).fetchall()
    return [r[0] for r in rows]


def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _is_true(val) -> bool:
    if val is None:
        return False
    if isinstance(val, bool):
        return val
    return str(val).lower() in ("true", "1", "yes")
