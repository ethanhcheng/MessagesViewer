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
