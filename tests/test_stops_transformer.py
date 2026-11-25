import logging

import pytest

from dino2gtfs.domain.errors import CollisionError, ConfigurationError, InputError
from dino2gtfs.infrastructure.sqlite import temporary_database
from dino2gtfs.transformers.stops import StopsTransformer


GLOBAL = {"ids": {"stops": {"mode": "global"}}, "input": {"crs": "EPSG:4326"}}
TEMPLATE = {"ids": {"stops": {
    "mode": "template", "template": "stop:{gtfs.stop_kind}:{gtfs.canonical}"
}}, "input": {"crs": "EPSG:4326"}}


def transform(db, settings):
    transformer = StopsTransformer()
    transformer.initialize(db, settings)
    transformer.transform(db, settings)


def create_table(db, name, columns, rows):
    if name == "stop" and "IS_RESPONSIBLE_STOP" not in columns:
        columns = [*columns, "IS_RESPONSIBLE_STOP"]
        rows = [(*row, "1") for row in rows]
    db.execute(f"CREATE TABLE dino_{name} ({', '.join(column + ' TEXT' for column in columns)})")
    db.executemany(f"INSERT INTO dino_{name} VALUES ({', '.join('?' for _ in columns)})", rows)


@pytest.mark.parametrize("settings", [GLOBAL, TEMPLATE])
def test_only_responsible_stations_and_their_points_are_transformed(settings, caplog):
    with temporary_database() as db:
        create_table(db, "stop", ["VERSION", "STOP_NR", "STOP_NAME", "IS_RESPONSIBLE_STOP",
                                  "GLOBAL_ID", "STOP_POS_X", "STOP_POS_Y"], [
            ("1", "1", "Excluded version", "0", "", "bad", "bad"),
            ("2", "1", "Selected", "1", "S", "13", "52"),
            ("3", "1", "Later", "1", "S2", "14", "53"),
            ("1", "2", "External", "0", "S", "bad", "bad"),
            ("1", "3", "Blank", "", "", "bad", "bad"),
            ("1", "4", "Null", None, "", "bad", "bad"),
        ])
        create_table(db, "stop_area", ["VERSION", "STOP_NR", "STOP_AREA_NR", "STOP_AREA_POS_X", "STOP_AREA_POS_Y"], [
            ("1", "1", "1", "13.1", "52.1"),
            ("1", "2", "1", "", ""), ("1", "2", "1", "", ""),
        ])
        create_table(db, "stop_point", ["VERSION", "STOP_NR", "STOP_AREA_NR", "STOPPING_POINT_NR", "GLOBAL_ID",
                                       "STOPPING_POINT_POS_X", "STOPPING_POINT_POS_Y"], [
            ("1", "1", "1", "1", "P", "", ""),
            ("1", "2", "1", "1", "S", "bad", "bad"),
            ("1", "9", "1", "1", "", "bad", "bad"),
        ])
        with caplog.at_level(logging.WARNING):
            transform(db, settings)
        station = db.execute("SELECT stop_id, stop_name FROM gtfs_stops WHERE location_type = 1").fetchone()
        point = db.execute(
            "SELECT stop_name, stop_lat, stop_lon, parent_station FROM gtfs_stops WHERE location_type = 0"
        ).fetchone()
        assert station[1] == "Selected"
        assert point == ("Selected", 52.1, 13.1, station[0])
        assert db.execute("SELECT count(*) FROM gtfs_stops").fetchone() == ((3,) if settings is GLOBAL else (2,))
        assert db.execute("SELECT * FROM map_stop_2_stops ORDER BY VERSION").fetchall() == [
            ("2", "1", station[0]), ("3", "1", "S2" if settings is GLOBAL else station[0]),
        ]
        assert db.execute(
            "SELECT m.VERSION, m.STOP_ID, m.STOP_AREA_ID, m.STOPPING_POINT_NR, s.location_type "
            "FROM map_stop_point_2_stops m JOIN gtfs_stops s ON m.gtfs_id = s.stop_id"
        ).fetchall() == [("1", "1", "1", "1", 0)]
        assert not caplog.records


def test_responsibility_column_is_required():
    with temporary_database() as db:
        db.execute("CREATE TABLE dino_stop (VERSION TEXT, STOP_NR TEXT)")
        with pytest.raises(InputError, match="IS_RESPONSIBLE_STOP"):
            transform(db, GLOBAL)


def test_no_responsible_stations_produces_no_stops(caplog):
    with temporary_database() as db:
        create_table(db, "stop", ["VERSION", "STOP_NR", "IS_RESPONSIBLE_STOP"], [
            ("1", "1", "0"), ("2", "1", ""),
        ])
        create_table(db, "stop_point", ["VERSION", "STOP_NR", "STOP_AREA_NR", "STOPPING_POINT_NR"], [
            ("1", "1", "1", "1"),
        ])
        with caplog.at_level(logging.WARNING):
            transform(db, GLOBAL)
        assert db.execute("SELECT * FROM gtfs_stops").fetchall() == []
        assert db.execute("SELECT * FROM map_stop_2_stops").fetchall() == []
        assert db.execute("SELECT * FROM map_stop_point_2_stops").fetchall() == []
        assert not caplog.records


