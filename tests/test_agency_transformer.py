import pytest

from dino2gtfs.domain.errors import CollisionError, ConfigurationError, InputError
from dino2gtfs.infrastructure.sqlite import temporary_database
from dino2gtfs.transformers.agency import AgencyTransformer


SETTINGS = {
    "defaults": {"agency": {
        "agency_url": "https://example.org", "agency_timezone": "Europe/Berlin", "agency_lang": "de"
    }},
    "ids": {"agency": {"mode": "template", "template": "agency:{operator.OP_CODE}"}},
}


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
        AgencyTransformer().transform(db, {
            "defaults": {"agency": {"agency_url": "https://example.org", "agency_timezone": "UTC"}},
            "ids": SETTINGS["ids"],
        })
        assert db.execute("SELECT agency_id, agency_name, agency_lang FROM gtfs_agency").fetchall() == [
            ("agency:A", "Agency", None)
        ]


def test_defaults_override_source_and_add_gtfs_fields():
    settings = {**SETTINGS, "defaults": {"agency": {
        "agency_url": "https://example.org", "agency_timezone": "UTC", "agency_name": "Configured",
        "agency_phone": "+49 123", "agency_email": "contact@example.org", "cemv_support": 0,
    }}}
    with temporary_database() as db:
        db.execute("CREATE TABLE dino_operator (OP_CODE TEXT, OP_LONG_NAME TEXT)")
        db.executemany("INSERT INTO dino_operator VALUES (?, ?)", [("A", "Source"), ("A", "Changed"), ("B", "")])
        AgencyTransformer().transform(db, settings)
        assert db.execute(
            "SELECT agency_id, agency_name, agency_phone, agency_email, cemv_support FROM gtfs_agency ORDER BY agency_id"
        ).fetchall() == [
            ("agency:A", "Configured", "+49 123", "contact@example.org", 0),
            ("agency:B", "Configured", "+49 123", "contact@example.org", 0),
        ]


def test_default_name_allows_operator_without_name_column():
    settings = {**SETTINGS, "defaults": {"agency": {
        **SETTINGS["defaults"]["agency"], "agency_name": "Configured"
    }}}
    with temporary_database() as db:
        db.execute("CREATE TABLE dino_operator (OP_CODE TEXT)")
        db.execute("INSERT INTO dino_operator VALUES ('A')")
        AgencyTransformer().transform(db, settings)
        assert db.execute("SELECT agency_name FROM gtfs_agency").fetchall() == [("Configured",)]


@pytest.mark.parametrize("settings", [
    {}, {"defaults": None}, {"defaults": {"agency": None}},
    {"defaults": {"agency": {"agency_url": "", "agency_timezone": "UTC"}}},
    {"defaults": {"agency": {"agency_url": "https://example.org"}}},
    {"defaults": {"agency": {"agency_url": "x", "agency_timezone": "UTC", "agency_lang": False}}},
    {"defaults": {"agency": {"agency_url": "x", "agency_timezone": "UTC", "agency_name": None}}},
])
def test_rejects_invalid_agency_configuration(settings):
    with temporary_database() as db, pytest.raises(ConfigurationError, match="defaults"):
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


def test_custom_agency_id_template_uses_operator_columns():
    settings = {**SETTINGS, "ids": {"agency": {"mode": "template", "template": "op-{operator.OP_CODE}-{operator.OP_BRANCH_NR}"}}}
    with temporary_database() as db:
        db.execute("CREATE TABLE dino_operator (OP_CODE TEXT, OP_LONG_NAME TEXT, OP_BRANCH_NR TEXT)")
        db.execute("INSERT INTO dino_operator VALUES ('A', 'Agency', '02')")
        AgencyTransformer().transform(db, settings)
        assert db.execute("SELECT agency_id FROM gtfs_agency").fetchall() == [("op-A-02",)]


