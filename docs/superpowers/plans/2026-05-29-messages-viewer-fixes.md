# Messages Viewer Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the date/pagination bug, replace the broken attachments filter with a media gallery, and add AddressBook-based contact name resolution.

**Architecture:** Python/FastAPI backend reads a cached copy of macOS `chat.db` (and optionally `AddressBook-v22.abcddb`) via read-only SQLite; a vanilla-JS frontend renders the Messages UI. Date fix and contacts live in `app/db.py` + a new `app/contacts.py`; the gallery is a new backend query/route plus frontend changes in `app/static/js/app.js`.

**Tech Stack:** Python 3, FastAPI, SQLite (stdlib `sqlite3`), Jinja2 templates, vanilla JS. Tests with pytest.

---

## File Structure

- `requirements-dev.txt` — create: pytest + httpx for the test client
- `tests/conftest.py` — create: fixtures building temp `chat.db` and `AddressBook-v22.abcddb`
- `tests/test_db.py` — create: timestamp, pagination, attachment-query tests
- `tests/test_contacts.py` — create: AddressBook parsing + handle normalization tests
- `app/db.py` — modify: pagination fix, `get_chat_attachments`, contacts integration
- `app/contacts.py` — create: AddressBook reader + handle normalization
- `app/config.py` — modify: persist `addressbook_path`
- `app/main.py` — modify: attachments route, setup form handling
- `app/templates/index.html` — modify: Media button
- `app/templates/setup.html` — modify: optional AddressBook path field
- `app/static/js/app.js` — modify: remove filter, add gallery

---

## Task 1: Test infrastructure

**Files:**
- Create: `requirements-dev.txt`
- Create: `tests/conftest.py`

- [ ] **Step 1: Create dev requirements**

Create `requirements-dev.txt`:

```
-r requirements.txt
pytest==8.3.3
httpx==0.27.2
```

- [ ] **Step 2: Install**

Run: `pip install -r requirements-dev.txt`
Expected: pytest and httpx install successfully.

- [ ] **Step 3: Create the chat.db fixture**

Create `tests/conftest.py`. This builds a minimal subset of the Apple Messages
schema with known rows, and points `config.cache_dir` at the temp dir so
`db.get_conn()` reads the test database.

```python
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


@pytest.fixture
def chat_db(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    _build_chat_db(cache_dir / "chat.db")
    monkeypatch.setattr(config, "cache_dir", str(cache_dir))
    db.clear_decoder_cache()
    db.clear_contacts_cache()
    yield cache_dir
```

- [ ] **Step 4: Verify pytest collects**

Run: `pytest --collect-only`
Expected: 0 tests collected, no import or fixture errors.

- [ ] **Step 5: Commit**

```bash
git add requirements-dev.txt tests/conftest.py
git commit -m "test: add pytest infra and chat.db fixture"
```

---

## Task 2: Timestamp conversion guard

Lock in correct Apple-timestamp conversion with tests. The conversion logic is
likely already correct; these tests prove it and guard against regressions
while we change pagination (the real cause of "wrong" dates).

**Files:**
- Create: `tests/test_db.py`
- Modify: `app/db.py:29-35` (only if a test fails)

- [ ] **Step 1: Write failing tests**

Create `tests/test_db.py`:

```python
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
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/test_db.py -k timestamp -v`
Expected: PASS. If any FAIL, the conversion is wrong — fix `apple_ts_to_unix`
so both nanosecond and seconds inputs map to `APPLE_EPOCH + 600_000_000`, then
re-run until green. Do not change behavior that already passes.

- [ ] **Step 3: Commit**

```bash
git add tests/test_db.py app/db.py
git commit -m "test: guard Apple timestamp conversion"
```

---

## Task 3: Pagination fix — show newest messages

**Files:**
- Modify: `app/db.py:190-228` (`get_chat_messages`)
- Modify: `tests/test_db.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_db.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_db.py -k newest -v`
Expected: FAIL — current code returns "message 1", "message 2" (oldest).

- [ ] **Step 3: Fix the query**

In `app/db.py`, change the `ORDER BY` in `get_chat_messages` from `ASC` to
`DESC` and reverse the rows before building results. Replace lines 204-209:

```python
        WHERE cmj.chat_id = ?
        ORDER BY m.date DESC
        LIMIT ? OFFSET ?
    """
    with get_conn() as conn:
        rows = conn.execute(msg_sql, (chat_id, limit, offset)).fetchall()
        rows = list(reversed(rows))  # newest-N fetched desc -> display oldest->newest
        message_ids = [r["message_id"] for r in rows]
        attachments_by_msg = _attachments_for(conn, message_ids)
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_db.py -v`
Expected: PASS (all db tests).

