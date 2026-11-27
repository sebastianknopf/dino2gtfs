import logging

import pytest

from dino2gtfs.domain.errors import CollisionError, ConfigurationError, InputError
from dino2gtfs.infrastructure.sqlite import temporary_database
from dino2gtfs.transformers.trips import TripsTransformer


SETTINGS = {"ids": {"trips": {"mode": "template", "template": "trip:{gtfs.canonical}"}}}


def transform(db, settings=SETTINGS):
    transformer = TripsTransformer()
    transformer.initialize(db, settings)
    transformer.transform(db, settings)


def create_tables(db):
    db.execute(
        "CREATE TABLE dino_trip (VERSION TEXT, LINE_NR TEXT, STR_LINE_VAR TEXT, LINE_DIR_NR TEXT, TRIP_ID TEXT, "
        "TRIP_ID_PRINTING TEXT, TIMING_GROUP_NR TEXT, DEPARTURE_TIME TEXT, DAY_ATTRIBUTE_NR TEXT, RESTRICTION TEXT, "
        "GLOBAL_ID TEXT, BIKE_ALLOWED TEXT)"
    )
    db.execute(
        "CREATE TABLE dino_route (VERSION TEXT, LINE_NR TEXT, STR_LINE_VAR TEXT, LINE_DIR_NR TEXT, "
        "LINE_CONSEC_NR TEXT, STOP_NR TEXT, STOPPING_POINT_NR TEXT, STOPPING_POINT_TYPE TEXT, LENGTH TEXT)"
    )
    db.execute(
        "CREATE TABLE dino_timing_pattern (VERSION TEXT, LINE_NR TEXT, STR_LINE_VAR TEXT, LINE_DIR_NR TEXT, "
        "LINE_CONSEC_NR TEXT, TIMING_GROUP_NR TEXT, TT_REL TEXT, STOPPING_TIME TEXT)"
    )
    db.execute(
        "CREATE TABLE dino_trip_stop_time (VERSION TEXT, LINE_NR TEXT, TRIP_ID TEXT, LINE_CONSEC_NR TEXT, "
        "STOPPING_TIME TEXT)"
    )
    db.execute("CREATE TABLE dino_line (VERSION TEXT, LINE_NR TEXT, BRANCH_NR TEXT)")
    db.execute(
        "CREATE TABLE dino_vehicle_destination_text "
        "(VERSION TEXT, BRANCH_NR TEXT, VDT_NR TEXT, VDT_TEXT_FRONT1 TEXT, VDT_TEXT_INTERIOR1 TEXT)"
    )
    db.execute(
        "CREATE TABLE dino_trip_vdt (VERSION TEXT, LINE_NR TEXT, STR_LINE_VAR TEXT, LINE_DIR_NR TEXT, TRIP_ID TEXT, "
        "LINE_CONSEC_NR TEXT, VDT_NR TEXT)"
    )
    db.execute(
        "CREATE TABLE map_line_2_routes (VERSION TEXT, LINE_NR TEXT, STR_LINE_VAR TEXT, LINE_DIR_NR TEXT, gtfs_id TEXT)"
    )
    db.execute(
        "CREATE TABLE map_route_2_shapes "
        "(VERSION TEXT, LINE_NR TEXT, STR_LINE_VAR TEXT, LINE_DIR_NR TEXT, gtfs_id TEXT)"
    )
    db.execute(
        "CREATE TABLE map_calendar_2_calendar (VERSION TEXT, DAY_ATTRIBUTE_NR TEXT, RESTRICTION TEXT, gtfs_id TEXT)"
    )
    db.execute("CREATE TABLE map_stop_2_stops (VERSION TEXT, STOP_ID TEXT, gtfs_id TEXT)")
    db.execute(
        "CREATE TABLE map_stop_point_2_stops "
        "(VERSION TEXT, STOP_ID TEXT, STOP_AREA_ID TEXT, STOPPING_POINT_NR TEXT, gtfs_id TEXT)"
    )