def test_global_stations_platforms_and_coordinate_fallback(caplog):
    with temporary_database() as db:
        create_table(db, "stop", ["VERSION", "STOP_NR", "STOP_NAME", "STOP_SHORTNAME", "STOP_POS_X", "STOP_POS_Y", "GLOBAL_ID"], [
            ("1", "10", "City Station", "ST", "13", "52", "S"),
            ("2", "10", "City Station", "ST", "13", "52", "S"),
            ("1", "20", "Missing", "", "", "-1", "M"),
        ])
        create_table(db, "stop_area", ["VERSION", "STOP_NR", "STOP_AREA_NR", "STOP_AREA_POS_X", "STOP_AREA_POS_Y"], [
            ("1", "10", "2", "13.1", "52.1"), ("2", "10", "2", "13.1", "52.1"),
        ])
        create_table(db, "stop_point", ["VERSION", "STOP_NR", "STOP_AREA_NR", "STOPPING_POINT_NR",
                                      "STOPPING_POINT_SHORTNAME", "GLOBAL_ID", "STREET_ACCESS",
                                      "STOPPING_POINT_POS_X", "STOPPING_POINT_POS_Y"], [
            ("1", "10", "2", "3", "P", "P", "4", "", ""),
            ("2", "10", "2", "3", "P", "P", "4", "", ""),
            ("1", "10", "2", "4", "Q", "Q", "0", "13.2", "52.2"),
        ])
        with caplog.at_level(logging.WARNING):
            transform(db, GLOBAL)
        assert db.execute(
            "SELECT stop_id, stop_name, stop_code, stop_lat, stop_lon, location_type, parent_station, stop_access "
            "FROM gtfs_stops ORDER BY stop_id"
        ).fetchall() == [
            ("M", "Missing", None, 0.0, 0.0, 1, None, None),
            ("P", "City Station", "P", 52.1, 13.1, 0, "S", 1),
            ("Q", "City Station", "Q", 52.2, 13.2, 0, "S", 0),
            ("S", "City Station", "ST", 52.0, 13.0, 1, None, None),
        ]
        assert "missing coordinates" in caplog.text


def test_template_mode_generates_distinct_ids_and_parent_reference(caplog):
    with temporary_database() as db:
        create_table(db, "stop", ["VERSION", "STOP_NR", "STOP_NAME"], [("1", "10", "Town")])
        create_table(db, "stop_point", ["VERSION", "STOP_NR", "STOP_AREA_NR", "STOPPING_POINT_NR"], [
            ("1", "10", "2", "3"),
        ])
        transform(db, TEMPLATE)
        assert db.execute("SELECT stop_id, parent_station FROM gtfs_stops ORDER BY location_type DESC").fetchall() == [
            ("stop:station:7:station2:10", None),
            ("stop:platform:8:platform2:101:21:3", "stop:station:7:station2:10"),
        ]
        assert not any("global ID collision" in record.message for record in caplog.records)


def test_removed_stop_source_key_placeholder_is_rejected():
    settings = {"ids": {"stops": {"mode": "template", "template": "{gtfs.stop_source_key}"}}}
    with temporary_database() as db:
        create_table(db, "stop", ["VERSION", "STOP_NR"], [])
        with pytest.raises(InputError, match="gtfs.stop_source_key"):
            transform(db, settings)


def test_template_mode_supports_generic_fingerprint_and_source_field():
    settings = {"ids": {"stops": {"mode": "template",
                "template": "{gtfs.fingerprint}:{stop.VERSION}"}}, "input": {"crs": "EPSG:4326"}}
    with temporary_database() as db:
        create_table(db, "stop", ["VERSION", "STOP_NR", "STOP_NAME"], [("1", "10", "Town")])
        create_table(db, "stop_point", ["VERSION", "STOP_NR", "STOP_AREA_NR", "STOPPING_POINT_NR"], [
            ("1", "10", "2", "3"),
        ])
        transform(db, settings)
        station, platform = db.execute(
            "SELECT stop_id, parent_station FROM gtfs_stops ORDER BY location_type DESC"
        ).fetchall()
        assert station[0].endswith(":1")
        assert platform[0].endswith(":1")
        assert station[0] != platform[0]
        assert platform[1] == station[0]


def test_deduplicates_by_non_version_keys_and_keeps_first(caplog):
    settings = TEMPLATE
    with temporary_database() as db:
        create_table(db, "stop", ["VERSION", "STOP_NR", "STOP_NAME", "GLOBAL_ID", "STOP_POS_X", "STOP_POS_Y"], [
            ("1", "10", "First", "S", "13", "52"),
            ("2", "10", "Other", "S2", "14", "53"),
            ("1", "10", "Duplicate", "S3", "15", "54"),
        ])
        create_table(db, "stop_point", ["VERSION", "STOP_NR", "STOP_AREA_NR", "STOPPING_POINT_NR", "GLOBAL_ID",
                                       "STOPPING_POINT_SHORTNAME"], [
            ("2", "10", "1", "1", "P", "First point"),
            ("1", "10", "1", "1", "P2", "Other point"),
            ("1", "10", "2", "1", "Q", "Different area"),
        ])
        with caplog.at_level(logging.WARNING):
            transform(db, settings)
        station = db.execute("SELECT stop_id, stop_name FROM gtfs_stops WHERE location_type = 1").fetchone()
        points = db.execute(
            "SELECT stop_id, stop_code, stop_lat, stop_lon, parent_station FROM gtfs_stops "
            "WHERE location_type = 0 ORDER BY stop_code"
        ).fetchall()
        assert station[1] == "First"
        assert [(point[1], point[2], point[3], point[4]) for point in points] == [
            ("Different area", 52, 13, station[0]), ("First point", 52, 13, station[0]),
        ]
        assert db.execute("SELECT * FROM map_stop_2_stops ORDER BY VERSION").fetchall() == [
            ("1", "10", station[0]), ("2", "10", station[0]),
        ]
        assert db.execute(
            "SELECT * FROM map_stop_point_2_stops ORDER BY VERSION, STOP_AREA_ID"
        ).fetchall() == [
            ("1", "10", "1", "1", points[1][0]), ("1", "10", "2", "1", points[0][0]),
            ("2", "10", "1", "1", points[1][0]),
        ]
        assert not caplog.records


