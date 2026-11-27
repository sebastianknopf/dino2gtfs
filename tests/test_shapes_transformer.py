import logging

import pytest

from dino2gtfs.domain.errors import ConfigurationError, InputError
from dino2gtfs.infrastructure.sqlite import temporary_database
from dino2gtfs.transformers.shapes import ShapesTransformer


SETTINGS = {
    "input": {"crs": "EPSG:4326"},
    "ids": {"shapes": {"mode": "template", "template": "shape:{gtfs.canonical}"}},
}


def transform(db, settings=SETTINGS):
    transformer = ShapesTransformer()
    transformer.initialize(db, settings)
    transformer.transform(db, settings)


def create_tables(db):
    db.execute("CREATE TABLE dino_version (VERSION TEXT, PERIOD_PRIORITY TEXT)")
    db.execute(
        "CREATE TABLE dino_route (VERSION TEXT, LINE_NR TEXT, STR_LINE_VAR TEXT, LINE_DIR_NR TEXT, "
        "LINE_CONSEC_NR TEXT, STOP_NR TEXT, STOPPING_POINT_NR TEXT)"
    )
    db.execute(
        "CREATE TABLE dino_link (VERSION TEXT, LINK_ID TEXT, ORIG_STOP_NR TEXT, ORIG_STOP_AREA_NR TEXT, "
        "STOPPING_POINT_NR TEXT, DEST_STOP_NR TEXT, DEST_STOP_AREA_NR TEXT, DEST_STOPPING_POINT_NR TEXT)"
    )
    db.execute(
        "CREATE TABLE dino_link_geometry (VERSION TEXT, LINK_ID TEXT, LINK_CONSEC_PT_NR TEXT, "
        "LINK_PT_X TEXT, LINK_PT_Y TEXT)"
    )
    db.execute(
        "CREATE TABLE dino_stop_point (VERSION TEXT, STOP_NR TEXT, STOP_AREA_NR TEXT, STOPPING_POINT_NR TEXT)"
    )
    db.execute(
        "CREATE TABLE dino_stop_area (VERSION TEXT, STOP_NR TEXT, STOP_AREA_NR TEXT, "
        "STOP_AREA_POS_X TEXT, STOP_AREA_POS_Y TEXT)"
    )
    db.execute(
        "CREATE TABLE map_stop_point_2_stops "
        "(VERSION TEXT, STOP_ID TEXT, STOP_AREA_ID TEXT, STOPPING_POINT_NR TEXT, gtfs_id TEXT)"
    )


def add_stop(db, version, stop, lat, lon, final=None, mapped=True):
    """Add a source stopping point 1 in area 1 and map it to a final stop; shapes must not read gtfs_stops."""
    db.execute("INSERT INTO dino_stop_point VALUES (?, ?, '1', '1')", (version, stop))
    db.execute("INSERT INTO dino_stop_area VALUES (?, ?, '1', ?, ?)", (version, stop, lon, lat))
    if mapped:
        db.execute("INSERT INTO map_stop_point_2_stops VALUES (?, ?, '1', '1', ?)", (version, stop, final or f"S{stop}"))


def add_route(db, version, line, stops, direction="1", links=True):
    db.executemany("INSERT INTO dino_route VALUES (?, ?, 'V', ?, ?, ?, '1')", [
        (version, line, direction, str(index), stop) for index, stop in enumerate(stops, 1)
    ])
    if links:
        db.executemany("INSERT INTO dino_link VALUES (?, ?, ?, '1', '1', ?, '1', '1')", [
            (version, f"{origin}{destination}", origin, destination) for origin, destination in zip(stops, stops[1:])
        ])


def shape(db, shape_id):
    return db.execute(
        "SELECT shape_pt_lat, shape_pt_lon, shape_pt_sequence, shape_dist_traveled FROM gtfs_shapes "
        "WHERE shape_id = ? ORDER BY shape_pt_sequence", (shape_id,)
    ).fetchall()


def test_shape_follows_link_geometry_between_final_stops():
    with temporary_database() as db:
        create_tables(db)
        add_stop(db, "1", "A", 50.0, 8.0)
        add_stop(db, "1", "B", 50.0, 8.2)
        add_route(db, "1", "L", ["A", "B"], links=False)
        db.execute("INSERT INTO dino_link VALUES ('1', 'K1', 'A', '1', '1', 'B', '1', '1')")
        db.executemany("INSERT INTO dino_link_geometry VALUES ('1', 'K1', ?, ?, ?)", [
            ("2", "8.1", "50.1"), ("1", "8.0", "50.0"), ("3", "8.2", "50.0"),
        ])

        transform(db)

        points = shape(db, "shape:1:L:V:1")
        assert [point[:2] for point in points] == [(50.0, 8.0), (50.1, 8.1), (50.0, 8.2)]
        assert [point[2] for point in points] == [0, 1, 2]
        assert points[0][3] == 0 and 0 < points[1][3] < points[2][3]
        assert db.execute("SELECT * FROM map_route_2_shapes").fetchall() == [("1", "L", "V", "1", "shape:1:L:V:1")]