- [ ] **Step 5: Commit**

```bash
git add app/db.py tests/test_db.py
git commit -m "fix: load newest messages first so recent messages are visible"
```

---

## Task 4: Media gallery backend

**Files:**
- Modify: `app/db.py` (add `get_chat_attachments`)
- Modify: `app/main.py` (add route)
- Modify: `tests/test_db.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_db.py`:

```python
def test_get_chat_attachments_returns_conversation_media(chat_db):
    atts = db.get_chat_attachments(chat_id=1)
    assert len(atts) == 1
    a = atts[0]
    assert a["mime_type"] == "image/jpeg"
    assert a["transfer_name"] == "x.jpg"
    assert a["date"] is not None
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_db.py -k attachments -v`
Expected: FAIL — `get_chat_attachments` not defined.

- [ ] **Step 3: Implement the query**

Add to `app/db.py` after `get_chat_messages` (before `_attachments_for`):

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_db.py -k attachments -v`
Expected: PASS.

- [ ] **Step 5: Add the route**

In `app/main.py`, add after `api_chat_messages` (after line 133):

```python
@app.get("/api/chats/{chat_id}/attachments")
def api_chat_attachments(chat_id: int, _: None = Depends(auth_dep)) -> list[dict]:
    return db.get_chat_attachments(chat_id)
```

- [ ] **Step 6: Commit**

```bash
git add app/db.py app/main.py tests/test_db.py
git commit -m "feat: add conversation attachments query and route for media gallery"
```

---

## Task 5: Media gallery frontend

Remove the thread filter (the disappearing-text bug) and turn the control into
a Media gallery toggle.

**Files:**
- Modify: `app/templates/index.html:18-20`
- Modify: `app/static/js/app.js`

- [ ] **Step 1: Replace the filter control with a Media button**

In `app/templates/index.html`, replace lines 18-20:

```html
      <div class="filters">
        <button id="media-toggle" class="ghost small" disabled title="Show photos, videos and files in this conversation">Media</button>
      </div>
```

- [ ] **Step 2: Remove the filter logic in app.js**

In `app/static/js/app.js`, delete the `filterAttachments: false,` line from
`state` (line 5). In `els` (lines 8-15), replace the `filterAttachments`
reference:

```javascript
const els = {
  chatList: document.getElementById("chat-list"),
  thread: document.getElementById("thread"),
  threadTitle: document.getElementById("thread-title"),
  threadSubtitle: document.getElementById("thread-subtitle"),
  search: document.getElementById("search"),
  mediaToggle: document.getElementById("media-toggle"),
};
```

In `selectChat` (lines 140-145), remove the filter block so it reads:

```javascript
  let messages = await api(`/api/chats/${chatId}/messages?limit=2000`);
  if (!messages) return;
  els.mediaToggle.disabled = false;
  renderMessages(messages);
```

- [ ] **Step 3: Replace the old filter listener with the gallery toggle**

In `app/static/js/app.js`, replace the `els.filterAttachments` listener
(lines 207-210) with:

```javascript
let galleryOpen = false;

async function openGallery() {
  if (!state.selectedChatId) return;
  const atts = await api(`/api/chats/${state.selectedChatId}/attachments`);
  if (!atts) return;
  if (!atts.length) {
    els.thread.innerHTML = `<div class="empty-state">No media in this conversation</div>`;
    return;
  }
  const cells = atts.map((a) => `<div class="gallery-cell">${attachmentHtml(a)}</div>`).join("");
  els.thread.innerHTML = `<div class="media-gallery">${cells}</div>`;
}

els.mediaToggle.addEventListener("click", () => {
  galleryOpen = !galleryOpen;
  els.mediaToggle.classList.toggle("active", galleryOpen);
  if (galleryOpen) {
    openGallery();
  } else if (state.selectedChatId) {
    selectChat(state.selectedChatId);
  }
});
```

- [ ] **Step 4: Reset gallery state when switching chats**

In `app/static/js/app.js`, at the top of `selectChat` (after line 130
`state.selectedChatId = chatId;`), add:

```javascript
  galleryOpen = false;
  if (els.mediaToggle) els.mediaToggle.classList.remove("active");
