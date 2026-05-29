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
