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