```

- [ ] **Step 5: Add gallery CSS**

Append to `app/static/css/app.css`:

```css
.media-gallery {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 8px;
  padding: 12px;
}
.gallery-cell img,
.gallery-cell video {
  width: 100%;
  height: 120px;
  object-fit: cover;
  border-radius: 8px;
}
#media-toggle.active {
  background: var(--accent, #0a84ff);
  color: #fff;
}
```

- [ ] **Step 6: Manual verification**

Run the app, open a conversation, confirm: (a) text is always visible,
(b) clicking Media shows a grid of media, (c) clicking Media again returns to
the thread with text intact, (d) switching chats resets to the thread view.

- [ ] **Step 7: Commit**

```bash
git add app/templates/index.html app/static/js/app.js app/static/css/app.css
git commit -m "feat: replace broken attachments filter with media gallery"
```

---

## Task 6: Contacts — AddressBook reader & normalization

**Files:**
- Create: `app/contacts.py`
- Create: `tests/test_contacts.py`
- Modify: `tests/conftest.py` (add AddressBook fixture)

- [ ] **Step 1: Add the AddressBook fixture**

Add to `tests/conftest.py`:

```python
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
def addressbook(tmp_path):
    path = tmp_path / "AddressBook-v22.abcddb"
    _build_addressbook(path)
    return path
```

- [ ] **Step 2: Write failing tests**

Create `tests/test_contacts.py`:

```python
from app import contacts


def test_normalize_phone_keeps_last_ten_digits():
    assert contacts.normalize_handle("+1 (555) 123-4567") == "5551234567"


def test_normalize_email_lowercases():
    assert contacts.normalize_handle("Hello@Acme.com") == "hello@acme.com"


def test_load_contacts_maps_phone_to_full_name(addressbook):
    mapping = contacts.load_contacts(addressbook)
    assert mapping["5551234567"] == "Jane Doe"


def test_load_contacts_maps_email_to_org(addressbook):
    mapping = contacts.load_contacts(addressbook)
    assert mapping["hello@acme.com"] == "Acme Inc"


def test_resolve_falls_back_to_raw_handle(addressbook):
    mapping = contacts.load_contacts(addressbook)
    assert contacts.resolve("+15559999999", mapping) == "+15559999999"
    assert contacts.resolve("+1 (555) 123-4567", mapping) == "Jane Doe"
```

- [ ] **Step 3: Run to verify failure**

Run: `pytest tests/test_contacts.py -v`
Expected: FAIL — `app.contacts` not found.

- [ ] **Step 4: Implement the module**

Create `app/contacts.py`:

```python
import sqlite3
from pathlib import Path
from typing import Optional


def _normalize_phone(raw: str) -> Optional[str]:
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return None
    return digits[-10:] if len(digits) >= 10 else digits


def _normalize_email(raw: str) -> Optional[str]:
    cleaned = raw.strip().lower()
    return cleaned or None


def normalize_handle(handle: Optional[str]) -> Optional[str]:
    """Normalize a Messages handle (phone or email) to a lookup key."""
    if not handle:
        return None
    if "@" in handle:
        return _normalize_email(handle)
    return _normalize_phone(handle)


def load_contacts(addressbook_path: Optional[Path]) -> dict[str, str]:
    """Return {normalized_handle: display_name} from an AddressBook DB."""
    if not addressbook_path or not Path(addressbook_path).exists():
        return {}
    uri = f"file:{addressbook_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    mapping: dict[str, str] = {}
    try:
        names: dict[int, str] = {}
        for r in conn.execute(
            "SELECT Z_PK, ZFIRSTNAME, ZLASTNAME, ZORGANIZATION FROM ZABCDRECORD"
        ):
            first = r["ZFIRSTNAME"] or ""
            last = r["ZLASTNAME"] or ""
            name = (first + " " + last).strip() or (r["ZORGANIZATION"] or "")
            if name:
                names[r["Z_PK"]] = name
        for r in conn.execute("SELECT ZOWNER, ZFULLNUMBER FROM ZABCDPHONENUMBER"):
            name = names.get(r["ZOWNER"])
            key = _normalize_phone(r["ZFULLNUMBER"] or "")
            if name and key:
                mapping[key] = name
        for r in conn.execute("SELECT ZOWNER, ZADDRESS FROM ZABCDEMAILADDRESS"):
            name = names.get(r["ZOWNER"])
            key = _normalize_email(r["ZADDRESS"] or "")
            if name and key:
                mapping[key] = name
    finally:
        conn.close()
    return mapping


def resolve(handle: Optional[str], mapping: dict[str, str]) -> Optional[str]:
    """Resolve a handle to a contact name, falling back to the raw handle."""
    key = normalize_handle(handle)
    if key and key in mapping:
        return mapping[key]
    return handle
