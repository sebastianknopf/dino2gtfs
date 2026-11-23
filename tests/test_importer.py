from zipfile import ZipFile

import pytest

from dino2gtfs.domain.errors import InputError
from dino2gtfs.infrastructure.importer import DinoImporter
from dino2gtfs.infrastructure.sqlite import temporary_database


def test_imports_dynamic_tables_and_rows(tmp_path):
    source = tmp_path / "dino.zip"
    with ZipFile(source, "w") as archive:
        archive.writestr("stop.din", 'STOP_NR;STOP_NAME\n001;"A;B"\n2;München\n'.encode("cp1252"))
        archive.writestr("line.DIN", "LINE;DESC\n")
        archive.writestr(".din", "ID\n")
        archive.writestr("notes.txt", "ignored")

    with temporary_database() as db:
        DinoImporter().import_archive(source, db, {})
        assert db.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name GLOB 'dino_*' ORDER BY name").fetchall() == [
            ("dino_",), ("dino_line",), ("dino_stop",)
        ]
        assert [(row[1], row[2]) for row in db.execute('PRAGMA table_info("dino_stop")')] == [
            ("STOP_NR", "TEXT"), ("STOP_NAME", "TEXT")
        ]
        assert db.execute("SELECT * FROM dino_stop").fetchall() == [("001", "A;B"), ("2", "München")]
        assert db.execute("SELECT * FROM dino_line").fetchall() == []
        assert db.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name GLOB 'dino_*'").fetchall() == []


def test_quotes_identifiers_and_honors_input_settings(tmp_path):
    source = tmp_path / "dino.zip"
    with ZipFile(source, "w") as archive:
        archive.writestr('order"data.din', '"select","a""b"\n"é","x,y"\n'.encode("utf-8"))

    with temporary_database() as db:
        DinoImporter().import_archive(source, db, {"input": {"encoding": "utf-8", "delimiter": ","}})
        assert db.execute('SELECT "select", "a""b" FROM "dino_order""data"').fetchall() == [("é", "x,y")]


def test_imports_more_than_one_batch_and_missing_fields(tmp_path):
    source = tmp_path / "dino.zip"
    with ZipFile(source, "w") as archive:
        archive.writestr("stop.din", "ID;NAME\n" + "".join(f"{i}\n" for i in range(501)))

    with temporary_database() as db:
        DinoImporter().import_archive(source, db, {})
        assert db.execute("SELECT COUNT(*) FROM dino_stop WHERE NAME = ''").fetchone() == (501,)


@pytest.mark.parametrize("contents, message", [
    ("", "no header"),
    ("ID;ID\n", "duplicate header"),
    ("ID;id\n", "duplicate SQLite column"),
    ("ID;NAME\n1;2;3\n", "Too many fields"),
    ("ID;\x00\n", "NUL"),
])
def test_invalid_file_rolls_back_import(tmp_path, contents, message):
    source = tmp_path / "dino.zip"
    with ZipFile(source, "w") as archive:
        archive.writestr("first.din", "ID\n1\n")
        archive.writestr("bad.din", contents)

    with temporary_database() as db:
        with pytest.raises(InputError, match=message):
            DinoImporter().import_archive(source, db, {})
        assert db.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name GLOB 'dino_*'").fetchall() == []


def test_missing_or_invalid_archive(tmp_path):
    source = tmp_path / "dino.zip"
    with temporary_database() as db:
        with pytest.raises(InputError, match="not found"):
            DinoImporter().import_archive(source, db, {})
        source.write_bytes(b"not a zip")
        with pytest.raises(InputError, match="unable to read"):
            DinoImporter().import_archive(source, db, {})


def test_failed_import_preserves_existing_transaction(tmp_path):
    source = tmp_path / "dino.zip"
    with ZipFile(source, "w") as archive:
        archive.writestr("first.din", "ID\n1\n")
        archive.writestr("bad.din", "")

    with temporary_database() as db:
        db.execute("CREATE TABLE existing (value TEXT)")
        db.execute("INSERT INTO existing VALUES ('pending')")
        with pytest.raises(InputError, match="no header"):
            DinoImporter().import_archive(source, db, {})
        assert db.in_transaction
        assert db.execute("SELECT value FROM existing").fetchall() == [("pending",)]
        assert db.execute("SELECT name FROM sqlite_master WHERE name = 'dino_first'").fetchall() == []
