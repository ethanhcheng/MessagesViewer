import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from .config import config

APPLE_EPOCH_OFFSET = 978307200  # Seconds between Unix epoch and 2001-01-01


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    db_path = config.chat_db_path
    if db_path is None or not db_path.exists():
        raise RuntimeError("chat.db not configured or missing")
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def apple_ts_to_unix(ts: Optional[int]) -> Optional[float]:
    """Apple's `message.date` is nanoseconds since 2001-01-01 (or seconds in old DBs)."""
    if ts is None or ts == 0:
        return None
    # Heuristic: nanosecond timestamps are very large
    if ts > 10**12:
        ts = ts / 1_000_000_000
    return ts + APPLE_EPOCH_OFFSET


def decode_attributed_body(blob: Optional[bytes]) -> Optional[str]:
    """Decode the typedstream-encoded `attributedBody` blob used in Ventura+.

    Falls back to a naïve scan if the typedstream library is unavailable or
    parsing fails — extracts the longest UTF-8 run, which is reliably the
    NSString payload at the start of the archive.
    """
    if not blob:
        return None
    try:
        import typedstream  # type: ignore

        ts = typedstream.unarchive_from_data(blob)
        for obj in _walk(ts):
            if isinstance(obj, str) and obj and obj != "NSString" and obj != "NSDictionary":
                return obj
    except Exception:
        pass
    return _fallback_extract_text(blob)


def _walk(obj, _seen=None):
    if _seen is None:
        _seen = set()
    oid = id(obj)
    if oid in _seen:
        return
    _seen.add(oid)
    yield obj
    if hasattr(obj, "contents"):
        for c in obj.contents:  # type: ignore[attr-defined]
            yield from _walk(c, _seen)
    elif isinstance(obj, (list, tuple)):
        for c in obj:
            yield from _walk(c, _seen)
    elif isinstance(obj, dict):
        for c in obj.values():
            yield from _walk(c, _seen)


def _fallback_extract_text(blob: bytes) -> Optional[str]:
    # NSString payload sits after the marker bytes 01 2B in typedstream archives.
    marker = b"\x01\x2b"
    idx = blob.find(marker)
    if idx == -1:
        return None
    cursor = idx + len(marker)
    if cursor >= len(blob):
        return None
    length = blob[cursor]
    cursor += 1
    if length == 0x81 and cursor + 2 <= len(blob):
        length = int.from_bytes(blob[cursor:cursor + 2], "little")
        cursor += 2
    elif length == 0x82 and cursor + 4 <= len(blob):
        length = int.from_bytes(blob[cursor:cursor + 4], "little")
        cursor += 4
    try:
        return blob[cursor:cursor + length].decode("utf-8", errors="replace")
    except Exception:
        return None


def list_chats(limit: int = 500) -> list[dict]:
    sql = """
        SELECT
            c.ROWID AS chat_id,
            c.guid,
            c.display_name,
            c.chat_identifier,
            (
                SELECT MAX(m.date)
                FROM chat_message_join cmj
                JOIN message m ON m.ROWID = cmj.message_id
                WHERE cmj.chat_id = c.ROWID
            ) AS last_date,
            (
                SELECT COUNT(*)
                FROM chat_message_join cmj
                WHERE cmj.chat_id = c.ROWID
            ) AS message_count,
            (
                SELECT GROUP_CONCAT(DISTINCT h.id)
                FROM chat_handle_join chj
                JOIN handle h ON h.ROWID = chj.handle_id
                WHERE chj.chat_id = c.ROWID
            ) AS participants
        FROM chat c
        ORDER BY last_date DESC NULLS LAST
        LIMIT ?
    """
    with get_conn() as conn:
        rows = conn.execute(sql, (limit,)).fetchall()
    return [
        {
            "chat_id": r["chat_id"],
            "guid": r["guid"],
            "display_name": r["display_name"] or r["chat_identifier"],
            "chat_identifier": r["chat_identifier"],
            "participants": (r["participants"] or "").split(",") if r["participants"] else [],
            "last_date": apple_ts_to_unix(r["last_date"]),
            "message_count": r["message_count"],
        }
        for r in rows
    ]