```

- [ ] **Step 5: Run to verify pass**

Run: `pytest tests/test_contacts.py -v`
Expected: PASS (all 5 tests).

- [ ] **Step 6: Commit**

```bash
git add app/contacts.py tests/test_contacts.py tests/conftest.py
git commit -m "feat: add AddressBook contact reader and handle normalization"
```

---

## Task 7: Contacts — config persistence

**Files:**
- Modify: `app/config.py`

- [ ] **Step 1: Add addressbook_path to config**

In `app/config.py`, in `__init__` add `self.addressbook_path` after
`self.data_dir` (line 12):

```python
        self.data_dir: Optional[str] = None
        self.addressbook_path: Optional[str] = None
```

In `load` (after line 19):

```python
            self.data_dir = data.get("data_dir")
            self.addressbook_path = data.get("addressbook_path")
```

In `save` (replace line 22):

```python
        CONFIG_PATH.write_text(
            json.dumps(
                {"data_dir": self.data_dir, "addressbook_path": self.addressbook_path},
                indent=2,
            )
        )
```

Add a setter after `set_data_dir` (after line 26):

```python
    def set_addressbook_path(self, path: Optional[str]) -> None:
        self.addressbook_path = path
        self.save()
```

- [ ] **Step 2: Verify it imports**

Run: `python -c "from app.config import config; print(config.addressbook_path)"`
Expected: prints `None` (or a configured path), no error.

- [ ] **Step 3: Commit**

```bash
git add app/config.py
git commit -m "feat: persist optional AddressBook path in config"
```

---

## Task 8: Contacts — integrate name resolution into queries

**Files:**
- Modify: `app/db.py` (add contacts cache, resolve names in queries)
- Modify: `tests/test_db.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_db.py`. This points the contacts cache at the AddressBook
fixture and checks that the message sender resolves to a name.

```python
def test_messages_resolve_sender_name(chat_db, addressbook, monkeypatch):
    from app.config import config
    monkeypatch.setattr(config, "addressbook_path", str(addressbook))
    db.clear_contacts_cache()
    msgs = db.get_chat_messages(chat_id=1, limit=100, offset=0)
    # handle +15551234567 -> normalized 5551234567 -> "Jane Doe"
    assert msgs[0]["sender_name"] == "Jane Doe"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_db.py -k sender_name -v`
Expected: FAIL — `KeyError: 'sender_name'`.

- [ ] **Step 3: Add the contacts cache to db.py**

In `app/db.py`, add to the imports near the top (after `from . import cache`):

```python
from . import cache, contacts
```

After the `APPLE_EPOCH_OFFSET` constant (line 10), add:

```python
_contacts_cache: Optional[dict[str, str]] = None


def _get_contacts() -> dict[str, str]:
    global _contacts_cache
    if _contacts_cache is None:
        path = config.addressbook_path
        _contacts_cache = contacts.load_contacts(Path(path)) if path else {}
    return _contacts_cache


def clear_contacts_cache() -> None:
    """Call after the configured AddressBook path changes."""
    global _contacts_cache
    _contacts_cache = None
```

- [ ] **Step 4: Resolve sender names in get_chat_messages**

In `app/db.py` `get_chat_messages`, after building `attachments_by_msg`, load
contacts once and add `sender_name` to each result. Replace the results loop
(lines 213-227) so it reads:

```python
    cmap = _get_contacts()
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
            "sender_name": contacts.resolve(r["sender_id"], cmap),
            "attachment_count": len(atts),
            "attachments": atts,
        })
    return results
```

- [ ] **Step 5: Resolve participant names in list_chats**

In `app/db.py` `list_chats`, replace the results list comprehension (lines
176-187) so participants resolve to names and a name-based display fallback is
used:

```python
    cmap = _get_contacts()
    out = []
    for r in rows:
        participants = (r["participants"] or "").split(",") if r["participants"] else []
        names = [contacts.resolve(p, cmap) for p in participants]
        out.append({
            "chat_id": r["chat_id"],
            "guid": r["guid"],
            "display_name": r["display_name"] or ", ".join(names) or r["chat_identifier"],
            "chat_identifier": r["chat_identifier"],
            "participants": names,
            "last_date": apple_ts_to_unix(r["last_date"]),
            "message_count": r["message_count"],
        })
    return out
