import logging

import pytest

from dino2gtfs.domain.errors import CollisionError, ConfigurationError, InputError
from dino2gtfs.infrastructure.sqlite import temporary_database
from dino2gtfs.transformers.agency import AgencyTransformer
from dino2gtfs.transformers.routes import RoutesTransformer


SETTINGS = {
    "defaults": {"routes": {"route_type": 3}},
    "mapping": {"mode_of_transport": {"10": 0, "20": 2}},
    "ids": {"routes": {"mode": "template", "template": "route:{gtfs.canonical}"}},
}
COLUMNS = ("VERSION", "LINE_NR", "STR_LINE_VAR", "LINE_DIR_NR", "OP_CODE", "LINE_NAME", "MOT_NR", "GLOBAL_ID")


@pytest.fixture
def db():
    with temporary_database() as database:
        database.execute("CREATE TABLE dino_operator (VERSION TEXT, OP_CODE TEXT, OP_LONG_NAME TEXT)")
        database.executemany("INSERT INTO dino_operator VALUES (?, ?, ?)", [
            ("1", "A", "First"), ("2", "A", "First"), ("1", "B", "Second"),
        ])
        settings = {
            "defaults": {"agency": {"agency_url": "https://example.org", "agency_timezone": "UTC"}},
            "ids": {"agency": {"mode": "template", "template": "agency:{operator.OP_CODE}"}},
        }
        transformer = AgencyTransformer()
        transformer.initialize(database, settings)
        transformer.transform(database, settings)
        yield database


def create_lines(db, rows, columns=COLUMNS):
    db.execute(f"CREATE TABLE dino_line ({', '.join(column + ' TEXT' for column in columns)})")
    db.executemany(f"INSERT INTO dino_line VALUES ({', '.join('?' for _ in columns)})", rows)


def transform(db, settings=SETTINGS):
    transformer = RoutesTransformer()
    transformer.initialize(db, settings)
    transformer.transform(db, settings)


@pytest.mark.parametrize("mode,identifier", [
    ("template", "route:2:421:3"), ("global", "route:G:A:3"),
])
def test_consolidates_variants_and_versions_and_maps_every_source(db, caplog, mode, identifier):
    create_lines(db, [
        ("1", "42", "a", "1", "A", "42", "unknown", "G"),
        ("1", "42", "b", "2", "A", "42", "unknown", "G"),
        ("2", "42", "a", "1", "A", "42", "unknown", "G"),
        ("2", "42", "a", "1", "A", "42", "unknown", "G"),
    ])
    settings = {**SETTINGS, "ids": {"routes": {**SETTINGS["ids"]["routes"], "mode": mode}}}
    transform(db, settings)
    assert db.execute(
        "SELECT route_id, agency_id, route_short_name, route_long_name, route_type, network_id FROM gtfs_routes"
    ).fetchall() == [(identifier, "agency:A", "42", None, 3, None)]
    assert db.execute("SELECT * FROM map_line_2_routes ORDER BY VERSION, STR_LINE_VAR").fetchall() == [
        ("1", "42", "a", "1", identifier), ("1", "42", "b", "2", identifier),
        ("2", "42", "a", "1", identifier),
    ]
    assert not any(record.levelno >= logging.WARNING for record in caplog.records)


