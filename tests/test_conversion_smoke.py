from zipfile import ZipFile
from contextlib import closing
import sqlite3
from dino2gtfs.cli import main


def add_calendar(archive):
    archive.writestr("version.din", "VERSION;PERIOD_DATE_FROM;PERIOD_DATE_TO\n1;20260105;20260111\n")
    archive.writestr("day_type_calendar.din", "VERSION;DAY;DAY_TYPE_NR\n1;20260105;1\n1;20260106;1\n")
    archive.writestr("day_type_2_day_attribute.din", "VERSION;DAY_TYPE_NR;DAY_ATTRIBUTE_NR\n1;1;1\n")
    archive.writestr("day_attribute.din", "VERSION;DAY_ATTRIBUTE_NR;STR_DAY_ATTRIBUTE\n1;1;\n")


CALENDAR_IDS = (
    "  calendar:\n    mode: template\n    template: 'service:{gtfs.canonical}'\n"
    "  shapes:\n    mode: template\n    template: 'shape:{gtfs.canonical}'\n"
)


def add_route_stops(archive):
    archive.writestr("route.din", "VERSION;LINE_NR;STR_LINE_VAR;LINE_DIR_NR;LINE_CONSEC_NR;STOP_NR;STOPPING_POINT_NR\n")


def global_config(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "defaults:\n  agency:\n    agency_url: https://example.org\n    agency_timezone: Europe/Berlin\n"
        "  routes:\n    route_type: 3\n"
        "mapping:\n  mode_of_transport:\n    3: 0\n"
        "ids:\n  agency:\n    mode: template\n    template: 'agency:{operator.OP_CODE}'\n"
        "  stops:\n    mode: global\n"
        "  routes:\n    mode: global\n" + CALENDAR_IDS
    )
    return config


def test_end_to_end_exports_agency(tmp_path):
    source = tmp_path / "input.zip"
    destination = tmp_path / "output.zip"
    config = global_config(tmp_path)
    with ZipFile(source, "w") as archive:
        add_calendar(archive)
        add_route_stops(archive)
        archive.writestr("stop.din", "VERSION;STOP_NR;STOP_NAME;GLOBAL_ID;IS_RESPONSIBLE_STOP\n"
                                    "1;1;Test;S1;1\n1;2;External;S2;0\n")
        archive.writestr("operator.din", "VERSION;OP_CODE;OP_LONG_NAME\n1;1;Operator\n")
        archive.writestr("line.din", "VERSION;LINE_NR;STR_LINE_VAR;LINE_DIR_NR;OP_CODE;LINE_NAME;MOT_NR;GLOBAL_ID\n"
                                    "1;42;a;1;1;42;10;R\n")
        archive.writestr("means_of_transport_desc.din", "VERSION;MOT_NR;TMOT_NR\n1;10;3\n")
    assert main(["convert", "--profile", "dino212_default", "--config", str(config), "--input", str(source), "--output", str(destination)]) == 0
    with ZipFile(destination) as archive:
        assert b"agency:1,Operator,https://example.org,Europe/Berlin" in archive.read("agency.txt")
        stops = archive.read("stops.txt").splitlines()
        assert len(stops) == 3
        assert stops[1].startswith(b"S1,")
        assert stops[2].startswith(b"S2,")
        assert b"R,agency:1,42,,,0," in archive.read("routes.txt")

def test_default_conversion_retains_import_for_debugging(tmp_path):
    source = tmp_path / "input.zip"
    destination = tmp_path / "output.zip"
    config = tmp_path / "config.yaml"
    database = tmp_path / "debug.db3"
    config.write_text(
        "input:\n  encoding: utf-8\n"
        "defaults:\n  agency:\n    agency_url: https://example.org\n    agency_timezone: UTC\n"
        "    agency_name: Fallback agency\n"
        "  routes:\n    route_type: 3\n    route_short_name: Fallback route\n"
        "  stops:\n    stop_name: Fallback stop\n"
        "overrides:\n  agency:\n    agency_timezone: Europe/Berlin\n"
        "  stops:\n    stop_desc: Configured description\n"
        "  routes:\n    route_long_name: Configured long name\n"
        "ids:\n  agency:\n    mode: template\n    template: 'operator-{operator.OP_CODE}'\n"
        "  stops:\n    mode: template\n    template: 'stop:{gtfs.canonical}'\n"
        "  routes:\n    mode: template\n    template: 'route-{line.LINE_NR}-{gtfs.route_type}'\n" + CALENDAR_IDS
    )
    with ZipFile(source, "w") as archive:
        add_calendar(archive)
        add_route_stops(archive)
        archive.writestr("stop.din", "VERSION;STOP_NR;STOP_NAME;IS_RESPONSIBLE_STOP\n1;1;Test;1\n")
        archive.writestr("operator.din", "VERSION;OP_CODE;OP_LONG_NAME\n1;1;Operator\n")
        archive.writestr("line.din", "VERSION;LINE_NR;STR_LINE_VAR;LINE_DIR_NR;OP_CODE;LINE_NAME;BRANCH_NR\n1;42;a;1;1;42;1\n")
    assert main(["convert", "--profile", "dino212_default", "--config", str(config),
                 "--input", str(source), "--output", str(destination), "--database", str(database)]) == 0
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT STOP_NR, STOP_NAME FROM dino_stop").fetchall() == [("1", "Test")]
        assert connection.execute("SELECT stop_id, stop_name FROM gtfs_stops").fetchall() == [
            ("stop:1:1", "Test")
        ]
        assert connection.execute("SELECT agency_id FROM gtfs_agency").fetchall() == [("operator-1",)]
        assert connection.execute("SELECT agency_name, agency_timezone FROM gtfs_agency").fetchall() == [
            ("Operator", "Europe/Berlin")
        ]
        assert connection.execute("SELECT stop_desc FROM gtfs_stops").fetchall() == [("Configured description",)]
        assert connection.execute("SELECT * FROM map_operator_2_agency").fetchall() == [("1", "1", "operator-1")]
        assert connection.execute("SELECT * FROM map_stop_2_stops").fetchall() == [("1", "1", "stop:1:1")]
        assert connection.execute("SELECT * FROM map_stop_point_2_stops").fetchall() == []
        assert connection.execute("SELECT route_id, agency_id, route_type FROM gtfs_routes").fetchall() == [
            ("route-42-3", "operator-1", 3)
        ]
        assert connection.execute("SELECT * FROM map_line_2_routes").fetchall() == [
            ("1", "42", "a", "1", "route-42-3")
        ]
        assert connection.execute("SELECT route_short_name, route_long_name FROM gtfs_routes").fetchall() == [
            ("42", "Configured long name")
        ]
    assert destination.exists()