@pytest.mark.parametrize("settings", [GLOBAL, TEMPLATE])
@pytest.mark.parametrize("zero_first", [True, False])
@pytest.mark.parametrize("other_version", ["1", "2"])
def test_prefers_nonzero_area_for_same_stop_point_without_warning(caplog, settings, zero_first, other_version):
    with temporary_database() as db:
        create_table(db, "stop", ["VERSION", "STOP_NR", "STOP_NAME", "GLOBAL_ID", "STOP_POS_X", "STOP_POS_Y"], [
            ("1", "10", "First", "S", "13", "52"),
            ("1", "20", "Second", "T", "13", "52"),
        ])
        zero = ("1", "10", "0", "1", "P", "discarded", "bad", "bad")
        nonzero = (other_version, "10", "1", "1", "P", "preferred", "", "")
        rows = [zero, nonzero] if zero_first else [nonzero, zero]
        rows.extend([
            ("1", "10", "2", "1", "Q", "other area", "", ""),
            ("1", "10", "0", "2", "R", "zero only", "", ""),
            ("1", "20", "0", "1", "U", "other station", "", ""),
        ])
        create_table(db, "stop_point", ["VERSION", "STOP_NR", "STOP_AREA_NR", "STOPPING_POINT_NR", "GLOBAL_ID",
                                       "STOPPING_POINT_SHORTNAME", "STOPPING_POINT_POS_X", "STOPPING_POINT_POS_Y"], rows)
        with caplog.at_level(logging.WARNING):
            transform(db, settings)
        assert db.execute(
            "SELECT stop_code, stop_lat, stop_lon FROM gtfs_stops WHERE location_type = 0 ORDER BY stop_code"
        ).fetchall() == [
            ("other area", 52, 13), ("other station", 52, 13), ("preferred", 52, 13), ("zero only", 52, 13),
        ]
        preferred_id = db.execute("SELECT stop_id FROM gtfs_stops WHERE stop_code = 'preferred'").fetchone()[0]
        assert db.execute(
            "SELECT * FROM map_stop_point_2_stops WHERE STOP_ID = '10' AND STOPPING_POINT_NR = '1' "
            "AND STOP_AREA_ID IN ('0', '1') ORDER BY STOP_AREA_ID"
        ).fetchall() == [
            ("1", "10", "0", "1", preferred_id), (other_version, "10", "1", "1", preferred_id),
        ]
        assert not caplog.records


@pytest.mark.parametrize("settings", [GLOBAL, TEMPLATE])
@pytest.mark.parametrize("zero_first", [True, False])
@pytest.mark.parametrize("other_version", ["1", "2"])
def test_prefers_nonzero_area_for_same_global_id_and_station(caplog, settings, zero_first, other_version):
    with temporary_database() as db:
        create_table(db, "stop", ["VERSION", "STOP_NR", "STOP_NAME", "GLOBAL_ID", "STOP_POS_X", "STOP_POS_Y"], [
            ("1", "10", "Station", "S", "13", "52"),
        ])
        zero = ("1", "10", "0", "1", "P", "discarded")
        nonzero = (other_version, "10", "2", "3", "P", "preferred")
        rows = [zero, nonzero] if zero_first else [nonzero, zero]
        create_table(db, "stop_point", ["VERSION", "STOP_NR", "STOP_AREA_NR", "STOPPING_POINT_NR",
                                       "GLOBAL_ID", "STOPPING_POINT_SHORTNAME"], rows)
        with caplog.at_level(logging.WARNING):
            transform(db, settings)
        assert db.execute("SELECT stop_code FROM gtfs_stops WHERE location_type = 0").fetchall() == [("preferred",)]
        preferred_id = db.execute("SELECT stop_id FROM gtfs_stops WHERE location_type = 0").fetchone()[0]
        assert db.execute("SELECT * FROM map_stop_point_2_stops ORDER BY STOP_AREA_ID").fetchall() == [
            ("1", "10", "0", "1", preferred_id), (other_version, "10", "2", "3", preferred_id),
        ]
        assert not caplog.records


@pytest.mark.parametrize("settings", [GLOBAL, TEMPLATE])
def test_zero_area_global_id_reuse_between_stations_depends_on_mode(caplog, settings):
    with temporary_database() as db:
        create_table(db, "stop", ["VERSION", "STOP_NR", "STOP_NAME", "GLOBAL_ID"], [
            ("1", "1", "First", "S"), ("1", "2", "Second", "T"),
        ])
        create_table(db, "stop_point", ["VERSION", "STOP_NR", "STOP_AREA_NR", "STOPPING_POINT_NR", "GLOBAL_ID"], [
            ("1", "1", "0", "1", "P"), ("1", "2", "2", "3", "P"),
        ])
        if settings is GLOBAL:
            transform(db, settings)
            assert db.execute("SELECT stop_id, parent_station FROM gtfs_stops WHERE location_type = 0").fetchall() == [
                ("P", "T"),
            ]
            assert db.execute("SELECT gtfs_id FROM map_stop_point_2_stops").fetchall() == [("P",), ("P",)]
        else:
            with caplog.at_level(logging.WARNING):
                transform(db, settings)
            assert any("global ID collision" in r.message and "'P'" in r.message for r in caplog.records)
            assert db.execute("SELECT count(*) FROM gtfs_stops WHERE location_type = 0").fetchone() == (2,)


@pytest.mark.parametrize("template", ["{gtfs.stop_kind}:{gtfs.canonical}", "{gtfs.canonical}", "{gtfs.fingerprint}"])
def test_template_identity_does_not_depend_on_version(template):
    ids = []
    settings = {"ids": {"stops": {"mode": "template", "template": template}}}
    for version in ("1", "2"):
        with temporary_database() as db:
            create_table(db, "stop", ["VERSION", "STOP_NR", "STOP_NAME"], [(version, "1", "Town")])
            create_table(db, "stop_point", ["VERSION", "STOP_NR", "STOP_AREA_NR", "STOPPING_POINT_NR"], [
                (version, "1", "2", "3"),
            ])
            transform(db, settings)
            ids.append(db.execute("SELECT stop_id, parent_station FROM gtfs_stops ORDER BY location_type DESC").fetchall())
    assert ids[0] == ids[1]