def add_line(db, shape=True, start="86100", restriction=None, bikes=None):
    """Add line L with the stops A (0), B (5), C (2) and D (3), one trip and all mapping rows.

    Arrivals follow from the pattern: B after 60s, C after 120s, D after 180s; dwell times are 0, 30, 60 and 0s.
    The segments to B, C and D are 100m, 200m and 300m long.
    """
    db.executemany("INSERT INTO dino_route VALUES ('1', 'L', 'V', '1', ?, ?, '1', ?, ?)", [
        ("1", "A", "0", "0"), ("2", "B", "5", "100"), ("3", "C", "2", "200"), ("4", "D", "3", "300"),
    ])
    db.executemany("INSERT INTO dino_timing_pattern VALUES ('1', 'L', 'V', '1', ?, 'G', ?, ?)", [
        ("1", "0", "0"), ("2", "60", "30"), ("3", "120", "60"), ("4", "180", "0"),
    ])
    db.execute("INSERT INTO dino_trip VALUES ('1', 'L', 'V', '1', 'T1', '4711', 'G', ?, 'D1', ?, NULL, ?)",
               (start, restriction, bikes))
    db.execute("INSERT INTO map_line_2_routes VALUES ('1', 'L', 'V', '1', 'route:L')")
    db.execute("INSERT INTO map_calendar_2_calendar VALUES ('1', 'D1', ?, 'service:1:D1')", (restriction,))
    db.executemany("INSERT INTO map_stop_point_2_stops VALUES ('1', ?, '1', '1', ?)", [
        (stop, f"S{stop}") for stop in "ABCD"
    ])
    if shape:
        db.execute("INSERT INTO map_route_2_shapes VALUES ('1', 'L', 'V', '1', 'shape:1:L:V:1')")


def stop_times(db):
    return db.execute(
        "SELECT stop_id, stop_sequence, arrival_time, departure_time, pickup_type, drop_off_type, stop_headsign "
        "FROM gtfs_stop_times ORDER BY stop_sequence"
    ).fetchall()


def test_trip_and_stop_times_use_mapped_ids_and_timing_pattern():
    with temporary_database() as db:
        create_tables(db)
        add_line(db)

        transform(db)

        assert db.execute(
            "SELECT trip_id, route_id, service_id, trip_short_name, direction_id, shape_id FROM gtfs_trips"
        ).fetchall() == [("trip:1:L:V:1:T1:D1", "route:L", "service:1:D1", "4711", 0, "shape:1:L:V:1")]
        # B is omitted, the sequence is renumbered, and the trip runs over midnight
        assert stop_times(db) == [
            ("SA", 1, "23:55:00", "23:55:00", 0, 0, None),
            ("SC", 2, "23:58:30", "23:59:30", 1, 0, None),
            ("SD", 3, "24:02:30", "24:02:30", 0, 1, None),
        ]
        assert db.execute("SELECT * FROM map_trip_2_trips").fetchall() == [
            ("1", "L", "V", "1", "T1", "D1", None, "trip:1:L:V:1:T1:D1")
        ]


def test_shape_dist_traveled_accumulates_route_lengths_including_omitted_stops():
    with temporary_database() as db:
        create_tables(db)
        add_line(db)

        transform(db)

        assert db.execute(
            "SELECT stop_id, shape_dist_traveled FROM gtfs_stop_times ORDER BY stop_sequence"
        ).fetchall() == [("SA", 0.0), ("SC", 300.0), ("SD", 600.0)]


def test_missing_route_length_fails():
    with temporary_database() as db:
        create_tables(db)
        add_line(db)
        db.execute("UPDATE dino_route SET LENGTH = '' WHERE LINE_CONSEC_NR = '3'")

        with pytest.raises(InputError, match=r"route\.din row 3"):
            transform(db)


@pytest.mark.parametrize(("value", "expected"), [("0", 2), ("1", 1), ("2", 0), (None, 0), ("", 0)])
def test_bikes_allowed(value, expected):
    with temporary_database() as db:
        create_tables(db)
        add_line(db, bikes=value)

        transform(db)

        assert db.execute("SELECT bikes_allowed FROM gtfs_trips").fetchone() == (expected,)