def test_end_to_end_exports_fingerprint_agency_id(tmp_path):
    source = tmp_path / "input.zip"
    destination = tmp_path / "output.zip"
    config = tmp_path / "config.yaml"
    config.write_text(
        "defaults:\n  agency:\n    agency_url: https://example.org\n    agency_timezone: UTC\n"
        "  routes:\n    route_type: 3\n"
        "ids:\n  agency:\n    mode: template\n    template: 'agency:{gtfs.fingerprint}'\n"
        "  stops:\n    mode: global\n"
        "  routes:\n    mode: template\n    template: 'route:{gtfs.fingerprint}'\n" + CALENDAR_IDS
    )
    with ZipFile(source, "w") as archive:
        add_calendar(archive)
        add_route_stops(archive)
        archive.writestr("operator.din", "VERSION;OP_CODE;OP_LONG_NAME\n1;A;Operator\n")
        archive.writestr("stop.din", "VERSION;STOP_NR;STOP_NAME;GLOBAL_ID;IS_RESPONSIBLE_STOP\n1;1;Test;S1;1\n")
        archive.writestr("line.din", "VERSION;LINE_NR;STR_LINE_VAR;LINE_DIR_NR;OP_CODE;LINE_NAME;BRANCH_NR\n1;42;a;1;A;42;1\n")
    assert main(["convert", "--profile", "dino212_default", "--config", str(config),
                 "--input", str(source), "--output", str(destination)]) == 0
    with ZipFile(destination) as archive:
        assert b"agency:ead76f8e70b5dd3b1a07a92c25c425b2b27198728862103d65c31c621e52a6aa,Operator" in archive.read("agency.txt")


def test_missing_global_stop_id_fails_and_rolls_back_conversion(tmp_path):
    source = tmp_path / "input.zip"
    destination = tmp_path / "output.zip"
    database = tmp_path / "debug.db3"
    config = global_config(tmp_path)
    with ZipFile(source, "w") as archive:
        archive.writestr("operator.din", "OP_CODE;OP_LONG_NAME\nA;Operator\n")
        archive.writestr("stop.din", "VERSION;STOP_NR;STOP_NAME;GLOBAL_ID;IS_RESPONSIBLE_STOP\n1;1;Test;;1\n")
    assert main(["convert", "--profile", "dino212_default", "--config", str(config),
                 "--input", str(source), "--output", str(destination), "--database", str(database)]) == 3
    assert not destination.exists()
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT count(*) FROM gtfs_agency").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM gtfs_stops").fetchone() == (0,)


def test_missing_route_agency_rolls_back_all_transformations(tmp_path):
    source = tmp_path / "input.zip"
    destination = tmp_path / "output.zip"
    database = tmp_path / "debug.db3"
    config = global_config(tmp_path)
    with ZipFile(source, "w") as archive:
        archive.writestr("operator.din", "VERSION;OP_CODE;OP_LONG_NAME\n1;A;Operator\n")
        archive.writestr("stop.din", "VERSION;STOP_NR;STOP_NAME;GLOBAL_ID;IS_RESPONSIBLE_STOP\n1;1;Test;S1;1\n")
        archive.writestr("line.din", "VERSION;LINE_NR;STR_LINE_VAR;LINE_DIR_NR;OP_CODE;LINE_NAME;GLOBAL_ID\n"
                                    "1;42;a;1;B;42;R\n")
    assert main(["convert", "--profile", "dino212_default", "--config", str(config),
                 "--input", str(source), "--output", str(destination), "--database", str(database)]) == 3
    assert not destination.exists()
    with closing(sqlite3.connect(database)) as connection:
        for table in ("gtfs_agency", "gtfs_stops", "gtfs_routes"):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone() == (0,)
