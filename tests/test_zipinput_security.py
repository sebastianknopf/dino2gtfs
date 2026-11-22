import pytest
from zipfile import ZipFile
from dino2gtfs.infrastructure.zipinput import din_members
from dino2gtfs.domain.errors import InputError

def test_zip_traversal(tmp_path):
    path = tmp_path / "bad.zip"
    
    with ZipFile(path, "w") as archive:
        archive.writestr("../bad.din", "X\n1\n")
    
    with ZipFile(path) as archive:
        with pytest.raises(InputError):
            list(din_members(archive))