def test_station_coordinate_fallback_uses_station_version_crs():
    with temporary_database() as db:
        create_table(db, "stop", ["VERSION", "STOP_NR", "STOP_NAME", "GLOBAL_ID", "STOP_POS_X", "STOP_POS_Y"],
                     [("1", "1", "Town", "S", "111319.49079327357", "0")])
        create_table(db, "coordsys", ["VERSION", "EPSG_CODE"], [("1", "3857"), ("2", "4326")])
        create_table(db, "stop_point", ["VERSION", "STOP_NR", "STOP_AREA_NR", "STOPPING_POINT_NR", "GLOBAL_ID"],
                     [("2", "1", "1", "1", "P")])
        transform(db, GLOBAL)
        lat, lon, parent = db.execute(
            "SELECT stop_lat, stop_lon, parent_station FROM gtfs_stops WHERE stop_id = 'P'"
        ).fetchone()
        assert lat == pytest.approx(0)
        assert lon == pytest.approx(1)
        assert parent == "S"


@pytest.mark.parametrize("second_version", ["1", "2"])
def test_global_mode_deduplicates_same_global_id_across_versions(second_version):
    with temporary_database() as db:
        create_table(db, "stop", ["VERSION", "STOP_NR", "STOP_NAME", "GLOBAL_ID"], [
            ("1", "1", "First", "S"), (second_version, "2", "Second", "S"),
        ])
        create_table(db, "stop_point", ["VERSION", "STOP_NR", "STOP_AREA_NR", "STOPPING_POINT_NR", "GLOBAL_ID"], [
            ("1", "1", "1", "1", "P"), (second_version, "2", "1", "1", "P"),
            (second_version, "2", "1", "2", "Q"),
        ])
        transform(db, GLOBAL)
        assert db.execute(
            "SELECT stop_id, stop_name, parent_station FROM gtfs_stops ORDER BY stop_id"
        ).fetchall() == [("P", "First", "S"), ("Q", "First", "S"), ("S", "First", None)]
        assert db.execute("SELECT * FROM map_stop_2_stops ORDER BY VERSION").fetchall() == [
            ("1", "1", "S"), (second_version, "2", "S"),
        ]
        assert db.execute(
            "SELECT * FROM map_stop_point_2_stops ORDER BY VERSION, STOPPING_POINT_NR"
        ).fetchall() == [
            ("1", "1", "1", "1", "P"), (second_version, "2", "1", "1", "P"),
            (second_version, "2", "1", "2", "Q"),
        ]


def test_global_identity_keeps_different_ids_for_same_source_numbers_and_parent_versions():
    with temporary_database() as db:
        create_table(db, "stop", ["VERSION", "STOP_NR", "STOP_NAME", "GLOBAL_ID", "STOP_POS_X", "STOP_POS_Y"], [
            ("1", "10", "First", "S", "13", "52"), ("2", "10", "Second", "T", "14", "53"),
        ])
        create_table(db, "stop_point", ["VERSION", "STOP_NR", "STOP_AREA_NR", "STOPPING_POINT_NR", "GLOBAL_ID"], [
            ("1", "10", "1", "1", "P"), ("2", "10", "1", "1", "Q"),
        ])
        transform(db, GLOBAL)
        assert db.execute(
            "SELECT stop_id, stop_name, stop_lat, stop_lon, parent_station FROM gtfs_stops ORDER BY stop_id"
        ).fetchall() == [
            ("P", "First", 52, 13, "S"), ("Q", "Second", 53, 14, "T"),
            ("S", "First", 52, 13, None), ("T", "Second", 53, 14, None),
        ]
        assert db.execute("SELECT * FROM map_stop_2_stops ORDER BY VERSION").fetchall() == [
            ("1", "10", "S"), ("2", "10", "T"),
        ]
        assert db.execute("SELECT * FROM map_stop_point_2_stops ORDER BY VERSION").fetchall() == [
            ("1", "10", "1", "1", "P"), ("2", "10", "1", "1", "Q"),
        ]


def test_global_identity_does_not_replace_zero_area_point_with_different_global_id():
    with temporary_database() as db:
        create_table(db, "stop", ["VERSION", "STOP_NR", "STOP_NAME", "GLOBAL_ID"], [("1", "1", "Town", "S")])
        create_table(db, "stop_point", ["VERSION", "STOP_NR", "STOP_AREA_NR", "STOPPING_POINT_NR", "GLOBAL_ID"], [
            ("1", "1", "0", "1", "P"), ("1", "1", "2", "1", "Q"),
        ])
        transform(db, GLOBAL)
        assert db.execute("SELECT stop_id FROM gtfs_stops WHERE location_type = 0 ORDER BY stop_id").fetchall() == [
            ("P",), ("Q",),
        ]
        assert db.execute("SELECT gtfs_id FROM map_stop_point_2_stops ORDER BY STOP_AREA_ID").fetchall() == [
            ("P",), ("Q",),
        ]


@pytest.mark.parametrize("target", ["station", "platform", "zero-area"])
def test_global_identity_rejects_ambiguous_source_mapping(target):
    with temporary_database() as db:
        stations = [("1", "1", "Town", "S")]
        if target == "station":
            stations.append(("1", "1", "Other", "T"))
        create_table(db, "stop", ["VERSION", "STOP_NR", "STOP_NAME", "GLOBAL_ID"], stations)
        if target == "platform":
            create_table(db, "stop_point", ["VERSION", "STOP_NR", "STOP_AREA_NR", "STOPPING_POINT_NR", "GLOBAL_ID"], [
                ("1", "1", "1", "1", "P"), ("1", "1", "1", "1", "Q"),
            ])
        elif target == "zero-area":
            create_table(db, "stop_point", ["VERSION", "STOP_NR", "STOP_AREA_NR", "STOPPING_POINT_NR", "GLOBAL_ID"], [
                ("1", "1", "0", "1", "P"), ("1", "1", "0", "1", "Q"),
                ("1", "1", "1", "2", "P"), ("1", "1", "2", "3", "Q"),
            ])
        with pytest.raises(CollisionError, match="conflicting map_stop"):
            transform(db, GLOBAL)
        assert db.execute("SELECT * FROM gtfs_stops").fetchall() == []
        assert db.execute("SELECT * FROM map_stop_2_stops").fetchall() == []
        assert db.execute("SELECT * FROM map_stop_point_2_stops").fetchall() == []


