import pytest

from dino2gtfs.domain.errors import CollisionError, ConfigurationError, InputError
from dino2gtfs.infrastructure.sqlite import temporary_database
from dino2gtfs.transformers.agency import AgencyTransformer


SETTINGS = {"agency": {"url": "https://example.org", "timezone": "Europe/Berlin", "language": "de"}}


def test_transforms_operators_and_deduplicates_versions():
    with temporary_database() as db:
        db.execute(
            "CREATE TABLE dino_operator (VERSION TEXT, OP_CODE TEXT, OP_TRADING_NAME TEXT, "
            "OP_LONG_NAME TEXT, OP_PUBLIC_SHORT TEXT)"
        )
        db.executemany("INSERT INTO dino_operator VALUES (?, ?, ?, ?, ?)", [
            ("1", "01", "Trading", "Long", "Short"),
            ("2", "01", "Trading", "Long", "Short"),
            ("1", "02", "", "Long 2", "Short 2"),
            ("1", "03", "", "", "Short 3"),
        ])
        AgencyTransformer().transform(db, SETTINGS)
        assert db.execute(
            "SELECT agency_id, agency_name, agency_url, agency_timezone, agency_lang FROM gtfs_agency ORDER BY agency_id"
        ).fetchall() == [
            ("agency:01", "Trading", "https://example.org", "Europe/Berlin", "de"),
            ("agency:02", "Long 2", "https://example.org", "Europe/Berlin", "de"),
            ("agency:03", "Short 3", "https://example.org", "Europe/Berlin", "de"),
        ]


def test_optional_source_columns_and_language():
    with temporary_database() as db:
        db.execute("CREATE TABLE dino_operator (op_code TEXT, op_long_name TEXT)")
        db.execute("INSERT INTO dino_operator VALUES ('A', 'Agency')")
        AgencyTransformer().transform(db, {"agency": {"url": "https://example.org", "timezone": "UTC"}})
        assert db.execute("SELECT agency_id, agency_name, agency_lang FROM gtfs_agency").fetchall() == [
            ("agency:A", "Agency", None)
        ]


@pytest.mark.parametrize("settings", [
    {}, {"agency": None}, {"agency": {"url": "", "timezone": "UTC"}},
    {"agency": {"url": "https://example.org"}}, {"agency": {"url": "x", "timezone": "UTC", "language": False}},
])
def test_rejects_invalid_agency_configuration(settings):
    with temporary_database() as db, pytest.raises(ConfigurationError, match="agency"):
        AgencyTransformer().transform(db, settings)


@pytest.mark.parametrize("columns, rows, message", [
    (None, [], "Missing DINO source file"),
    ("OP_CODE TEXT", [], "requires OP_CODE"),
    ("OP_CODE TEXT, OP_LONG_NAME TEXT", [], "contains no operators"),
    ("OP_CODE TEXT, OP_LONG_NAME TEXT", [("", "Name")], "OP_CODE is required"),
    ("OP_CODE TEXT, OP_LONG_NAME TEXT", [("A", "")], "public operator name is required"),
])
def test_rejects_missing_operator_data(columns, rows, message):
    with temporary_database() as db:
        if columns:
            db.execute(f"CREATE TABLE dino_operator ({columns})")
            if rows:
                db.executemany("INSERT INTO dino_operator VALUES (?, ?)", rows)
        with pytest.raises(InputError, match=message):
            AgencyTransformer().transform(db, SETTINGS)
        assert db.execute("SELECT * FROM gtfs_agency").fetchall() == []


def test_conflicting_operator_names_fail_without_partial_inserts():
    with temporary_database() as db:
        db.execute("CREATE TABLE dino_operator (OP_CODE TEXT, OP_LONG_NAME TEXT)")
        db.executemany("INSERT INTO dino_operator VALUES (?, ?)", [("A", "First"), ("B", "Other"), ("A", "Second")])
        with pytest.raises(CollisionError, match="agency:A.*operator.din rows 1 and 3"):
            AgencyTransformer().transform(db, SETTINGS)
        assert db.execute("SELECT * FROM gtfs_agency").fetchall() == []


def test_existing_agency_id_is_a_collision():
    with temporary_database() as db:
        db.execute("CREATE TABLE dino_operator (OP_CODE TEXT, OP_LONG_NAME TEXT)")
        db.execute("INSERT INTO dino_operator VALUES ('A', 'Agency')")
        db.execute("INSERT INTO gtfs_agency (agency_id, agency_name, agency_url, agency_timezone) VALUES ('agency:A', 'Old', 'x', 'UTC')")
        with pytest.raises(CollisionError, match="agency:A"):
            AgencyTransformer().transform(db, SETTINGS)
