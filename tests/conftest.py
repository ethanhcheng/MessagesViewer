import sqlite3
from pathlib import Path

import pytest

from app import db
from app.config import config

# Apple epoch (2001-01-01) in Unix seconds.
APPLE_EPOCH = 978307200


def _build_chat_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
        CREATE TABLE chat (
            ROWID INTEGER PRIMARY KEY, guid TEXT, display_name TEXT,
            chat_identifier TEXT
        );
        CREATE TABLE message (
            ROWID INTEGER PRIMARY KEY, guid TEXT, text TEXT,
            attributedBody BLOB, date INTEGER, is_from_me INTEGER,
            service TEXT, handle_id INTEGER
        );
        CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
        CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
        CREATE TABLE attachment (
            ROWID INTEGER PRIMARY KEY, filename TEXT, mime_type TEXT,
            transfer_name TEXT, total_bytes INTEGER
        );
        CREATE TABLE message_attachment_join (message_id INTEGER, attachment_id INTEGER);
        """
    )
    conn.execute("INSERT INTO handle (ROWID, id) VALUES (1, '+15551234567')")
    conn.execute("INSERT INTO chat VALUES (1, 'guid-1', NULL, '+15551234567')")
    # 5 messages, ascending date (nanoseconds since Apple epoch).
    base_ns = 600_000_000 * 1_000_000_000  # ~2020-01-05 in ns since 2001
    for i in range(1, 6):
        date_ns = base_ns + i * 60 * 1_000_000_000  # one minute apart
        conn.execute(
            "INSERT INTO message (ROWID, guid, text, date, is_from_me, service, handle_id) "
            "VALUES (?, ?, ?, ?, ?, 'iMessage', 1)",
            (i, f"m{i}", f"message {i}", date_ns, 0),
        )
        conn.execute("INSERT INTO chat_message_join VALUES (1, ?)", (i,))
    # One attachment on message 3.
    conn.execute(
        "INSERT INTO attachment VALUES (1, '~/Library/Messages/Attachments/x.jpg', "
        "'image/jpeg', 'x.jpg', 1024)"
    )
    conn.execute("INSERT INTO message_attachment_join VALUES (3, 1)")
    conn.commit()
    conn.close()


def _build_addressbook(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE ZABCDRECORD (
            Z_PK INTEGER PRIMARY KEY, ZFIRSTNAME TEXT, ZLASTNAME TEXT,
            ZORGANIZATION TEXT
        );
        CREATE TABLE ZABCDPHONENUMBER (
            Z_PK INTEGER PRIMARY KEY, ZOWNER INTEGER, ZFULLNUMBER TEXT
        );
        CREATE TABLE ZABCDEMAILADDRESS (
            Z_PK INTEGER PRIMARY KEY, ZOWNER INTEGER, ZADDRESS TEXT
        );
        """
    )
    conn.execute("INSERT INTO ZABCDRECORD VALUES (1, 'Jane', 'Doe', NULL)")
    conn.execute("INSERT INTO ZABCDRECORD VALUES (2, NULL, NULL, 'Acme Inc')")
    conn.execute("INSERT INTO ZABCDPHONENUMBER VALUES (1, 1, '+1 (555) 123-4567')")
    conn.execute("INSERT INTO ZABCDEMAILADDRESS VALUES (1, 2, 'Hello@Acme.com')")
    conn.commit()
    conn.close()


@pytest.fixture
def chat_db(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    _build_chat_db(cache_dir / "chat.db")
    monkeypatch.setattr(config, "cache_dir", str(cache_dir))
    db.clear_decoder_cache()
    # clear_contacts_cache is added in a later task; guard so the fixture works
    # at every stage of implementation.
    if hasattr(db, "clear_contacts_cache"):
        db.clear_contacts_cache()
    yield cache_dir


@pytest.fixture
def addressbook(tmp_path):
    path = tmp_path / "AddressBook-v22.abcddb"
    _build_addressbook(path)
    return path