def test_agency_id_template_supports_stable_fingerprint():
    settings = {**SETTINGS, "ids": {"agency": {"mode": "template", "template": "agency:{gtfs.fingerprint}"}}}
    with temporary_database() as db:
        db.execute("CREATE TABLE dino_operator (VERSION TEXT, OP_CODE TEXT, OP_LONG_NAME TEXT)")
        db.executemany("INSERT INTO dino_operator VALUES (?, ?, ?)", [
            ("1", "A", "Agency"), ("2", "A", "Agency"), ("1", "B", "Other"),
        ])
        AgencyTransformer().transform(db, settings)
        ids = db.execute("SELECT agency_id FROM gtfs_agency ORDER BY agency_name").fetchall()
        assert ids[0] == ("agency:ead76f8e70b5dd3b1a07a92c25c425b2b27198728862103d65c31c621e52a6aa",)
        assert len(ids) == 2 and ids[0] != ids[1]


def test_agency_template_supports_generic_entity_and_canonical():
    settings = {**SETTINGS, "ids": {"agency": {
        "mode": "template", "template": "{gtfs.entity}:{gtfs.canonical}"
    }}}
    with temporary_database() as db:
        db.execute("CREATE TABLE dino_operator (OP_CODE TEXT, OP_LONG_NAME TEXT)")
        db.execute("INSERT INTO dino_operator VALUES ('A:1', 'Agency')")
        AgencyTransformer().transform(db, settings)
        assert db.execute("SELECT agency_id FROM gtfs_agency").fetchall() == [("agency:3:A:1",)]


def test_fingerprint_collision_fails_without_partial_inserts():
    class CollidingAgencyTransformer(AgencyTransformer):
        def fingerprint_components(self, sources, context):
            return ("same",)

    settings = {**SETTINGS, "ids": {"agency": {"mode": "template", "template": "{gtfs.fingerprint}"}}}
    with temporary_database() as db:
        db.execute("CREATE TABLE dino_operator (OP_CODE TEXT, OP_LONG_NAME TEXT)")
        db.executemany("INSERT INTO dino_operator VALUES (?, ?)", [("A", "First"), ("B", "Second")])
        with pytest.raises(CollisionError, match="operator.din rows 1 and 2"):
            CollidingAgencyTransformer().transform(db, settings)
        assert db.execute("SELECT * FROM gtfs_agency").fetchall() == []


@pytest.mark.parametrize("agency_ids", [
    None, {}, {"mode": "global", "template": "agency:{operator.OP_CODE}"},
    {"mode": "template", "template": ""}, {"mode": "template", "template": "agency:{"},
    {"mode": "template", "template": "{line.OP_CODE}"},
    {"mode": "template", "template": "{operator.OP_CODE!r}"},
    {"mode": "template", "template": "{operator.OP_CODE:>5}"},
])
def test_rejects_invalid_id_configuration(agency_ids):
    settings = {**SETTINGS, "ids": {"agency": agency_ids}}
    with temporary_database() as db, pytest.raises(ConfigurationError, match="ids.agency"):
        AgencyTransformer().transform(db, settings)


@pytest.mark.parametrize("template, rows, error, message", [
    ("{operator.MISSING}", [("A", "Agency", "1")], InputError, "missing column"),
    ("{operator.OP_BRANCH_NR}", [("A", "Agency", "")], InputError, "OP_BRANCH_NR is required"),
    ("same", [("A", "First", "1"), ("B", "Second", "1")], CollisionError, "operator.din rows 1 and 2"),
    ("{operator.OP_BRANCH_NR}", [("A", "Agency", "1"), ("A", "Agency", "2")],
     CollisionError, "operator.din rows 1 and 2"),
])
def test_rejects_missing_or_conflicting_template_values(template, rows, error, message):
    settings = {**SETTINGS, "ids": {"agency": {"mode": "template", "template": template}}}
    with temporary_database() as db:
        db.execute("CREATE TABLE dino_operator (OP_CODE TEXT, OP_LONG_NAME TEXT, OP_BRANCH_NR TEXT)")
        db.executemany("INSERT INTO dino_operator VALUES (?, ?, ?)", rows)
        with pytest.raises(error, match=message):
            AgencyTransformer().transform(db, settings)
        assert db.execute("SELECT * FROM gtfs_agency").fetchall() == []