def test_only_types_minus_one_and_five_are_omitted():
    with temporary_database() as db:
        create_tables(db)
        add_line(db)
        db.execute("UPDATE dino_route SET STOPPING_POINT_TYPE = '-1' WHERE STOP_NR = 'B'")
        db.execute("UPDATE dino_route SET STOPPING_POINT_TYPE = '9' WHERE STOP_NR = 'C'")
        db.execute("UPDATE dino_route SET STOPPING_POINT_TYPE = '' WHERE STOP_NR = 'D'")

        transform(db)

        assert [(row[0], row[4], row[5]) for row in stop_times(db)] == [("SA", 0, 0), ("SC", 0, 0), ("SD", 0, 0)]


def test_trip_id_and_headsign_are_free_of_whitespace_and_control_characters():
    for settings, global_id in ((SETTINGS, None), ({"ids": {"trips": {"mode": "global"}}}, " A\tB\r\nC \n")):
        with temporary_database() as db:
            create_tables(db)
            add_line(db)
            add_branchless_destination(db)
            db.execute("UPDATE dino_vehicle_destination_text SET VDT_TEXT_FRONT1 = 'Alpha\tCity\r\n'")
            db.execute("UPDATE dino_trip SET GLOBAL_ID = ?, TRIP_ID = 'T\t1\n'", (global_id,))
            db.execute("UPDATE dino_trip_vdt SET TRIP_ID = 'T\t1\n'")

            transform(db, settings)

            expected = "A_B_C" if global_id else "trip:1:L:V:1:T_1_:D1"
            assert db.execute("SELECT trip_id, trip_headsign FROM gtfs_trips").fetchone() == (expected, "Alpha City")
            assert {row[0] for row in db.execute("SELECT trip_id FROM gtfs_stop_times")} == {expected}


def test_stop_headsign_is_free_of_whitespace_and_control_characters():
    with temporary_database() as db:
        create_tables(db)
        add_line(db)
        db.execute("INSERT INTO dino_line VALUES ('1', 'L', NULL)")
        db.executemany("INSERT INTO dino_vehicle_destination_text VALUES ('1', NULL, ?, ?, NULL)", [
            ("1", "Alpha"), ("2", "Be\tta\r\n"),
        ])
        db.executemany("INSERT INTO dino_trip_vdt VALUES ('1', 'L', 'V', '1', 'T1', ?, ?)", [("1", "1"), ("3", "2")])

        transform(db)

        assert [row[6] for row in stop_times(db)] == [None, "Be ta", "Be ta"]


def test_trip_stop_time_overrides_pattern_dwell():
    with temporary_database() as db:
        create_tables(db)
        add_line(db)
        db.execute("INSERT INTO dino_trip_stop_time VALUES ('1', 'L', 'T1', '3', '0')")

        transform(db)

        assert [row[2:4] for row in stop_times(db)] == [
            ("23:55:00", "23:55:00"), ("23:58:30", "23:58:30"), ("24:01:30", "24:01:30"),
        ]


def test_service_is_resolved_by_day_attribute_and_restriction():
    with temporary_database() as db:
        create_tables(db)
        add_line(db, restriction="R")

        transform(db)

        assert db.execute("SELECT service_id FROM gtfs_trips").fetchall() == [("service:1:D1",)]
        # The restriction is part of the canonical trip ID
        assert db.execute("SELECT trip_id FROM gtfs_trips").fetchall() == [("trip:1:L:V:1:T1:D1:R",)]


def test_trip_headsign_and_stop_headsign_override():
    with temporary_database() as db:
        create_tables(db)
        add_line(db)
        db.execute("INSERT INTO dino_line VALUES ('1', 'L', '7')")
        db.executemany("INSERT INTO dino_vehicle_destination_text VALUES ('1', '7', ?, ?, NULL)", [
            ("1", "Alpha"), ("2", "Beta"),
        ])
        db.executemany("INSERT INTO dino_trip_vdt VALUES ('1', 'L', 'V', '1', 'T1', ?, ?)", [("1", "1"), ("3", "2")])

        transform(db)

        assert db.execute("SELECT trip_headsign FROM gtfs_trips").fetchall() == [("Alpha",)]
        assert [row[6] for row in stop_times(db)] == [None, "Beta", "Beta"]


def add_branchless_destination(db):
    db.execute("INSERT INTO dino_line VALUES ('1', 'L', NULL)")
    db.execute("INSERT INTO dino_vehicle_destination_text VALUES ('1', NULL, '1', 'Alpha', 'Inside')")
    db.execute("INSERT INTO dino_trip_vdt VALUES ('1', 'L', 'V', '1', 'T1', '1', '1')")


