# Messages Viewer — Fixes & Contacts Foundation

**Date:** 2026-05-29
**Status:** Approved design, pending implementation plan

## Context

The Messages Viewer is a Python/FastAPI app that reads a backed-up macOS
Messages database (`chat.db` + `Attachments/`) from a NAS and renders a
Messages-style UI. It is deployed and reading the database, but has several
bugs and a missing feature. This spec covers four work items.

Relevant files:
- `app/db.py` — SQLite queries, Apple timestamp conversion, attributedBody decoder
- `app/main.py` — FastAPI routes / API
- `app/static/js/app.js` — frontend rendering
- `app/config.py` — data dir / cache paths
- `app/cache.py` — local caching of chat.db from NAS

## Sequencing

1. Date / pagination fix (small, confirmed bug, highest daily impact)
2. Media gallery (self-contained UI change; fixes disappearing-text bug)
3. Contacts via AddressBook import (largest; unlocks chat merging)

Items 2 and 3 are independent. Contacts is the foundation that later resolves
the SMS/iMessage chat split.

---

## Item 1 — Date / pagination fix

Two distinct bugs:

### (a) Missing recent messages
`get_chat_messages` (`app/db.py:190`) uses `ORDER BY m.date ASC LIMIT 2000
OFFSET 0`. For any conversation with >2000 messages this returns the *oldest*
2000 and cuts off everything recent. The frontend scrolls to the bottom, but
the bottom is message #2000, not the latest message.

**Fix:** Fetch the newest N with `ORDER BY m.date DESC LIMIT ?`, then reverse
the list in Python so the thread still renders oldest→newest (latest at the
bottom). "Load older" pagination becomes a backward offset from the newest.

### (b) Wrong timestamps on a subset of messages
`apple_ts_to_unix` (`app/db.py:29`) uses a `ts > 10**12` heuristic to decide
seconds vs. nanoseconds. Some message times are wrong.

**Fix:** Verify against real values from the DB during implementation. Apple
nanosecond timestamps are ~10¹⁸; legacy seconds are ~10⁸–10⁹, so a threshold
near 10¹² is sound — confirm with actual data. Make timezone rendering
explicit: Apple stores UTC; the browser renders in local time via
`new Date(unixSec*1000)`, which is correct as long as the conversion to a Unix
timestamp is correct. No timezone math should be applied server-side.

**Acceptance:** Entering a long conversation shows the most recent messages at
the bottom. Spot-checked message timestamps match the real send/receive times.

---

## Item 2 — Media gallery

Replaces the broken "View attachments" filter button.

**Current bug:** `selectChat` filters `messages.filter((m) =>
m.attachment_count > 0)` (`app/static/js/app.js:142`), which deletes all
text-only messages from the thread when toggled — the cause of "conversation
body disappears."

**Design:**
- Remove the `messages.filter()` logic entirely.
- Inline attachments always render in the thread (already the behavior).
- The top control becomes a **"Media"** button. Clicking it opens a gallery
  panel: a grid of all images/videos/files in the current conversation,
  newest first. The main thread is left intact. Closing the gallery returns to
  the thread.
- Backend: new `get_chat_attachments(chat_id)` in `app/db.py` returning all
  attachments for the conversation (joined via `chat_message_join` →
  `message_attachment_join` → `attachment`), newest first. New route
  `GET /api/chats/{chat_id}/attachments`.

**Acceptance:** Toggling Media never removes thread text. Gallery shows all
conversation media. Inline attachments still appear in the thread regardless of
gallery state.

---

## Item 3 — Contacts via AddressBook import

**Goal:** Map raw handles (`+15551234`, `john@icloud.com`) to real names.
Foundation for merging SMS/iMessage chats (Issue 1 below).

**Design:**
- New module `app/contacts.py` reading `AddressBook-v22.abcddb` (SQLite) from
  the backup. Source tables: `ZABCDRECORD` (people), `ZABCDPHONENUMBER`,
  `ZABCDEMAILADDRESS`.
- Build a **handle → person** mapping:
  - Normalize phone numbers (strip formatting; match on last 7–10 digits).
  - Normalize emails (lowercase, trim).
  - Each handle maps to a display name + a stable person id.
- Setup UI (`app/main.py` `/setup`, `setup.html`): add an optional AddressBook
  path field beside the existing chat.db path. Persist in `config.py`.
- Apply at the **display layer**: chat list names and message sender labels
  show resolved names instead of raw handles. The mapping lives behind a small
  lookup so the source can be swapped later.

**Acceptance:** With an AddressBook path configured, known handles render as
contact names in the chat list and message bubbles. Unknown handles fall back
to the raw identifier. No AddressBook configured → behaves as today.

### Issue 1 (SMS/iMessage split) — follow-up
Once handles resolve to persons, chats can optionally be grouped by person.
This ships as a follow-up toggle after the mapping lands, to keep this
increment focused. Not in scope for the initial contacts implementation.

---

## Constraints

- Source data is read-only; never modify the NAS backup.
- AddressBook DB, like chat.db, should be read via the existing read-only /
  cached access pattern where applicable.
- Follow existing code patterns in `app/db.py` and `app/main.py`.
