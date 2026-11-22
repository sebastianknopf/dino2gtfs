from zipfile import ZipFile
from dino2gtfs.infrastructure.zipinput import din_members

def test_members(tmp_path):
    path = tmp_path / "input.zip"
    with ZipFile(path, "w") as archive:
        archive.writestr("stop.din", "STOP_NR\n1\n")
        archive.writestr("readme.txt", "hello")
    with ZipFile(path) as archive:
        assert [m.filename for m in din_members(archive)] == ["stop.din"]
