import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import config

log = logging.getLogger(__name__)


@dataclass
class CacheStatus:
    cached: bool
    source_path: Optional[str]
    cache_path: str
    last_refresh: Optional[float]
    source_size: Optional[int]


def refresh_chat_db_cache() -> bool:
    """Copy chat.db (and SQLite -wal/-shm sidecars) from the configured data_dir
    to the local cache_dir. Read queries hit the local copy. Idempotent."""
    src = config.chat_db_path
    if src is None or not src.exists():
        log.warning("refresh_chat_db_cache: no source chat.db configured/found")
        return False

    cache_dir = Path(config.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    dst = config.cache_db_path

    t0 = time.monotonic()
    tmp = dst.with_suffix(".db.tmp")
    shutil.copy2(src, tmp)
    tmp.replace(dst)

    # Bring sidecar files along — remove stale ones from the cache if source
    # no longer has them.
    for sidecar in ("-wal", "-shm"):
        src_side = src.with_name(src.name + sidecar)
        dst_side = dst.with_name(dst.name + sidecar)
        if src_side.exists():
            shutil.copy2(src_side, dst_side)
        elif dst_side.exists():
            dst_side.unlink()

    elapsed = time.monotonic() - t0
    log.info("Cached chat.db (%.1f MB) from %s in %.1fs",
             src.stat().st_size / 1_000_000, src, elapsed)
    return True


def cache_status() -> CacheStatus:
    src = config.chat_db_path
    dst = config.cache_db_path
    return CacheStatus(
        cached=dst.exists(),
        source_path=str(src) if src else None,
        cache_path=str(dst),
        last_refresh=dst.stat().st_mtime if dst.exists() else None,
        source_size=src.stat().st_size if (src and src.exists()) else None,
    )


def invalidate_cache() -> None:
    dst = config.cache_db_path
    for path in (dst, dst.with_name(dst.name + "-wal"), dst.with_name(dst.name + "-shm")):
        if path.exists():
            path.unlink()
