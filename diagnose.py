"""One-off diagnostic for contacts resolution, chat splitting, and message
coverage. Reuses the app's own code paths.

Run on the server (as root, pointing at the service's config):

    cd /opt/messagesviewer
    MV_CONFIG_PATH=/var/lib/messagesviewer/config.json \
      .venv/bin/python diagnose.py 19175085239

If contacts SHOULD resolve per this report but still don't in the UI, the
service sandbox can't see the file — re-run inside the sandbox:

    systemd-run --pipe --quiet --uid=messagesviewer --gid=messagesviewer \
      -p ProtectHome=true -p ProtectSystem=strict \
      -p ReadWritePaths=/var/lib/messagesviewer \
      -p WorkingDirectory=/opt/messagesviewer \
      -p EnvironmentFile=/etc/messagesviewer.env \
      .venv/bin/python /opt/messagesviewer/diagnose.py 19175085239
"""
import datetime
import os
import sqlite3
import sys
from pathlib import Path

from app import contacts
from app.config import config
from app.db import apple_ts_to_unix

target = sys.argv[1] if len(sys.argv) > 1 else None


def fmt_date(ts):
    u = apple_ts_to_unix(ts)
    return datetime.datetime.utcfromtimestamp(u).isoformat() if u else None


print("=== CONFIG ===")
print("data_dir         :", config.data_dir)
print("addressbook_path :", config.addressbook_path)
print("cache_db_path    :", config.cache_db_path)

print("\n=== ADDRESSBOOK FILE (as seen by THIS process) ===")
ab = config.addressbook_path
mapping = {}
if not ab:
    print("addressbook_path is NOT set in config.json")
else:
    p = Path(ab)
    print("exists  :", p.exists())
    if p.exists():
        print("is_file :", p.is_file())
        print("readable:", os.access(p, os.R_OK))
        try:
            conn = sqlite3.connect(f"file:{ab}?mode=ro", uri=True)
            for t in ("ZABCDRECORD", "ZABCDPHONENUMBER", "ZABCDEMAILADDRESS"):
                try:
                    n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    print(f"  {t:20s}: {n} rows")
                except Exception as e:
                    print(f"  {t:20s}: ERROR {e}")
            conn.close()
        except Exception as e:
            print("  open error:", e)
        mapping = contacts.load_contacts(p)
        print(f"load_contacts -> {len(mapping)} handle->name entries")
        for k, v in list(mapping.items())[:5]:
            print("  sample:", repr(k), "->", repr(v))

print("\n=== TARGET LOOKUP ===")
if target:
    key = contacts.normalize_handle(target)
    print(f"normalize_handle({target!r}) = {key!r}")
    print("in mapping:", key in mapping, "->", mapping.get(key))

print("\n=== CHAT ROWS matching target (split check) ===")
cdb = config.cache_db_path
rows = []
if target and Path(cdb).exists():
    conn = sqlite3.connect(f"file:{cdb}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    digits = "".join(ch for ch in target if ch.isdigit())[-10:]
    like = f"%{digits}%"
    rows = conn.execute(
        "SELECT ROWID, guid, service_name, chat_identifier, display_name "
        "FROM chat WHERE chat_identifier LIKE ? OR guid LIKE ?",
        (like, like),
    ).fetchall()
    for r in rows:
        print(f"  chat {r['ROWID']}: service={r['service_name']!r} "
              f"identifier={r['chat_identifier']!r} display_name={r['display_name']!r}")

    print("\n=== MESSAGE COVERAGE per matching chat ===")
    for r in rows:
        cid = r["ROWID"]
        cnt = conn.execute(
            "SELECT COUNT(*) FROM chat_message_join WHERE chat_id=?", (cid,)
        ).fetchone()[0]
        dr = conn.execute(
            "SELECT MIN(m.date), MAX(m.date) FROM chat_message_join cmj "
            "JOIN message m ON m.ROWID=cmj.message_id WHERE cmj.chat_id=?",
            (cid,),
        ).fetchone()
        print(f"  chat {cid} ({r['service_name']}): {cnt} messages, "
              f"earliest={fmt_date(dr[0])} latest={fmt_date(dr[1])}")
    conn.close()
elif not Path(cdb).exists():
    print("  cache chat.db not found at", cdb)