@pytest.mark.parametrize("mode", ["template", "global"])
@pytest.mark.parametrize("override_name", [False, True])
def test_merge_identity_uses_source_name_and_final_mode_not_line_number(db, mode, override_name):
    create_lines(db, [
        ("1", "42", "a", "1", "A", "Blue", "10", "old"),
        ("1", "42", "b", "1", "A", "Red", "10", "red"),
        ("2", "99", "c", "2", "A", "Blue", "11", "blue"),
        ("1", "100", "d", "1", "B", "Blue", "unknown", "bus"),
    ])
    settings = {
        **SETTINGS,
        "mapping": {"mode_of_transport": {"10": 0, "11": 0}},
        "ids": {"routes": {**SETTINGS["ids"]["routes"], "mode": mode}},
    }
    if override_name:
        settings["overrides"] = {"routes": {"route_short_name": "Forced"}}
    transform(db, settings)
    assert db.execute("SELECT route_short_name, route_type FROM gtfs_routes ORDER BY route_type, route_short_name").fetchall() == (
        [("Forced", 0), ("Forced", 0), ("Forced", 3)] if override_name else
        [("Blue", 0), ("Red", 0), ("Blue", 3)]
    )
    identifiers = dict(db.execute("SELECT STR_LINE_VAR, gtfs_id FROM map_line_2_routes"))
    assert identifiers["a"] == identifiers["c"]
    assert len({identifiers["a"], identifiers["b"], identifiers["d"]}) == 3
    assert db.execute("SELECT count(*) FROM map_line_2_routes").fetchone() == (4,)
    if mode == "template":
        assert identifiers["a"] == "route:4:Blue1:0"
    else:
        assert identifiers["a"] == "route:blue:A:0"


@pytest.mark.parametrize("mode", ["template", "global"])
def test_operators_merge_but_different_mapped_modes_split_routes(db, mode):
    create_lines(db, [
        ("1", "42", "a", "1", "A", "42", "10", "G"),
        ("1", "42", "b", "1", "B", "42", "10", "H"),
        ("1", "42", "c", "1", "A", "42", "20", "I"),
    ])
    settings = {**SETTINGS, "ids": {"routes": {**SETTINGS["ids"]["routes"], "mode": mode}}}
    transform(db, settings)
    assert db.execute("SELECT agency_id, route_type FROM gtfs_routes ORDER BY agency_id, route_type").fetchall() == [
        ("agency:A", 2), ("agency:B", 0),
    ]
    assert db.execute("SELECT count(DISTINCT gtfs_id) FROM map_line_2_routes").fetchone() == (2,)
    ids = db.execute("SELECT gtfs_id FROM map_line_2_routes ORDER BY STR_LINE_VAR").fetchall()
    assert ids[0] == ids[1] and ids[0] != ids[2]


@pytest.mark.parametrize("mode", ["global", "template"])
@pytest.mark.parametrize("last_code", ["B", ""])
def test_last_operator_wins_across_versions_and_all_sources_map_to_one_route(db, caplog, mode, last_code):
    create_lines(db, [
        ("2", "42", "a", "1", "A", "Last", "", "G"),
        ("1", "42", "b", "2", "A", "Last", "", "G"),
        ("1", "42", "b", "2", last_code, "Last", "", "G"),
    ])
    settings = {**SETTINGS, "ids": {"routes": {**SETTINGS["ids"]["routes"], "mode": mode}}}
    transform(db, settings)
    identifier = f"route:G:{last_code}:3" if mode == "global" else "route:4:Last1:3"
    assert db.execute("SELECT route_id, agency_id, route_short_name FROM gtfs_routes").fetchall() == [
        (identifier, "agency:B" if last_code else None, "Last"),
    ]
    assert db.execute("SELECT * FROM map_line_2_routes ORDER BY VERSION").fetchall() == [
        ("1", "42", "b", "2", identifier), ("2", "42", "a", "1", identifier),
    ]
    assert not any("global ID collision" in record.message for record in caplog.records)


def test_source_fields_and_computed_mode_are_available_to_template(db):
    create_lines(db, [("1", "42", "a", "1", "A", "42", "20", "")])
    settings = {**SETTINGS, "ids": {"routes": {
        "mode": "template", "template": "{gtfs.entity}:{line.LINE_NR}:{line.OP_CODE}:{gtfs.route_type}"
    }}}
    transform(db, settings)
    assert db.execute("SELECT route_id FROM gtfs_routes").fetchall() == [("routes:42:A:2",)]