def test_destination_text_without_branch_is_accepted():
    with temporary_database() as db:
        create_tables(db)
        add_line(db)
        add_branchless_destination(db)

        transform(db)

        assert db.execute("SELECT trip_headsign FROM gtfs_trips").fetchall() == [("Alpha",)]


def test_headsign_default_applies_only_without_destination_text(caplog):
    settings = {**SETTINGS, "defaults": {"trips": {"trip_headsign": "Unknown"}}}
    with temporary_database() as db:
        create_tables(db)
        add_line(db)

        with caplog.at_level(logging.WARNING):
            transform(db, settings)

        assert db.execute("SELECT trip_headsign FROM gtfs_trips").fetchall() == [("Unknown",)]
        assert "no headsign found" not in caplog.text

    with temporary_database() as db:
        create_tables(db)
        add_line(db)
        add_branchless_destination(db)

        transform(db, settings)

        assert db.execute("SELECT trip_headsign FROM gtfs_trips").fetchall() == [("Alpha",)]


def test_headsign_override_uses_destination_text_column():
    settings = {
        **SETTINGS,
        "overrides": {"trips": {"trip_headsign": "{vehicle_destination_text.VDT_TEXT_INTERIOR1}"}},
    }
    with temporary_database() as db:
        create_tables(db)
        add_line(db)
        add_branchless_destination(db)

        transform(db, settings)

        assert db.execute("SELECT trip_headsign FROM gtfs_trips").fetchall() == [("Inside",)]


def test_headsign_override_with_unknown_placeholder_or_column_fails():
    with temporary_database() as db:
        create_tables(db)
        add_line(db)
        settings = {**SETTINGS, "overrides": {"trips": {"trip_headsign": "{trip.TRIP_ID}"}}}
        with pytest.raises(ConfigurationError, match="unsupported placeholder"):
            transform(db, settings)

    with temporary_database() as db:
        create_tables(db)
        add_line(db)
        settings = {**SETTINGS, "overrides": {"trips": {"trip_headsign": "{vehicle_destination_text.NOPE}"}}}
        with pytest.raises(ConfigurationError, match="NOPE"):
            transform(db, settings)


def test_missing_headsign_and_shape_warn(caplog):
    with temporary_database() as db:
        create_tables(db)
        add_line(db, shape=False)

        with caplog.at_level(logging.WARNING):
            transform(db)

        assert "trip.din row 1: trip 'trip:1:L:V:1:T1:D1' has no shape" in caplog.text
        assert "trip.din row 1: no headsign found for trip 'trip:1:L:V:1:T1:D1' or its stop times" in caplog.text
        assert db.execute("SELECT shape_id FROM gtfs_trips").fetchall() == [(None,)]


def test_unmapped_route_fails():
    with temporary_database() as db:
        create_tables(db)
        add_line(db)
        db.execute("DELETE FROM map_line_2_routes")

        with pytest.raises(InputError, match=r"trip\.din row 1: no GTFS route"):
            transform(db)


def test_unmapped_service_fails():
    with temporary_database() as db:
        create_tables(db)
        add_line(db)
        db.execute("DELETE FROM map_calendar_2_calendar")

        with pytest.raises(InputError, match=r"trip\.din row 1: no GTFS service for DAY_ATTRIBUTE_NR 'D1'"):
            transform(db)


def test_duplicate_trip_id_fails():
    with temporary_database() as db:
        create_tables(db)
        add_line(db)
        db.execute("INSERT INTO dino_trip VALUES ('1', 'L', 'V', '1', 'T2', '4712', 'G', '3600', 'D1', NULL, NULL, NULL)")
        settings = {"ids": {"trips": {"mode": "template", "template": "trip:{trip.VERSION}"}}}

        with pytest.raises(CollisionError, match="Identity collision in gtfs_trips: 'trip:1'"):
            transform(db, settings)


def test_invalid_mode_fails():
    with temporary_database() as db:
        create_tables(db)

        with pytest.raises(ConfigurationError, match="ids.trips.mode must be global or template"):
            transform(db, {"ids": {"trips": {"mode": "other"}}})
