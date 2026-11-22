"""Streaming header-aware DINO CSV reader."""
import csv
from collections.abc import Iterator
from typing import TextIO
from dino2gtfs.domain.errors import InputError

def read_din(stream: TextIO, delimiter: str = ";") -> Iterator[dict[str, str]]:
    if len(delimiter) != 1:
        raise InputError("DINO delimiter must be a single character")
    
    reader = csv.DictReader(stream, delimiter=delimiter)
    if not reader.fieldnames:
        raise InputError("DINO file has no header")
    
    if len(set(reader.fieldnames)) != len(reader.fieldnames):
        raise InputError("DINO file contains duplicate header names")
    
    for line_number, row in enumerate(reader, start=2):
        if None in row:
            raise InputError(f"Too many fields in DINO row {line_number}")
        
        yield {key: value or "" for key, value in row.items()}