def test_fingerprint_uses_only_line_name_and_resolved_mode(db):
    create_lines(db, [
        ("1", "42", "a", "1", "A", "42", "10", ""),
        ("1", "99", "b", "2", "B", "42", "10", ""),
        ("1", "42", "c", "1", "A", "42", "20", ""),
    ])
    settings = {**SETTINGS, "ids": {"routes": {"mode": "template", "template": "{gtfs.fingerprint}"}}}
    transform(db, settings)
    ids = db.execute("SELECT gtfs_id FROM map_line_2_routes ORDER BY VERSION, STR_LINE_VAR").fetchall()
    assert len(ids[0][0]) == 64
    assert ids[0] == ids[1]
    assert ids[0] != ids[2]


def test_mode_mapping_accepts_yaml_integer_keys_without_a_default(db):
    create_lines(db, [("1", "42", "a", "1", "A", "42", "10", "")])
    transform(db, {**SETTINGS, "defaults": {}, "mapping": {"mode_of_transport": {10: 0}}})
    assert db.execute("SELECT route_type FROM gtfs_routes").fetchall() == [(0,)]


def test_defaults_populate_gtfs_columns_without_inventing_source_metadata(db):
    create_lines(db, [("1", "42", "a", "1", "A", "42", "", "")])
    settings = {**SETTINGS, "overrides": {"routes": {"route_short_name": None}}, "defaults": {"routes": {
        "route_type": 3, "route_short_name": None, "route_long_name": "Configured line",
        "route_desc": "Description", "route_url": "https://example.org/42",
        "route_color": "ABCDEF", "route_text_color": "000000", "route_sort_order": 5,
    }}}
    transform(db, settings)
    assert db.execute(
        "SELECT route_short_name, route_long_name, route_desc, route_url, route_color, route_text_color, "
        "route_sort_order FROM gtfs_routes"
    ).fetchall() == [(None, "Configured line", "Description", "https://example.org/42", "ABCDEF", "000000", 5)]


@pytest.mark.parametrize("mode", ["global", "template"])
@pytest.mark.parametrize("override", [False, True])
def test_route_configuration_precedence_is_used_for_names_modes_ids_and_mappings(db, mode, override):
    create_lines(db, [("1", "42", "a", "1", "A", "Source", "20", "G")])
    settings = {
        **SETTINGS,
        "ids": {"routes": {"mode": mode, "template": "route:{gtfs.canonical}"}},
        "defaults": {"routes": {"route_type": 3, "route_short_name": "Default", "route_desc": "Description"}},
    }
    if override:
        settings["overrides"] = {"routes": {"route_type": 3, "route_short_name": "Forced", "route_desc": None}}
    transform(db, settings)
    route_type = 3 if override else 2
    identifier = f"route:G:A:{route_type}" if mode == "global" else f"route:6:Source1:{route_type}"
    assert db.execute("SELECT route_id, route_short_name, route_type, route_desc FROM gtfs_routes").fetchall() == [
        (identifier, "Forced" if override else "Source", route_type, None if override else "Description")
    ]
    assert db.execute("SELECT gtfs_id FROM map_line_2_routes").fetchall() == [(identifier,)]


def test_route_type_override_works_without_a_mapping_or_default(db):
    create_lines(db, [("1", "42", "a", "1", "A", "42", "", "")])
    transform(db, {**SETTINGS, "mapping": {}, "defaults": {}, "overrides": {"routes": {"route_type": 7}}})
    assert db.execute("SELECT route_type FROM gtfs_routes").fetchone() == (7,)


@pytest.mark.parametrize("field,value,message", [
    ("route_type", None, "overrides.routes.route_type"),
    ("route_short_name", 123, "overrides.routes.route_short_name"),
    ("route_type", -1, "overrides.routes.route_type"),
])
def test_route_overrides_keep_entity_validation(db, field, value, message):
    with pytest.raises(ConfigurationError, match=message):
        transform(db, {**SETTINGS, "overrides": {"routes": {field: value}}})


