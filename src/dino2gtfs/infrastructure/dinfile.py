"""Streaming header-aware DINO CSV reader."""
import csv
from collections.abc import Iterator
from typing import TextIO
from dino2gtfs.domain.errors import InputError

def read_din_with_headers(
    stream: TextIO, delimiter: str = ";"
) -> tuple[list[str], Iterator[dict[str, str]]]:
    if len(delimiter) != 1:
        raise InputError("DINO delimiter must be a single character")
    
    reader = csv.DictReader(stream, delimiter=delimiter)
    headers = reader.fieldnames
    if not headers:
        raise InputError("DINO file has no header")
    
    if len(set(headers)) != len(headers):
        raise InputError("DINO file contains duplicate header names")

    def rows() -> Iterator[dict[str, str]]:
        for line_number, row in enumerate(reader, start=2):
            if None in row:
                raise InputError(f"Too many fields in DINO row {line_number}")

            yield {key: value or "" for key, value in row.items()}

    return headers, rows()

def read_din(stream: TextIO, delimiter: str = ";") -> Iterator[dict[str, str]]:
    _, rows = read_din_with_headers(stream, delimiter)
    
    return rows