@pytest.mark.parametrize("settings", [GLOBAL, TEMPLATE])
def test_collision_detection_ignores_unselected_station_versions(settings, caplog):
    with temporary_database() as db:
        create_table(db, "stop", ["VERSION", "STOP_NR", "STOP_NAME", "GLOBAL_ID"], [
            ("1", "1", "First", "G"),
            ("2", "1", "First", "G"),
            ("2", "2", "Second", "G"),
        ])
        with caplog.at_level(logging.WARNING):
            transform(db, settings)
        assert not any("global ID collision" in r.message for r in caplog.records)
        assert db.execute("SELECT count(*) FROM gtfs_stops").fetchone() == ((1,) if settings is GLOBAL else (2,))


@pytest.mark.parametrize("settings", [GLOBAL, TEMPLATE])
def test_point_collision_detection_checks_versions_of_skipped_duplicates(settings, caplog):
    with temporary_database() as db:
        create_table(db, "stop", ["VERSION", "STOP_NR", "STOP_NAME", "GLOBAL_ID"], [
            ("1", "1", "Town", "S"),
        ])
        create_table(db, "stop_point", ["VERSION", "STOP_NR", "STOP_AREA_NR", "STOPPING_POINT_NR", "GLOBAL_ID"], [
            ("2", "1", "1", "1", "P"), ("1", "1", "1", "1", "P"), ("1", "1", "1", "2", "P"),
        ])
        if settings is GLOBAL:
            transform(db, settings)
            assert db.execute("SELECT stop_id FROM gtfs_stops WHERE location_type = 0").fetchall() == [("P",)]
        else:
            with caplog.at_level(logging.WARNING):
                transform(db, settings)
            messages = [r.message for r in caplog.records if "global ID collision" in r.message]
            assert len(messages) == 1
            assert "P" in messages[0] and "stop_point.din row 2" in messages[0] and "stop_point.din row 3" in messages[0]
            assert "VERSION" not in messages[0]


@pytest.mark.parametrize("settings", [GLOBAL, TEMPLATE])
def test_point_collision_checks_only_selected_responsible_station_version(settings, caplog):
    with temporary_database() as db:
        create_table(db, "stop", ["VERSION", "STOP_NR", "STOP_NAME", "IS_RESPONSIBLE_STOP", "GLOBAL_ID"], [
            ("1", "1", "External", "0", "S"), ("2", "1", "Selected", "1", "S"),
            ("3", "1", "Later", "1", "S"),
        ])
        create_table(db, "stop_point", ["VERSION", "STOP_NR", "STOP_AREA_NR", "STOPPING_POINT_NR", "GLOBAL_ID"], [
            ("1", "1", "1", "1", "P"), ("1", "1", "1", "2", "P"),
            ("3", "1", "1", "1", "P"), ("3", "1", "1", "2", "P"),
            ("2", "1", "1", "3", "Q"),
        ])
        with caplog.at_level(logging.WARNING):
            transform(db, settings)
        assert not any("global ID collision" in r.message for r in caplog.records)
        assert db.execute("SELECT count(*) FROM gtfs_stops").fetchone() == ((3,) if settings is GLOBAL else (4,))


@pytest.mark.parametrize("settings", [GLOBAL, TEMPLATE])
def test_point_collision_uses_source_station_version_after_global_alias(settings, caplog):
    with temporary_database() as db:
        create_table(db, "stop", ["VERSION", "STOP_NR", "STOP_NAME", "GLOBAL_ID"], [
            ("1", "1", "First", "S"), ("2", "2", "Second", "S"),
        ])
        create_table(db, "stop_point", ["VERSION", "STOP_NR", "STOP_AREA_NR", "STOPPING_POINT_NR", "GLOBAL_ID"], [
            ("2", "2", "1", "1", "P"), ("2", "2", "1", "2", "P"),
        ])
        if settings is GLOBAL:
            transform(db, settings)
            assert db.execute("SELECT gtfs_id FROM map_stop_point_2_stops").fetchall() == [("P",), ("P",)]
        else:
            with caplog.at_level(logging.WARNING):
                transform(db, settings)
            warnings = [r.message for r in caplog.records if "global ID collision" in r.message]
            assert len(warnings) == 1
            assert "P" in warnings[0] and "stop_point.din row 1" in warnings[0] and "stop_point.din row 2" in warnings[0]


def test_global_mode_rejects_cross_version_global_id_reused_between_stop_kinds():
    with temporary_database() as db:
        create_table(db, "stop", ["VERSION", "STOP_NR", "STOP_NAME", "GLOBAL_ID"], [
            ("1", "1", "Town", "same"),
        ])
        create_table(db, "stop_point", ["VERSION", "STOP_NR", "STOP_AREA_NR", "STOPPING_POINT_NR", "GLOBAL_ID"], [
            ("2", "1", "1", "1", "same"),
        ])
        with pytest.raises(InputError, match="same.*station.*boarding"):
            transform(db, GLOBAL)
        assert db.execute("SELECT * FROM gtfs_stops").fetchall() == []


def test_template_mode_warns_for_global_id_collisions(caplog):
    with temporary_database() as db:
        create_table(db, "stop", ["VERSION", "STOP_NR", "STOP_NAME", "GLOBAL_ID"], [
            ("1", "1", "Town", "station"),
            ("2", "1", "Town", "station"),
            ("1", "2", "Town", "station"),
            ("3", "4", "Different", "station"),
            ("1", "3", "Other", ""),
        ])
        create_table(db, "stop_point", ["VERSION", "STOP_NR", "STOP_AREA_NR", "STOPPING_POINT_NR", "GLOBAL_ID"], [
            ("1", "1", "1", "1", "platform"),
            ("2", "1", "1", "1", "platform"),
            ("1", "1", "1", "2", "platform"),
            ("1", "1", "1", "3", "station"),
            ("1", "1", "1", "4", ""),
        ])
        with caplog.at_level(logging.WARNING):
            transform(db, TEMPLATE)
        warnings = [record.message for record in caplog.records if "global ID collision" in record.message]
        assert len(warnings) == 3
        assert any("'station'" in message and "stop.din row 1" in message and "stop.din row 3" in message for message in warnings)
        assert any("'platform'" in message and "stop_point.din row 1" in message and "stop_point.din row 3" in message for message in warnings)
        assert any("'station'" in message and "stop.din row 1" in message and "stop_point.din row 4" in message for message in warnings)
        assert db.execute("SELECT count(*) FROM gtfs_stops").fetchone() == (8,)