@pytest.mark.parametrize("change,message", [
    ({"defaults": {}}, "no mapping.mode_of_transport"),
    ({"ids": {"routes": {"mode": "global"}}}, "GLOBAL_ID is required"),
])
def test_missing_mode_or_global_id_never_uses_an_implicit_fallback(db, change, message):
    create_lines(db, [("1", "42", "a", "1", "A", "42", "unknown", "")])
    with pytest.raises(InputError, match=message):
        transform(db, {**SETTINGS, **change})
    assert db.execute("SELECT * FROM gtfs_routes").fetchall() == []
    assert db.execute("SELECT * FROM map_line_2_routes").fetchall() == []


@pytest.mark.parametrize("version,code,message", [
    ("3", "A", "no agency mapping"), ("1", "X", "no agency mapping"),
])
def test_agency_assignment_requires_line_operator_and_exact_version(db, version, code, message):
    create_lines(db, [(version, "42", "a", "1", code, "42", "", "")])
    db.execute("CREATE TABLE dino_trip (VERSION TEXT, LINE_NR TEXT, OP_CODE TEXT)")
    db.execute("INSERT INTO dino_trip VALUES ('1', '42', 'A')")
    with pytest.raises(InputError, match=message):
        transform(db)


@pytest.mark.parametrize("code", [None, "", "   ", "missing-column"])
@pytest.mark.parametrize("mode,template,identifier", [
    ("global", None, "route:G::3"),
    ("template", "route:{gtfs.canonical}", "route:2:421:3"),
    ("template", "route:{line.LINE_NR}:{line.OP_CODE}:{gtfs.route_type}", "route:42::3"),
])
def test_missing_operator_warns_and_leaves_agency_unassigned(db, caplog, code, mode, template, identifier):
    row = ("1", "42", "a", "1", code, "42", "", "G")
    if code == "missing-column":
        create_lines(db, [row[:4] + row[5:]], COLUMNS[:4] + COLUMNS[5:])
    else:
        create_lines(db, [row])
    settings = {**SETTINGS, "ids": {"routes": {"mode": mode, "template": template}}}
    transform(db, settings)
    assert db.execute("SELECT route_id, agency_id FROM gtfs_routes").fetchall() == [(identifier, None)]
    assert db.execute("SELECT * FROM map_line_2_routes").fetchall() == [("1", "42", "a", "1", identifier)]
    assert "line.din row 1: OP_CODE is missing" in caplog.text
    assert "without an agency assignment" in caplog.text


def test_missing_operator_fingerprints_merge_with_assigned_routes(db):
    create_lines(db, [
        ("1", "42", "a", "1", "", "42", "", ""),
        ("2", "42", "b", "2", None, "42", "", ""),
        ("1", "42", "c", "1", "A", "42", "", ""),
    ])
    settings = {**SETTINGS, "ids": {"routes": {"mode": "template", "template": "{gtfs.fingerprint}"}}}
    transform(db, settings)
    routes = db.execute("SELECT route_id, agency_id FROM gtfs_routes ORDER BY agency_id").fetchall()
    assert len(routes) == 1
    assert len(routes[0][0]) == 64
    assert routes[0][1] == "agency:A"
    assert db.execute("SELECT gtfs_id FROM map_line_2_routes ORDER BY VERSION, STR_LINE_VAR").fetchall() == [
        (routes[0][0],), (routes[0][0],), (routes[0][0],),
    ]


@pytest.mark.parametrize("conflict", [False, True])
def test_rejects_dangling_or_ambiguous_agency_mapping(db, conflict):
    create_lines(db, [("1", "42", "a", "1", "A", "42", "", "")])
    if conflict:
        db.execute("INSERT INTO map_operator_2_agency VALUES ('1', 'A', 'agency:B')")
    else:
        db.execute("DELETE FROM gtfs_agency WHERE agency_id = 'agency:A'")
    with pytest.raises(CollisionError if conflict else InputError, match="line.din row 1.*agency"):
        transform(db)


