import sqlite3
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Iterator, Optional

from . import cache
from .config import config

APPLE_EPOCH_OFFSET = 978307200  # Seconds between Unix epoch and 2001-01-01


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    db_path = config.cache_db_path
    if not db_path.exists():
        # Cold start — populate the cache lazily from the configured source.
        if not cache.refresh_chat_db_cache():
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
    if ts > 10**12:
        ts = ts / 1_000_000_000
    return ts + APPLE_EPOCH_OFFSET


# ---------- attributedBody decoder ----------
#
# Apple stores message text in `attributedBody` as a `typedstream`-archived
# NSAttributedString. We walk the byte stream looking for the primitive-string
# type marker `\x01\x2b` (the `+` typecode), read the length-prefixed UTF-8
# payload after it, and skip anything that looks like a Foundation class name.

_FOUNDATION_CLASS_NAMES = frozenset({
    "NSString", "NSMutableString",
    "NSAttributedString", "NSMutableAttributedString",
    "NSObject", "NSDictionary", "NSMutableDictionary",
    "NSArray", "NSMutableArray",
    "NSNumber", "NSData", "NSValue", "NSNull",
    "NSColor", "NSFont", "NSURL",
    "NSRange", "NSRect", "NSPoint", "NSSize",
    "NSParagraphStyle", "NSMutableParagraphStyle",
    "NSDecimalNumber", "NSDate", "NSError",
})

# Apple Messages attribute keys — show up as strings inside the attribute dict
# but are never the user-visible text.
_MESSAGE_ATTR_KEYS = frozenset({
    "__kIMMessagePartAttributeName",
    "__kIMFileTransferGUIDAttributeName",
    "__kIMMentionConfirmedMention",
    "__kIMLinkAttributeName",
    "__kIMBaseWritingDirectionAttributeName",
    "__kIMCalloutAttributeName",
    "__kIMOneTimeCodeAttributeName",
    "__kIMDataDetectedAttributeName",
    "NSNumber", "NSValue",  # double-listed, harmless
})


def _looks_like_class_name(s: str) -> bool:
    if s in _FOUNDATION_CLASS_NAMES or s in _MESSAGE_ATTR_KEYS:
        return True
    if s.startswith("__kIM"):
        return True
    # Anything ObjC-class-shaped: NS/CF/CG/CA prefix followed by an uppercase letter.
    if len(s) >= 3 and s[:2] in ("NS", "CF", "CG", "CA", "UI") and s[2].isupper():
        return True
    return False


def _read_typedstream_length(blob: bytes, cursor: int) -> tuple[int, int]:
    """Read a variable-length integer at `cursor`. Returns (length, new_cursor).
    Returns (-1, cursor) if the encoding is malformed."""
    if cursor >= len(blob):
        return -1, cursor
    b = blob[cursor]
    cursor += 1
    if b < 0x80:
        return b, cursor
    if b == 0x81 and cursor + 2 <= len(blob):
        return int.from_bytes(blob[cursor:cursor + 2], "little"), cursor + 2
    if b == 0x82 and cursor + 4 <= len(blob):
        return int.from_bytes(blob[cursor:cursor + 4], "little"), cursor + 4
    return -1, cursor


@lru_cache(maxsize=50000)
def _decode_attributed_body_impl(blob: bytes) -> Optional[str]:
    """Scan a typedstream archive for the user-visible message text.

    Strategy:
      1. Find every `\\x01\\x2b` (primitive-string type marker) in the blob.
      2. Read the length-prefixed UTF-8 string after each marker.
      3. Discard class-name and attribute-key strings.
      4. Return the longest remaining candidate — that's almost always the
         message body (attribute strings like "Helvetica" or short URLs are
         shorter than typical message text).
    """
    candidates: list[str] = []
    pos = 0
    while pos < len(blob):
        idx = blob.find(b"\x01\x2b", pos)
        if idx < 0:
            break
        length, after = _read_typedstream_length(blob, idx + 2)
        if length <= 0 or after + length > len(blob):
            pos = idx + 2
            continue
        try:
            text = blob[after:after + length].decode("utf-8")
        except UnicodeDecodeError:
            text = blob[after:after + length].decode("utf-8", errors="replace")
        if text and not _looks_like_class_name(text):
            candidates.append(text)
        pos = after + length

    if not candidates:
        return None
    # The message body is typically the longest non-class candidate.
    return max(candidates, key=len)


def decode_attributed_body(blob: Optional[bytes]) -> Optional[str]:
    if not blob:
        return None
    try:
        return _decode_attributed_body_impl(bytes(blob))
    except Exception:
        return None