def test_template_mode_does_not_warn_for_cross_version_global_id_reuse(caplog):
    with temporary_database() as db:
        create_table(db, "stop", ["VERSION", "STOP_NR", "STOP_NAME", "GLOBAL_ID"], [
            ("1", "1", "Town", "station-1"), ("2", "2", "Town", "station-2"),
        ])
        create_table(db, "stop_point", ["VERSION", "STOP_NR", "STOP_AREA_NR", "STOPPING_POINT_NR", "GLOBAL_ID"], [
            ("1", "1", "1", "1", "platform"), ("2", "2", "1", "1", "platform"),
        ])
        with caplog.at_level(logging.WARNING):
            transform(db, TEMPLATE)
        warnings = [record.message for record in caplog.records if "global ID collision" in record.message]
        assert not warnings


def test_template_mode_does_not_warn_for_cross_version_cross_kind_reuse(caplog):
    with temporary_database() as db:
        create_table(db, "stop", ["VERSION", "STOP_NR", "STOP_NAME", "GLOBAL_ID"], [
            ("1", "1", "Town", "same"),
        ])
        create_table(db, "stop_point", ["VERSION", "STOP_NR", "STOP_AREA_NR", "STOPPING_POINT_NR", "GLOBAL_ID"], [
            ("2", "1", "1", "1", "same"),
        ])
        with caplog.at_level(logging.WARNING):
            transform(db, TEMPLATE)
        assert not any("global ID collision" in r.message for r in caplog.records)
        assert db.execute("SELECT count(*) FROM gtfs_stops").fetchone() == (2,)


@pytest.mark.parametrize("target,global_column", [
    ("station", False), ("station", True), ("platform", False), ("platform", True),
])
def test_global_mode_rejects_missing_global_id_without_partial_output(target, global_column):
    with temporary_database() as db:
        stop_cols = ["VERSION", "STOP_NR", "STOP_NAME"]
        if target != "station" or global_column:
            stop_cols.append("GLOBAL_ID")
        stop_values = ["1", "1", "Name"] + ([""] if target == "station" and global_column else
                                              ["S"] if target != "station" else [])
        create_table(db, "stop", stop_cols, [stop_values])
        if target == "platform":
            cols = ["VERSION", "STOP_NR", "STOP_AREA_NR", "STOPPING_POINT_NR"]
            values = ["1", "1", "1", "1"]
            if global_column:
                cols.append("GLOBAL_ID")
                values.append("")
            create_table(db, "stop_point", cols, [values])
        with pytest.raises(InputError, match="GLOBAL_ID is required"):
            transform(db, GLOBAL)
        assert db.execute("SELECT * FROM gtfs_stops").fetchall() == []
        assert db.execute("SELECT * FROM map_stop_2_stops").fetchall() == []
        assert db.execute("SELECT * FROM map_stop_point_2_stops").fetchall() == []


def test_crs_metadata_and_affine_override_config():
    with temporary_database() as db:
        create_table(db, "stop", ["VERSION", "STOP_NR", "STOP_NAME", "STOP_POS_X", "STOP_POS_Y", "GLOBAL_ID"],
                     [("1", "1", "Name", "55659.74539663678", "0", "S")])
        create_table(db, "coordsys", ["VERSION", "EPSG_CODE", "TRANS_X", "TRANS_Y", "SCALE_X", "SCALE_Y"],
                     [("1", "3857", "0", "0", "2", "1")])
        transform(db, GLOBAL)
        lat, lon = db.execute("SELECT stop_lat, stop_lon FROM gtfs_stops").fetchone()
        assert lat == pytest.approx(0)
        assert lon == pytest.approx(1)


def test_missing_epsg_metadata_uses_configured_crs_and_affine():
    with temporary_database() as db:
        create_table(db, "stop", ["VERSION", "STOP_NR", "STOP_NAME", "STOP_POS_X", "STOP_POS_Y", "GLOBAL_ID"],
                     [("1", "1", "Name", "1", "2", "S")])
        create_table(db, "coordsys", ["VERSION", "TRANS_X", "SCALE_X"], [("1", "3", "2")])
        transform(db, GLOBAL)
        assert db.execute("SELECT stop_lat, stop_lon FROM gtfs_stops").fetchone() == (2, 5)


def test_platform_falls_back_to_parent_station_coordinates():
    with temporary_database() as db:
        create_table(db, "stop", ["VERSION", "STOP_NR", "STOP_NAME", "STOP_POS_X", "STOP_POS_Y", "GLOBAL_ID"],
                     [("1", "1", "Name", "13", "52", "S")])
        create_table(db, "stop_point", ["VERSION", "STOP_NR", "STOP_AREA_NR", "STOPPING_POINT_NR", "GLOBAL_ID"],
                     [("1", "1", "1", "1", "P")])
        transform(db, GLOBAL)
        assert db.execute("SELECT stop_lat, stop_lon FROM gtfs_stops WHERE stop_id = 'P'").fetchone() == (52, 13)