def test_reports_missing_agency_transformer():
    with temporary_database() as db:
        create_lines(db, [])
        with pytest.raises(InputError, match="run AgencyTransformer first"):
            transform(db)


@pytest.mark.parametrize("template,second_row,message", [
    ("same", ("1", "43", "b", "1", "A", "43", "", ""), "identity collision"),
    ("{gtfs.canonical}", ("1", "42", "a", "1", "A", "42", "20", ""), "source identity"),
])
def test_conflicts_fail_before_inserting_any_route_or_mapping(db, template, second_row, message):
    create_lines(db, [("1", "42", "a", "1", "A", "42", "", ""), second_row])
    settings = {**SETTINGS, "ids": {"routes": {"mode": "template", "template": template}}}
    with pytest.raises(CollisionError, match=message + ".*line.din row 1 and line.din row 2"):
        transform(db, settings)
    assert db.execute("SELECT * FROM gtfs_routes").fetchall() == []
    assert db.execute("SELECT * FROM map_line_2_routes").fetchall() == []


def test_global_id_collision_between_distinct_lines_is_fatal(db):
    create_lines(db, [
        ("1", "42", "a", "1", "A", "42", "", "G"),
        ("1", "43", "a", "1", "A", "43", "", "G"),
    ])
    with pytest.raises(CollisionError, match="global ID collision in gtfs_routes.*'G'.*line.din row 1 and line.din row 2"):
        transform(db, {**SETTINGS, "ids": {"routes": {"mode": "global"}}})


@pytest.mark.parametrize("mode", ["global", "template"])
@pytest.mark.parametrize("line,code,mot", [
    ("43", "A", ""), ("43", "B", ""), ("42", "A", "20"),
])
def test_retained_route_global_id_collisions_include_operator_and_mode_splits(db, caplog, mode, line, code, mot):
    create_lines(db, [
        ("1", "42", "a", "1", "A", "Last", "", "old"),
        ("1", line, "b", "1", code, "Other", mot, "G"),
        ("2", "42", "a", "1", "A", "Last", "", "G"),
    ])
    settings = {**SETTINGS, "ids": {"routes": {**SETTINGS["ids"]["routes"], "mode": mode}}}
    if mode == "global":
        with pytest.raises(CollisionError, match="global ID collision in gtfs_routes.*'G'") as error:
            transform(db, settings)
        assert "line.din row 2" in str(error.value)
        assert "line.din row 3" in str(error.value)
        assert "line.din row 1" not in str(error.value)
        assert db.execute("SELECT * FROM gtfs_routes").fetchall() == []
        assert db.execute("SELECT * FROM map_line_2_routes").fetchall() == []
    else:
        transform(db, settings)
        warnings = [record.message for record in caplog.records if "global ID collision" in record.message]
        assert len(warnings) == 1
        assert "gtfs_routes" in warnings[0] and "'G'" in warnings[0]
        assert "line.din row 2" in warnings[0] and "line.din row 3" in warnings[0]
        assert "line.din row 1" not in warnings[0]
        assert db.execute("SELECT count(*) FROM gtfs_routes").fetchone() == (2,)


@pytest.mark.parametrize("mode", ["global", "template"])
def test_global_ids_of_replaced_line_records_are_not_checked(db, caplog, mode):
    create_lines(db, [
        ("1", "42", "a", "1", "A", "Last", "", "G"),
        ("1", "43", "a", "1", "A", "Other", "", "G"),
        ("2", "42", "b", "2", "A", "Last", "", "final"),
    ])
    transform(db, {**SETTINGS, "ids": {"routes": {**SETTINGS["ids"]["routes"], "mode": mode}}})
    assert db.execute("SELECT count(*) FROM gtfs_routes").fetchone() == (2,)
    assert not any(record.levelno >= logging.WARNING for record in caplog.records)


