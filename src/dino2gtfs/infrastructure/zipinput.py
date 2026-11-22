"""Secure ZIP member enumeration with streaming access."""
from collections.abc import Iterator
from zipfile import ZipFile, ZipInfo
from pathlib import PurePosixPath
from dino2gtfs.domain.errors import InputError

def din_members(archive: ZipFile) -> Iterator[ZipInfo]:
    seen: set[str] = set()
    
    for member in archive.infolist():
        name = member.filename
        path = PurePosixPath(name)
        
        if member.is_dir():
            continue
        
        if path.is_absolute() or ".." in path.parts or chr(92) in name:
            raise InputError(f"Unsafe ZIP entry: {name}")
        
        if len(path.parts) != 1:
            raise InputError(f"DINO file must be at ZIP root: {name}")
        
        canonical = name.lower()
        if canonical in seen:
            raise InputError(f"Duplicate ZIP entry: {name}")
        
        seen.add(canonical)
        if canonical.endswith(".din"):
            yield member
