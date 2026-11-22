"""Conversion use case and temporary database orchestration."""
from pathlib import Path
from collections.abc import Mapping
from typing import Any
from dino2gtfs.application.interfaces import Profile
from dino2gtfs.infrastructure.sqlite import temporary_database

def convert_archive(profile: Profile, settings: Mapping[str, Any], source: Path, destination: Path) -> None:
    """Stage the input, then delegate conversion to the selected profile."""
    with temporary_database() as database:
        profile.importer.import_archive(source, database, settings)
        profile.converter.convert(database, destination, settings)