@pytest.mark.parametrize("global_id", [None, "", "   "])
def test_blank_global_ids_do_not_collide_in_template_mode(db, caplog, global_id):
    create_lines(db, [
        ("1", "42", "a", "1", "A", "First", "", global_id),
        ("1", "43", "a", "1", "A", "Second", "", global_id),
    ])
    transform(db)
    assert db.execute("SELECT count(*) FROM gtfs_routes").fetchone() == (2,)
    assert not any(record.levelno >= logging.WARNING for record in caplog.records)


def test_existing_route_id_is_a_collision(db):
    create_lines(db, [("1", "42", "a", "1", "A", "42", "", "")])
    db.execute("INSERT INTO gtfs_routes (route_id, route_type) VALUES ('route:2:421:3', 3)")
    with pytest.raises(CollisionError, match="existing route and line.din row 1"):
        transform(db)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_public_name_fails_with_dino_attribute(db, value):
    create_lines(db, [("1", "42", "a", "1", "A", value, "", "")])
    with pytest.raises(InputError, match=r"^LINE_NAME missing in line\.din row 1$"):
        transform(db)
    assert db.execute("SELECT * FROM gtfs_routes").fetchall() == []
    assert db.execute("SELECT * FROM map_line_2_routes").fetchall() == []


@pytest.mark.parametrize("field", ["route_short_name", "route_long_name"])
@pytest.mark.parametrize("section", ["defaults", "overrides"])
@pytest.mark.parametrize("value", [None, "", "   "])
def test_configured_public_name_does_not_replace_missing_identity_name(db, field, section, value):
    create_lines(db, [("1", "42", "a", "1", "A", value, "", "")])
    settings = {**SETTINGS, section: {"routes": {"route_type": 3, field: "Configured"}}}
    with pytest.raises(InputError, match=r"^LINE_NAME missing in line\.din row 1$"):
        transform(db, settings)
    assert db.execute("SELECT * FROM gtfs_routes").fetchall() == []
    assert db.execute("SELECT * FROM map_line_2_routes").fetchall() == []


@pytest.mark.parametrize("ids,identifier", [
    ({"mode": "template", "template": "route:{gtfs.canonical}"}, "route:5:Final1:3"),
    ({"mode": "template", "template": "route:{line.VERSION}:{line.STR_LINE_VAR}"}, "route:1:c"),
    ({"mode": "global"}, "route:final:A:3"),
])
def test_last_route_wins_and_all_source_mappings_use_its_final_id(db, caplog, ids, identifier):
    create_lines(db, [
        ("2", "42", "a", "1", "A", "Final", "", "old"),
        ("1", "42", "b", "2", "A", "Final", "", "earlier"),
        ("1", "42", "c", "1", "A", "Final", "", "last"),
        ("1", "42", "c", "1", "A", "Final", "", "final"),
    ])
    transform(db, {**SETTINGS, "ids": {"routes": ids}})
    assert db.execute("SELECT route_id, route_short_name FROM gtfs_routes").fetchall() == [(identifier, "Final")]
    assert db.execute("SELECT * FROM map_line_2_routes ORDER BY VERSION, STR_LINE_VAR").fetchall() == [
        ("1", "42", "b", "2", identifier), ("1", "42", "c", "1", identifier),
        ("2", "42", "a", "1", identifier),
    ]
    assert not any(record.levelno >= logging.WARNING for record in caplog.records)


def test_last_route_with_missing_name_fails_without_reusing_an_older_name(db):
    create_lines(db, [
        ("2", "42", "a", "1", "A", "Old name", "", ""),
        ("1", "42", "b", "2", "A", "", "", ""),
    ])
    with pytest.raises(InputError, match=r"^LINE_NAME missing in line\.din row 2$"):
        transform(db)
    assert db.execute("SELECT * FROM gtfs_routes").fetchall() == []
    assert db.execute("SELECT * FROM map_line_2_routes").fetchall() == []


