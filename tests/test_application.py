from unittest.mock import Mock

import pytest

from dino2gtfs.application.convert import convert_archive
from dino2gtfs.infrastructure.sqlite import temporary_database


def test_conversion_delegates_imported_database(tmp_path):
    profile = Mock()
    profile.converter.convert.side_effect = RuntimeError("stub")
    destination = tmp_path / "out.zip"
    settings = {}
    with temporary_database() as database:
        with pytest.raises(RuntimeError, match="stub"):
            convert_archive(profile, settings, database, destination)
        profile.converter.convert.assert_called_once_with(database, destination, settings)