# ---------- queries ----------

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
    msg_sql = """
        SELECT
            m.ROWID AS message_id,
            m.guid,
            m.text,
            m.attributedBody,
            m.date,
            m.is_from_me,
            m.service,
            h.id AS sender_id
        FROM chat_message_join cmj
        JOIN message m ON m.ROWID = cmj.message_id
        LEFT JOIN handle h ON h.ROWID = m.handle_id
        WHERE cmj.chat_id = ?
        ORDER BY m.date DESC
        LIMIT ? OFFSET ?
    """
    with get_conn() as conn:
        rows = conn.execute(msg_sql, (chat_id, limit, offset)).fetchall()
        rows = list(reversed(rows))  # newest-N fetched desc -> display oldest->newest
        message_ids = [r["message_id"] for r in rows]
        attachments_by_msg = _attachments_for(conn, message_ids)

    results = []
    for r in rows:
        text = r["text"] or decode_attributed_body(r["attributedBody"])
        atts = attachments_by_msg.get(r["message_id"], [])
        results.append({
            "message_id": r["message_id"],
            "guid": r["guid"],
            "text": text,
            "date": apple_ts_to_unix(r["date"]),
            "is_from_me": bool(r["is_from_me"]),
            "service": r["service"],
            "sender_id": r["sender_id"],
            "attachment_count": len(atts),
            "attachments": atts,
        })
    return results


def get_chat_attachments(chat_id: int) -> list[dict]:
    """All attachments in a conversation, newest first — for the media gallery."""
    sql = """
        SELECT a.ROWID AS attachment_id, a.filename, a.mime_type,
               a.transfer_name, a.total_bytes, m.date
        FROM chat_message_join cmj
        JOIN message m ON m.ROWID = cmj.message_id
        JOIN message_attachment_join maj ON maj.message_id = m.ROWID
        JOIN attachment a ON a.ROWID = maj.attachment_id
        WHERE cmj.chat_id = ?
        ORDER BY m.date DESC
    """
    with get_conn() as conn:
        rows = conn.execute(sql, (chat_id,)).fetchall()
    return [
        {
            "attachment_id": r["attachment_id"],
            "filename": r["filename"],
            "mime_type": r["mime_type"],
            "transfer_name": r["transfer_name"],
            "total_bytes": r["total_bytes"],
            "date": apple_ts_to_unix(r["date"]),
        }
        for r in rows
    ]


def _attachments_for(conn: sqlite3.Connection, message_ids: list[int]) -> dict[int, list[dict]]:
    if not message_ids:
        return {}
    placeholders = ",".join("?" * len(message_ids))
    sql = f"""
        SELECT maj.message_id, a.ROWID AS attachment_id, a.filename,
               a.mime_type, a.transfer_name, a.total_bytes
        FROM message_attachment_join maj
        JOIN attachment a ON a.ROWID = maj.attachment_id
        WHERE maj.message_id IN ({placeholders})
    """
    by_msg: dict[int, list[dict]] = {}
    for r in conn.execute(sql, message_ids):
        by_msg.setdefault(r["message_id"], []).append({
            "attachment_id": r["attachment_id"],
            "filename": r["filename"],
            "mime_type": r["mime_type"],
            "transfer_name": r["transfer_name"],
            "total_bytes": r["total_bytes"],
        })
    return by_msg


def get_attachment(attachment_id: int) -> Optional[dict]:
    with get_conn() as conn:
        r = conn.execute(
            "SELECT ROWID AS attachment_id, filename, mime_type, transfer_name FROM attachment WHERE ROWID = ?",
            (attachment_id,),
        ).fetchone()
    return dict(r) if r else None


def resolve_attachment_path(filename: str) -> Optional[Path]:
    """Filenames in the DB use `~/Library/Messages/Attachments/...`. Map onto our data dir.
    Attachments are still read from the source data_dir (NFS) — only chat.db is cached locally."""
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
    try:
        candidate.relative_to(Path(data_dir).resolve())
    except ValueError:
        return None
    return candidate if candidate.exists() else None


def search_messages(query: str, limit: int = 200) -> list[dict]:
    """Substring search on plaintext `text` column. Hex-blob bodies aren't
    indexed yet (would need an FTS table built from decoded text)."""
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


def clear_decoder_cache() -> None:
    """Call after refreshing the local DB cache — decoded bodies may change
    if the source DB was updated."""
    _decode_attributed_body_impl.cache_clear()