def get_chat_messages(chat_id: int, limit: int = 1000, offset: int = 0) -> list[dict]:
    sql = """
        SELECT
            m.ROWID AS message_id,
            m.guid,
            m.text,
            m.attributedBody,
            m.date,
            m.is_from_me,
            m.service,
            h.id AS sender_id,
            (
                SELECT COUNT(*) FROM message_attachment_join maj
                WHERE maj.message_id = m.ROWID
            ) AS attachment_count
        FROM chat_message_join cmj
        JOIN message m ON m.ROWID = cmj.message_id
        LEFT JOIN handle h ON h.ROWID = m.handle_id
        WHERE cmj.chat_id = ?
        ORDER BY m.date ASC
        LIMIT ? OFFSET ?
    """
    with get_conn() as conn:
        rows = conn.execute(sql, (chat_id, limit, offset)).fetchall()
    results = []
    for r in rows:
        text = r["text"] or decode_attributed_body(r["attributedBody"])
        results.append({
            "message_id": r["message_id"],
            "guid": r["guid"],
            "text": text,
            "date": apple_ts_to_unix(r["date"]),
            "is_from_me": bool(r["is_from_me"]),
            "service": r["service"],
            "sender_id": r["sender_id"],
            "attachment_count": r["attachment_count"],
        })
    return results


def get_message_attachments(message_id: int) -> list[dict]:
    sql = """
        SELECT a.ROWID AS attachment_id, a.filename, a.mime_type, a.transfer_name, a.total_bytes
        FROM message_attachment_join maj
        JOIN attachment a ON a.ROWID = maj.attachment_id
        WHERE maj.message_id = ?
    """
    with get_conn() as conn:
        rows = conn.execute(sql, (message_id,)).fetchall()
    return [dict(r) for r in rows]


def get_attachment(attachment_id: int) -> Optional[dict]:
    with get_conn() as conn:
        r = conn.execute(
            "SELECT ROWID AS attachment_id, filename, mime_type, transfer_name FROM attachment WHERE ROWID = ?",
            (attachment_id,),
        ).fetchone()
    return dict(r) if r else None


def resolve_attachment_path(filename: str) -> Optional[Path]:
    """Filenames in the DB use `~/Library/Messages/Attachments/...`. Map onto our data dir."""
    if not filename:
        return None
    if filename.startswith("~/Library/Messages/"):
        rel = filename.replace("~/Library/Messages/", "", 1)
    else:
        rel = filename
    data_dir = config.data_dir
    if not data_dir:
        return None
    candidate = (Path(data_dir) / rel).resolve()
    # Prevent path traversal — must stay under data_dir
    try:
        candidate.relative_to(Path(data_dir).resolve())
    except ValueError:
        return None
    return candidate if candidate.exists() else None


def search_messages(query: str, limit: int = 200) -> list[dict]:
    """Substring search on plaintext `text` column. Hex-blob bodies are not searched
    here (would require decoding every row); a future indexer could materialize them."""
    sql = """
        SELECT
            m.ROWID AS message_id,
            m.text,
            m.date,
            m.is_from_me,
            cmj.chat_id,
            c.display_name,
            c.chat_identifier,
            h.id AS sender_id
        FROM message m
        JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
        JOIN chat c ON c.ROWID = cmj.chat_id
        LEFT JOIN handle h ON h.ROWID = m.handle_id
        WHERE m.text LIKE ?
        ORDER BY m.date DESC
        LIMIT ?
    """
    with get_conn() as conn:
        rows = conn.execute(sql, (f"%{query}%", limit)).fetchall()
    return [
        {
            "message_id": r["message_id"],
            "chat_id": r["chat_id"],
            "chat_name": r["display_name"] or r["chat_identifier"],
            "text": r["text"],
            "date": apple_ts_to_unix(r["date"]),
            "is_from_me": bool(r["is_from_me"]),
            "sender_id": r["sender_id"],
        }
        for r in rows
    ]