@pytest.mark.parametrize("point_xy,area_xy,station_xy,expected,point_warns", [
    (("14", "53"), ("", ""), ("", ""), (53, 14), False),
    (("", ""), ("13.5", "52.5"), ("", ""), (52.5, 13.5), False),
    (("", ""), ("", ""), ("13", "52"), (52, 13), False),
    (("", ""), ("", ""), ("", ""), (0, 0), True),
])
def test_coordinate_warning_requires_all_point_and_parent_coordinates_to_be_missing(
    caplog, point_xy, area_xy, station_xy, expected, point_warns
):
    with temporary_database() as db:
        create_table(db, "stop", ["VERSION", "STOP_NR", "STOP_NAME", "GLOBAL_ID", "STOP_POS_X", "STOP_POS_Y"],
                     [("1", "1", "Town", "S", *station_xy)])
        create_table(db, "stop_area", ["VERSION", "STOP_NR", "STOP_AREA_NR", "STOP_AREA_POS_X", "STOP_AREA_POS_Y"],
                     [("1", "1", "1", *area_xy)])
        create_table(db, "stop_point", ["VERSION", "STOP_NR", "STOP_AREA_NR", "STOPPING_POINT_NR", "GLOBAL_ID",
                                       "STOPPING_POINT_POS_X", "STOPPING_POINT_POS_Y"],
                     [("1", "1", "1", "1", "P", *point_xy)])
        with caplog.at_level(logging.WARNING):
            transform(db, GLOBAL)
        assert db.execute("SELECT stop_lat, stop_lon FROM gtfs_stops WHERE stop_id = 'P'").fetchone() == expected
        point_warnings = [r.message for r in caplog.records if "stop_point.din row 1" in r.message and "missing coordinates" in r.message]
        assert bool(point_warnings) is point_warns


@pytest.mark.parametrize("mode,coords,crs,error,message", [
    ("global", ("1", "2"), None, ConfigurationError, "input.crs is required"),
    ("global", ("bad", "2"), "EPSG:4326", InputError, "invalid coordinate"),
    ("global", ("1", "2"), "bad-crs", ConfigurationError, "unable to transform coordinates"),
    ("invalid", ("", ""), None, ConfigurationError, "ids.stops.mode"),
])
def test_invalid_coordinate_configuration_or_values_fail(mode, coords, crs, error, message):
    with temporary_database() as db:
        create_table(db, "stop", ["VERSION", "STOP_NR", "STOP_NAME", "GLOBAL_ID", "STOP_POS_X", "STOP_POS_Y"],
                     [("1", "1", "Name", "S", *coords)])
        with pytest.raises(error, match=message):
            transform(db, {"ids": {"stops": {"mode": mode}}, "input": {"crs": crs}})
        assert db.execute("SELECT * FROM gtfs_stops").fetchall() == []


def test_same_global_id_merges_stations_with_different_source_numbers():
    with temporary_database() as db:
        create_table(db, "stop", ["VERSION", "STOP_NR", "STOP_NAME", "GLOBAL_ID"], [
            ("1", "1", "First", "S"), ("1", "2", "Second", "S"),
        ])
        transform(db, GLOBAL)
        assert db.execute("SELECT stop_id, stop_name FROM gtfs_stops").fetchall() == [("S", "First")]
        assert db.execute("SELECT * FROM map_stop_2_stops ORDER BY STOP_ID").fetchall() == [
            ("1", "1", "S"), ("1", "2", "S"),
        ]


def test_global_ids_are_raw_and_cross_kind_collisions_fail():
    with temporary_database() as db:
        create_table(db, "stop", ["VERSION", "STOP_NR", "STOP_NAME", "GLOBAL_ID"], [
            ("1", "1", "Name", " S "),
        ])
        create_table(db, "stop_point", ["VERSION", "STOP_NR", "STOP_AREA_NR", "STOPPING_POINT_NR", "GLOBAL_ID"], [
            ("1", "1", "1", "1", " P "),
        ])
        transform(db, GLOBAL)
        assert db.execute("SELECT stop_id, parent_station FROM gtfs_stops ORDER BY location_type DESC").fetchall() == [
            (" S ", None), (" P ", " S "),
        ]
    with temporary_database() as db:
        create_table(db, "stop", ["VERSION", "STOP_NR", "STOP_NAME", "GLOBAL_ID"], [
            ("1", "1", "Name", "same"),
        ])
        create_table(db, "stop_point", ["VERSION", "STOP_NR", "STOP_AREA_NR", "STOPPING_POINT_NR", "GLOBAL_ID"], [
            ("1", "1", "1", "1", "same"),
        ])
        with pytest.raises(InputError, match="same.*stop.din row 1.*stop_point.din row 1"):
            transform(db, GLOBAL)
        assert db.execute("SELECT * FROM gtfs_stops").fetchall() == []


def test_template_collisions_fail():
    settings = {"ids": {"stops": {"mode": "template", "template": "same"}}}
    with temporary_database() as db:
        create_table(db, "stop", ["VERSION", "STOP_NR", "STOP_NAME"], [
            ("1", "1", "First"), ("1", "2", "Second"),
        ])
        with pytest.raises(CollisionError, match="same.*stop.din row 1.*stop.din row 2"):
            transform(db, settings)
        assert db.execute("SELECT * FROM gtfs_stops").fetchall() == []
    with temporary_database() as db:
        create_table(db, "stop", ["VERSION", "STOP_NR", "STOP_NAME"], [
            ("1", "1", "Same"), ("2", "2", "Same"),
        ])
        with pytest.raises(CollisionError, match="same.*stop.din row 1.*stop.din row 2"):
            transform(db, settings)
        assert db.execute("SELECT * FROM gtfs_stops").fetchall() == []


@pytest.mark.parametrize("point_xy,station_xy,expected,warns", [
    (("14", "53"), ("13", "52"), (53, 14), False),
    (("", ""), ("13", "52"), (52, 13), False),
    (("", ""), ("", "-1"), (0, 0), True),
])
def test_missing_area_uses_point_or_station_coordinates_and_warns_for_zero(caplog, point_xy, station_xy, expected, warns):
    with temporary_database() as db:
        create_table(db, "stop", ["VERSION", "STOP_NR", "STOP_NAME", "GLOBAL_ID", "STOP_POS_X", "STOP_POS_Y"],
                     [("1", "1", "Name", "S", *station_xy)])
        create_table(db, "stop_area", ["VERSION", "STOP_NR", "STOP_AREA_NR"], [("1", "1", "2")])
        create_table(db, "stop_point", ["VERSION", "STOP_NR", "STOP_AREA_NR", "STOPPING_POINT_NR", "GLOBAL_ID",
                                       "STOPPING_POINT_POS_X", "STOPPING_POINT_POS_Y"],
                     [("1", "1", "1", "1", "P", *point_xy)])
        with caplog.at_level(logging.WARNING):
            transform(db, GLOBAL)
        assert db.execute("SELECT stop_lat, stop_lon, parent_station FROM gtfs_stops WHERE stop_id = 'P'").fetchone() == (
            *expected, "S",
        )
        point_warnings = [record.message for record in caplog.records if "stop_point.din row 1" in record.message]
        assert bool(point_warnings) is warns
        if warns:
            assert "missing coordinates; using 0,0" in point_warnings[0]


