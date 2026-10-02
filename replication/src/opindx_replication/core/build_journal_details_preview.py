"""Pure helpers extracted unchanged from the validated September pipeline."""
import re
import json
import unicodedata
from urllib.parse import urlparse, parse_qs
def normalized_title(value):
    return re.sub(r"[^\w]+", "", unicodedata.normalize("NFKC", value).casefold())

def primary_id(title, members):
    """Display choice only: exactly one title match; never changes journal identity."""
    name = normalized_title(title)
    hits = [m.journal_id for m in members if name and name in
            {normalized_title(m.original_title), normalized_title(m.international_title)}]
    return hits[0] if len(hits) == 1 else ""

def register_ids(value):
    ids = []
    for text in str(value or "").split(";"):
        if not text.strip():
            continue
        url = urlparse(text.strip())
        values = parse_qs(url.query).get("id", [])
        if url.hostname != "kanalregister.hkdir.no" or len(values) != 1 or not values[0].isdigit():
            raise ValueError("Invalid register URL")
        ids.append(values[0])
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate register IDs")
    return ids

def register_details(row, roster):
    ids = register_ids(row.norwegian_register_url)
    if bool(ids) != bool(row.in_n):
        raise ValueError("Register membership and URLs disagree")
    members = [roster[(int(row.score_year), key)] for key in ids]
    expected_fields = {m.field.strip() for m in members if m.field.strip()}
    existing_fields = {s.strip() for s in str(row.norwegian_field or "").split("|") if s.strip()}
    if expected_fields != existing_fields:
        raise ValueError("Per-ID fields differ from exported annual fields")
    if len(members) < 2:
        return "", ""
    entries = [{"id": m.journal_id, "field": m.field,
                "title": m.international_title or m.original_title} for m in members]
    return json.dumps(entries, ensure_ascii=False, separators=(",", ":")), primary_id(row.title, members)