```

- [ ] **Step 6: Run to verify pass**

Run: `pytest tests/ -v`
Expected: PASS (all tests across both files).

- [ ] **Step 7: Commit**

```bash
git add app/db.py tests/test_db.py
git commit -m "feat: resolve handles to contact names in chat and message queries"
```

---

## Task 9: Contacts — frontend prefers resolved names

**Files:**
- Modify: `app/static/js/app.js`

- [ ] **Step 1: Use sender_name in bubbles**

In `app/static/js/app.js` `bubbleHtml` (line 111), change the sender line:

```javascript
  const sender = msg.is_from_me ? "" : (msg.sender_name || msg.sender_id || "");
```

- [ ] **Step 2: Manual verification**

With an AddressBook configured, open a conversation: sender labels and chat
list names show contact names where known, raw handles otherwise.

- [ ] **Step 3: Commit**

```bash
git add app/static/js/app.js
git commit -m "feat: display resolved contact names in message bubbles"
```

---

## Task 10: Contacts — setup UI & cache invalidation

**Files:**
- Modify: `app/templates/setup.html`
- Modify: `app/main.py`

- [ ] **Step 1: Add the optional AddressBook field**

In `app/templates/setup.html`, after the data_dir input block (after line 24,
before the `{% if error %}` line), add:

```html
      <label for="addressbook_path">AddressBook database (optional)</label>
      <input
        id="addressbook_path"
        name="addressbook_path"
        type="text"
        placeholder="/mnt/nas/messages-backup/AddressBook-v22.abcddb"
        value="{{ ab_current or '' }}"
      />
```

- [ ] **Step 2: Pass current AddressBook path to the template**

In `app/main.py` `setup_page` (lines 82-86), add `ab_current`:

```python
    return templates.TemplateResponse(
        "setup.html",
        {
            "request": request,
            "current": config.data_dir,
            "ab_current": config.addressbook_path,
            "error": None,
        },
    )
```

- [ ] **Step 3: Handle the AddressBook field on submit**

In `app/main.py` `setup_submit`, change the signature to accept the field and
validate/persist it. Replace the function body up to the existing
`config.set_data_dir` call. The new signature and validation:

```python
@app.post("/setup")
def setup_submit(
    request: Request,
    data_dir: str = Form(...),
    addressbook_path: str = Form(""),
    _: None = Depends(auth_dep),
) -> Response:
    path = Path(data_dir).expanduser()
    chat_db = path / "chat.db"
    if not chat_db.exists():
        return templates.TemplateResponse(
            "setup.html",
            {
                "request": request,
                "current": data_dir,
                "ab_current": addressbook_path,
                "error": f"chat.db not found at {chat_db}",
            },
            status_code=400,
        )
    ab = addressbook_path.strip()
    if ab:
        ab_path = Path(ab).expanduser()
        if not ab_path.exists():
            return templates.TemplateResponse(
                "setup.html",
                {
                    "request": request,
                    "current": data_dir,
                    "ab_current": addressbook_path,
                    "error": f"AddressBook not found at {ab_path}",
                },
                status_code=400,
            )
        config.set_addressbook_path(str(ab_path))
    else:
        config.set_addressbook_path(None)
    config.set_data_dir(str(path))
    cache.invalidate_cache()
    db.clear_decoder_cache()
    db.clear_contacts_cache()
    try:
        cache.refresh_chat_db_cache()
    except Exception as exc:
        log.warning("Cache refresh after setup failed: %s", exc)
    return RedirectResponse("/", status_code=303)
```

- [ ] **Step 4: Run the full suite**

Run: `pytest tests/ -v`
Expected: PASS (no regressions).

- [ ] **Step 5: Manual verification**

Start the app, go to `/setup`, enter the AddressBook path, save. Confirm the
chat list and message senders show contact names.

- [ ] **Step 6: Commit**

```bash
git add app/templates/setup.html app/main.py
git commit -m "feat: configure AddressBook path in setup and refresh contacts"
```

---

## Self-Review Notes

- **Spec coverage:** Item 1 dates → Tasks 2, 3. Item 2 gallery → Tasks 4, 5.
  Item 3 contacts → Tasks 6-10. SMS/iMessage merge intentionally excluded
  (deferred follow-up per spec).
- **Type consistency:** `clear_contacts_cache`, `_get_contacts`,
  `load_contacts`, `normalize_handle`, `resolve` names are consistent across
  tasks. `sender_name` field name is consistent between db (Task 8) and
  frontend (Task 9). `media-toggle` id matches between HTML (Task 5 step 1) and
  JS (Task 5 step 2).
- **Fixture coupling:** `chat_db` fixture handle `+15551234567` normalizes to
  `5551234567`, matching the `addressbook` fixture's phone entry, so the Task 8
  integration test resolves correctly.
