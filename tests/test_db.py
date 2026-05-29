from app import db
from tests.conftest import APPLE_EPOCH


def test_nanosecond_timestamp_converts_to_unix():
    # 600,000,000 seconds after Apple epoch, expressed in nanoseconds.
    ns = 600_000_000 * 1_000_000_000
    assert db.apple_ts_to_unix(ns) == APPLE_EPOCH + 600_000_000


def test_legacy_seconds_timestamp_converts_to_unix():
    secs = 600_000_000  # already seconds since Apple epoch
    assert db.apple_ts_to_unix(secs) == APPLE_EPOCH + 600_000_000


def test_zero_and_none_return_none():
    assert db.apple_ts_to_unix(0) is None
    assert db.apple_ts_to_unix(None) is None


def test_get_chat_messages_returns_newest_when_limited(chat_db):
    # With limit=2, we want the 2 NEWEST messages, ordered oldest->newest
    # for display: message 4 then message 5.
    msgs = db.get_chat_messages(chat_id=1, limit=2, offset=0)
    assert [m["text"] for m in msgs] == ["message 4", "message 5"]


def test_get_chat_messages_full_thread_is_chronological(chat_db):
    msgs = db.get_chat_messages(chat_id=1, limit=100, offset=0)
    assert [m["text"] for m in msgs] == [
        "message 1", "message 2", "message 3", "message 4", "message 5",
    ]


def test_get_chat_messages_paginates_older_batches(chat_db):
    # Progressive loading: offset counts back from the newest. Each batch is
    # ordered oldest->newest within itself, and batches don't overlap.
    newest = db.get_chat_messages(chat_id=1, limit=2, offset=0)
    assert [m["text"] for m in newest] == ["message 4", "message 5"]
    older = db.get_chat_messages(chat_id=1, limit=2, offset=2)
    assert [m["text"] for m in older] == ["message 2", "message 3"]
    oldest = db.get_chat_messages(chat_id=1, limit=2, offset=4)
    assert [m["text"] for m in oldest] == ["message 1"]
    # Past the end -> empty, signalling "all loaded".
    assert db.get_chat_messages(chat_id=1, limit=2, offset=6) == []


def test_get_chat_attachments_returns_conversation_media(chat_db):
    atts = db.get_chat_attachments(chat_id=1)
    assert len(atts) == 1
    a = atts[0]
    assert a["mime_type"] == "image/jpeg"
    assert a["transfer_name"] == "x.jpg"
    assert a["date"] is not None


def test_messages_resolve_sender_name(chat_db, addressbook, monkeypatch):
    from app.config import config
    monkeypatch.setattr(config, "addressbook_path", str(addressbook))
    db.clear_contacts_cache()
    msgs = db.get_chat_messages(chat_id=1, limit=100, offset=0)
    # handle +15551234567 -> normalized 5551234567 -> "Jane Doe"
    assert msgs[0]["sender_name"] == "Jane Doe"