def test_missing_geometry_falls_back_to_stops_with_warning(caplog):
    with temporary_database() as db:
        create_tables(db)
        add_stop(db, "1", "A", 50.0, 8.0)
        add_stop(db, "1", "B", 50.0, 8.2)
        add_route(db, "1", "L", ["A", "B"])

        with caplog.at_level(logging.WARNING):
            transform(db)

        assert [point[:2] for point in shape(db, "shape:1:L:V:1")] == [(50.0, 8.0), (50.0, 8.2)]
        assert (
            "no link geometry between stopping points (VERSION, STOP_NR, STOP_AREA_NR, STOPPING_POINT_NR) "
            "= (1, A, 1, 1) and (1, B, 1, 1)"
        ) in caplog.text


def test_routes_with_same_final_stops_share_the_shape_of_the_highest_ranked_version():
    with temporary_database() as db:
        create_tables(db)
        db.executemany("INSERT INTO dino_version VALUES (?, ?)", [("1", "5"), ("2", "5"), ("3", "1")])
        for version in ("1", "2", "3"):
            add_stop(db, version, "A", 50.0, 8.0)
            add_stop(db, version, "B", 50.0, 8.2)
            add_route(db, version, "L", ["A", "B"])

        transform(db)

        assert db.execute("SELECT DISTINCT shape_id FROM gtfs_shapes").fetchall() == [("shape:2:L:V:1",)]
        assert db.execute("SELECT VERSION, gtfs_id FROM map_route_2_shapes ORDER BY VERSION").fetchall() == [
            ("1", "shape:2:L:V:1"), ("2", "shape:2:L:V:1"), ("3", "shape:2:L:V:1"),
        ]


def test_equal_priority_prefers_last_version_and_different_sequences_stay_separate():
    with temporary_database() as db:
        create_tables(db)
        for stop, lon in (("A", 8.0), ("B", 8.2), ("C", 8.4)):
            add_stop(db, "1", stop, 50.0, lon)
            add_stop(db, "2", stop, 50.0, lon)
        add_route(db, "1", "L", ["A", "B"])
        add_route(db, "2", "L", ["A", "B"])
        add_route(db, "2", "L", ["A", "B", "C"], direction="2")

        transform(db)

        assert {row[0] for row in db.execute("SELECT shape_id FROM gtfs_shapes")} == {
            "shape:2:L:V:1", "shape:2:L:V:2",
        }


def test_missing_link_falls_back_to_stops_with_warning(caplog):
    with temporary_database() as db:
        create_tables(db)
        add_stop(db, "1", "A", 50.0, 8.0)
        add_stop(db, "1", "B", 50.0, 8.2)
        add_route(db, "1", "L", ["A", "B"], links=False)

        with caplog.at_level(logging.WARNING):
            transform(db)

        assert [point[:2] for point in shape(db, "shape:1:L:V:1")] == [(50.0, 8.0), (50.0, 8.2)]
        assert (
            "no link in link.din between stopping points (VERSION, STOP_NR, STOP_AREA_NR, STOPPING_POINT_NR) "
            "= (1, A, 1, 1) and (1, B, 1, 1); using a straight line"
        ) in caplog.text


def test_versions_with_different_stop_numbers_are_deduplicated_by_final_stops():
    with temporary_database() as db:
        create_tables(db)
        add_stop(db, "1", "A", 50.0, 8.0, final="SA")
        add_stop(db, "1", "B", 50.0, 8.2, final="SB")
        add_stop(db, "2", "X", 50.0, 8.0, final="SA")
        add_stop(db, "2", "Y", 50.0, 8.2, final="SB")
        add_route(db, "1", "L", ["A", "B"])
        add_route(db, "2", "L", ["X", "Y"])

        transform(db)

        assert db.execute("SELECT DISTINCT shape_id FROM gtfs_shapes").fetchall() == [("shape:2:L:V:1",)]
        assert db.execute("SELECT VERSION, gtfs_id FROM map_route_2_shapes ORDER BY VERSION").fetchall() == [
            ("1", "shape:2:L:V:1"), ("2", "shape:2:L:V:1"),
        ]


def test_unmapped_stopping_point_fails():
    with temporary_database() as db:
        create_tables(db)
        add_stop(db, "1", "A", 50.0, 8.0)
        add_stop(db, "1", "B", 50.0, 8.2, mapped=False)
        add_route(db, "1", "L", ["A", "B"])

        with pytest.raises(InputError, match=r"route\.din row 1: stopping point .* = \(1, B, 1, 1\) has no final stop"):
            transform(db)


def test_unknown_stopping_point_fails():
    with temporary_database() as db:
        create_tables(db)
        add_stop(db, "1", "A", 50.0, 8.0)
        add_route(db, "1", "L", ["A", "X"])

        with pytest.raises(InputError, match=r"route\.din row 1: stopping point X:1 of VERSION '1' is missing"):
            transform(db)


def test_template_mode_is_required():
    with temporary_database() as db:
        create_tables(db)

        with pytest.raises(ConfigurationError, match="ids.shapes.mode must be template"):
            transform(db, {"ids": {"shapes": {"mode": "global"}}})