def test_replaced_route_ids_do_not_cause_false_collisions(db):
    create_lines(db, [
        ("1", "42", "a", "1", "A", "Blue", "", "shared"),
        ("1", "43", "a", "1", "A", "Red", "", "shared"),
        ("2", "42", "a", "1", "A", "Blue", "", "final"),
    ])
    transform(db, {**SETTINGS, "ids": {"routes": {"mode": "template", "template": "{line.GLOBAL_ID}"}}})
    assert db.execute("SELECT route_id FROM gtfs_routes ORDER BY route_id").fetchall() == [("final",), ("shared",)]
    assert db.execute("SELECT gtfs_id FROM map_line_2_routes WHERE LINE_NR = '42'").fetchall() == [
        ("final",), ("final",),
    ]


@pytest.mark.parametrize("change,message", [
    ({"mapping": None}, "mapping must be a mapping"),
    ({"mapping": {"mode_of_transport": []}}, "mapping.mode_of_transport must be a mapping"),
    ({"mapping": {"mode_of_transport": {"": 3}}}, "keys must be non-empty"),
    ({"mapping": {"mode_of_transport": {"10": -1}}}, "GTFS route_type integer"),
    ({"mapping": {"mode_of_transport": {"10": 2**63}}}, "SQLite integer range"),
    ({"mapping": {"mode_of_transport": {True: 3}}}, "keys must be non-empty"),
    ({"mapping": {"mode_of_transport": {10: 3, "10": 2}}}, "Duplicate MOT_NR"),
    ({"defaults": {"routes": {"route_type": None}}}, "defaults.routes.route_type"),
    ({"defaults": {"routes": {"route_type": True}}}, "GTFS route_type integer"),
    ({"defaults": {"routes": {"route_type": "3"}}}, "GTFS route_type integer"),
    ({"defaults": {"routes": {"route_type": 3.5}}}, "GTFS route_type integer"),
    ({"defaults": {"routes": {"route_long_name": 5}}}, "route_long_name"),
    ({"defaults": {"routes": {"ROUTE_TYPE": 3}}}, "Unknown GTFS field"),
    ({"ids": {}}, "ids.routes"),
    ({"ids": {"routes": {"mode": "other"}}}, "ids.routes.mode"),
])
def test_rejects_invalid_configuration_before_inserting(db, change, message):
    with pytest.raises(ConfigurationError, match=message):
        transform(db, {**SETTINGS, **change})
    assert db.execute("SELECT * FROM gtfs_routes").fetchall() == []


def test_validates_template_fields_even_without_lines(db):
    create_lines(db, [])
    settings = {**SETTINGS, "ids": {"routes": {"mode": "template", "template": "{line.UNKNOWN}"}}}
    with pytest.raises(InputError, match="line.din is missing column.*line.UNKNOWN"):
        transform(db, settings)


def test_requires_line_source_and_composite_key(db):
    transformer = RoutesTransformer()
    transformer.initialize(db, SETTINGS)
    with pytest.raises(InputError, match="Missing DINO source file line.din"):
        transformer.transform(db, SETTINGS)
    create_lines(db, [], ("VERSION", "LINE_NR", "OP_CODE"))
    with pytest.raises(InputError, match="missing required columns.*LINE_DIR_NR.*STR_LINE_VAR"):
        transformer.transform(db, SETTINGS)


def test_requires_line_name_column_even_with_configured_output_name(db):
    create_lines(db, [], COLUMNS[:5] + COLUMNS[6:])
    with pytest.raises(InputError, match="line.din is missing required columns: LINE_NAME"):
        transform(db, {**SETTINGS, "overrides": {"routes": {"route_short_name": "Configured"}}})
