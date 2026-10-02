"""Read annual Norwegian membership without assigning OpenAlex identities.

Eligibility follows the existing register contract: Series 0/1, that score
year's Level 1/2, and no cessation before the score year. A cessation year is
inclusive. The source is a dated register CSV, not a previously matched roster.

This module only reads its input and returns records. It never writes a roster,
downloads metadata, or consults the research pipeline or OpenAlex caches.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable


DEFAULT_YEARS = (2022, 2023, 2024, 2025, 2026)
BASE_COLUMNS = (
    "journal_id",
    "Original Title",
    "International Title",
    "Print ISSN",
    "Online ISSN",
    "NPI Academic Discipline",
    "NPI Scientific Field",
    "Series",
    "Ceased",
)
_ISSN = re.compile(r"[0-9]{4}-?[0-9]{3}[0-9X]\Z")


@dataclass(frozen=True)
class NorwegianMembership:
    score_year: int
    journal_id: str
    norwegian_level: int
    print_issn: str
    online_issn: str
    original_title: str
    international_title: str
    discipline: str
    field: str
    series: str
    ceased_year: int | None


def normalize_issn(value: str) -> str:
    """Normalize ISSN presentation, preserving blanks and source spelling.

    Syntax is checked, but the checksum is deliberately not used to discard a
    register record. Identity matching and identifier-quality review are later
    steps and must retain access to syntactically valid source identifiers.
    """
    if not isinstance(value, str):
        raise ValueError("ISSN must be a string")
    compact = re.sub(r"\s+", "", value).upper()
    compact = compact.replace("\u2010", "-").replace("\u2011", "-")
    if not compact:
        return ""
    if not _ISSN.fullmatch(compact):
        raise ValueError(f"Invalid ISSN syntax: {value!r}")
    digits = compact.replace("-", "")
    return f"{digits[:4]}-{digits[4:]}"


def _score_years(years: Iterable[int]) -> tuple[int, ...]:
    selected = tuple(years)
    if not selected or any(type(year) is not int or not 1900 <= year <= 9997 for year in selected):
        raise ValueError("years must contain integer score years between 1900 and 9997")
    if len(set(selected)) != len(selected):
        raise ValueError("years must not contain duplicates")
    return tuple(sorted(selected))


def _read_rosters(reader: csv.DictReader, years: tuple[int, ...]) -> tuple[NorwegianMembership, ...]:
    required = (*BASE_COLUMNS, *(f"Level {year}" for year in years))
    columns = reader.fieldnames
    if not columns or any(not isinstance(name, str) or not name.strip() for name in columns):
        raise ValueError("Register CSV must have nonblank column names")
    if len(set(columns)) != len(columns):
        raise ValueError("Register CSV has duplicate column names")
    missing = sorted(set(required).difference(columns))
    if missing:
        raise ValueError(f"Register CSV is missing required columns: {', '.join(missing)}")

    records: dict[tuple[int, str], NorwegianMembership] = {}
    seen: dict[tuple[int, str], tuple[object, ...]] = {}
    first_line: dict[tuple[int, str], int] = {}
    for raw in reader:
        line = reader.line_num
        if reader.restkey in raw or any(not isinstance(raw.get(name), str) for name in required):
            raise ValueError(f"Register CSV line {line} has missing or excess cells")
        row = {name: raw[name].strip() for name in required}
        journal_id = row["journal_id"]
        if not journal_id:
            raise ValueError(f"Register CSV line {line} has a blank journal_id")
        ceased = row["Ceased"]
        if ceased and (not re.fullmatch(r"[0-9]{4}", ceased) or int(ceased) == 0):
            raise ValueError(f"Register CSV line {line} has an invalid Ceased year: {ceased!r}")
        ceased_year = int(ceased) if ceased else None
        try:
            print_issn = normalize_issn(row["Print ISSN"])
            online_issn = normalize_issn(row["Online ISSN"])
        except ValueError as exc:
            raise ValueError(f"Register CSV line {line}: {exc}") from exc

        for year in years:
            level = row[f"Level {year}"]
            key = (year, journal_id)
            signature = (
                level, row["Series"], ceased_year, print_issn, online_issn,
                row["Original Title"], row["International Title"],
                row["NPI Academic Discipline"], row["NPI Scientific Field"],
            )
            if key in seen and seen[key] != signature:
                raise ValueError(
                    f"Ambiguous duplicate journal_id/year {journal_id!r}/{year} "
                    f"on CSV lines {first_line[key]} and {line}"
                )
            seen[key] = signature
            first_line.setdefault(key, line)
            if row["Series"] not in {"0", "1"} or level not in {"1", "2"}:
                continue
            if ceased_year is not None and ceased_year < year:
                continue
            record = NorwegianMembership(
                score_year=year,
                journal_id=journal_id,
                norwegian_level=int(level),
                print_issn=print_issn,
                online_issn=online_issn,
                original_title=row["Original Title"],
                international_title=row["International Title"],
                discipline=row["NPI Academic Discipline"],
                field=row["NPI Scientific Field"],
                series=row["Series"],
                ceased_year=ceased_year,
            )
            records[key] = record
    return tuple(records[key] for key in sorted(records))


def build_annual_rosters(
    source: str | Path | csv.DictReader,
    years: Iterable[int] = DEFAULT_YEARS,
) -> tuple[NorwegianMembership, ...]:
    """Return eligible journal/year records, ordered by year then journal ID.

    Paths are read as semicolon-delimited UTF-8 (an optional BOM is accepted).
    A supplied ``csv.DictReader`` lets callers use in-memory data or configure
    their own CSV dialect; it is consumed but its underlying stream stays open.
    Required headers and malformed rows fail explicitly. Identical duplicates
    collapse to one record; conflicting source records for a journal/year
    fail, without choosing a level, title, field, or ISSN on the caller's behalf.
    Blank ISSNs remain blank and no OpenAlex match or canonical identity is
    inferred. Years are configurable so the same reader can support history.
    """
    selected = _score_years(years)
    if isinstance(source, (str, Path)):
        with Path(source).open("r", encoding="utf-8-sig", newline="") as stream:
            return _read_rosters(csv.DictReader(stream, delimiter=";", strict=True), selected)
    if not isinstance(source, csv.DictReader):
        raise TypeError("source must be a CSV file path or csv.DictReader")
    return _read_rosters(source, selected)