@pytest.mark.parametrize("section,expected_name", [("defaults", "Source"), ("overrides", "Configured")])
def test_stop_configured_fields_follow_precedence(section, expected_name):
    with temporary_database() as db:
        create_table(db, "stop", ["VERSION", "STOP_NR", "STOP_NAME", "GLOBAL_ID"], [("1", "1", "Source", "S")])
        settings = {**GLOBAL, section: {"stops": {"stop_name": "Configured", "stop_desc": "Description"}}}
        transform(db, settings)
        assert db.execute("SELECT stop_name, stop_desc FROM gtfs_stops").fetchone() == (expected_name, "Description")


def test_stop_defaults_preserve_coordinates_location_types_and_zero_access():
    with temporary_database() as db:
        create_table(db, "stop", ["VERSION", "STOP_NR", "STOP_NAME", "GLOBAL_ID", "STOP_POS_X", "STOP_POS_Y"],
                     [("1", "1", "Source", "S", "0", "0")])
        create_table(db, "stop_point", ["VERSION", "STOP_NR", "STOP_AREA_NR", "STOPPING_POINT_NR", "GLOBAL_ID",
                                       "STREET_ACCESS"], [("1", "1", "1", "1", "P", "0")])
        transform(db, {**GLOBAL, "defaults": {"stops": {
            "stop_name": "Default", "stop_lat": 52, "stop_lon": 13, "location_type": 3, "stop_access": 1,
        }}})
        assert db.execute(
            "SELECT stop_name, stop_lat, stop_lon, location_type, stop_access FROM gtfs_stops ORDER BY location_type"
        ).fetchall() == [("Source", 0.0, 0.0, 0, 0), ("Source", 0.0, 0.0, 1, 1)]


@pytest.mark.parametrize("section,expected_xy", [("defaults", (0.0, 0.0)), ("overrides", (52, 13))])
def test_stop_coordinate_overrides_are_distinct_from_defaults(section, expected_xy):
    with temporary_database() as db:
        create_table(db, "stop", ["VERSION", "STOP_NR", "STOP_NAME", "GLOBAL_ID", "STOP_POS_X", "STOP_POS_Y"],
                     [("1", "1", "Source", "S", "0", "0")])
        transform(db, {**GLOBAL, section: {"stops": {"stop_lat": 52, "stop_lon": 13}}})
        assert db.execute("SELECT stop_lat, stop_lon FROM gtfs_stops").fetchone() == expected_xy


def test_missing_stop_coordinates_use_defaults_before_zero_fallback(caplog):
    with temporary_database() as db:
        create_table(db, "stop", ["VERSION", "STOP_NR", "STOP_NAME", "GLOBAL_ID"], [("1", "1", "Source", "S")])
        create_table(db, "stop_point", ["VERSION", "STOP_NR", "STOP_AREA_NR", "STOPPING_POINT_NR", "GLOBAL_ID"],
                     [("1", "1", "1", "1", "P")])
        transform(db, {**GLOBAL, "defaults": {"stops": {"stop_lat": 52, "stop_lon": 13}}})
        assert db.execute("SELECT stop_lat, stop_lon FROM gtfs_stops").fetchall() == [(52, 13), (52, 13)]
        assert not any(record.levelno >= logging.WARNING for record in caplog.records)


def test_stop_overrides_clear_optional_fields_without_reapplying_defaults():
    with temporary_database() as db:
        create_table(db, "stop", ["VERSION", "STOP_NR", "STOP_NAME", "GLOBAL_ID", "STOP_SHORTNAME"],
                     [("1", "1", "Source", "S", "Code")])
        transform(db, {**GLOBAL, "defaults": {"stops": {"stop_code": "Default"}},
                       "overrides": {"stops": {"stop_code": None}}})
        assert db.execute("SELECT stop_code FROM gtfs_stops").fetchone() == (None,)


@pytest.mark.parametrize("source_name", [None, "", "   "])
def test_missing_stop_name_warns_and_is_inherited_by_platform(caplog, source_name):
    with temporary_database() as db:
        columns = ["VERSION", "STOP_NR", "GLOBAL_ID", "STOP_POS_X", "STOP_POS_Y"]
        row = ["1", "1", "S", "13", "52"]
        if source_name is not None:
            columns.append("STOP_NAME")
            row.append(source_name)
        create_table(db, "stop", columns, [row])
        create_table(db, "stop_point", ["VERSION", "STOP_NR", "STOP_AREA_NR", "STOPPING_POINT_NR", "GLOBAL_ID"],
                     [("1", "1", "1", "1", "P")])
        with caplog.at_level(logging.WARNING):
            transform(db, GLOBAL)
        assert db.execute("SELECT stop_id, stop_name, parent_station FROM gtfs_stops ORDER BY location_type DESC").fetchall() == [
            ("S", None, None), ("P", None, "S"),
        ]
        assert any("stop.din row 1" in record.message and "STOP_NAME" in record.message for record in caplog.records)


@pytest.mark.parametrize("section", ["defaults", "overrides"])
def test_configured_stop_name_avoids_missing_name_warning(caplog, section):
    with temporary_database() as db:
        create_table(db, "stop", ["VERSION", "STOP_NR", "GLOBAL_ID"], [("1", "1", "S")])
        with caplog.at_level(logging.WARNING):
            transform(db, {**GLOBAL, section: {"stops": {"stop_name": "Default"}}})
        assert db.execute("SELECT stop_name FROM gtfs_stops").fetchone() == ("Default",)
        assert not any("STOP_NAME" in record.message for record in caplog.records)
