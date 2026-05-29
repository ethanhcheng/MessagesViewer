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
