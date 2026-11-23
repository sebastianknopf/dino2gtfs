import csv
import io
import json
import logging
import sqlite3
from contextlib import closing
from zipfile import ZipFile

import pytest

from dino2gtfs.domain.errors import ConfigurationError, ExportError
from dino2gtfs.gtfs.exporter import GtfsExporter
from dino2gtfs.infrastructure.sqlite import temporary_database


def _csv(archive, name):
    return list(csv.DictReader(io.StringIO(archive.read(name).decode("utf-8"), newline="")))


def test_exports_all_tables_and_streams_gtfs_values(tmp_path, caplog):
    destination = tmp_path / "feed.zip"
    with temporary_database() as db:
        db.execute("INSERT INTO gtfs_stops (stop_id, stop_name, stop_lat) VALUES (?, ?, ?)",
                   ("001", 'München, "Hbf"', 0))
        db.execute("INSERT INTO gtfs_locations VALUES (?, ?, ?, ?)", (
            "zone", "Feature", '{"stop_name":"München"}',
            '{"type":"Polygon","coordinates":[[[0,0],[1,0],[1,1],[0,0]]]}',
        ))
        db.execute("CREATE TABLE gtfs_custom (value TEXT)")
        db.execute("INSERT INTO gtfs_custom VALUES ('custom')")
        db.execute("CREATE TABLE dino_stop (value TEXT)")
        expected = {
            name[5:] + (".geojson" if name == "gtfs_locations" else ".txt")
            for name, in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name GLOB 'gtfs_*'")
        }
        with caplog.at_level(logging.INFO, logger="dino2gtfs.gtfs.export"):
            GtfsExporter().export_archive(db, destination, {})

    with ZipFile(destination) as archive:
        assert set(archive.namelist()) == expected
        assert archive.read("agency.txt").startswith(b"agency_id,agency_name,")
        stop, = _csv(archive, "stops.txt")
        assert stop["stop_id"] == "001"
        assert stop["stop_name"] == 'München, "Hbf"'
        assert stop["stop_lat"] == "0.0"
        assert stop["stop_desc"] == ""
        assert _csv(archive, "custom.txt") == [{"value": "custom"}]
        locations = json.loads(archive.read("locations.geojson"))
        assert locations == {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature", "id": "zone", "properties": {"stop_name": "München"},
                "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
            }],
        }
    assert "starting GTFS export" in caplog.text
    assert "writing file stops.txt" in caplog.text
    assert "completed export of GTFS feed" in caplog.text
    assert "empty GTFS tables are included" in caplog.text


def test_conditional_headers_and_conflicting_network_data(tmp_path, caplog):
    destination = tmp_path / "feed.zip"
    with temporary_database() as db:
        db.execute("INSERT INTO gtfs_routes (route_id, route_type) VALUES ('r', 3)")
        db.execute("INSERT INTO gtfs_fare_leg_rules (fare_product_id) VALUES ('fare')")
        GtfsExporter().export_archive(db, destination, {})
        with ZipFile(destination) as archive:
            assert "network_id" not in _csv(archive, "routes.txt")[0]
            assert "rule_priority" not in _csv(archive, "fare_leg_rules.txt")[0]
        db.execute("UPDATE gtfs_routes SET network_id = 'network'")
        db.execute("UPDATE gtfs_fare_leg_rules SET rule_priority = 0")
        with caplog.at_level(logging.WARNING, logger="dino2gtfs.gtfs.export"):
            GtfsExporter().export_archive(db, destination, {})
        with ZipFile(destination) as archive:
            assert _csv(archive, "routes.txt")[0]["network_id"] == "network"
            assert _csv(archive, "fare_leg_rules.txt")[0]["rule_priority"] == "0"
        assert "violates GTFS file conditions" in caplog.text


@pytest.mark.parametrize("settings, enabled", [
    ({}, True),
    ({"features": {"notices": True}}, True),
    ({"features": {"notices": False}}, False),
])
def test_notices_feature_filters_files_and_translations(tmp_path, settings, enabled):
    destination = tmp_path / "feed.zip"
    with temporary_database() as db:
        db.execute("INSERT INTO gtfs_notices VALUES ('n', 'Notice')")
        db.execute("INSERT INTO gtfs_trip_segments VALUES ('s', 't', 1, 2)")
        db.execute("INSERT INTO gtfs_notice_assignments VALUES ('n', 'trip_segments', 's')")
        db.execute("INSERT INTO gtfs_translations (table_name, field_name, language, translation, record_id) "
                   "VALUES ('notices', 'display_text', 'de', 'Hinweis', 'n'), "
                   "('stops', 'stop_name', 'de', 'Haltestelle', 's')")
        GtfsExporter().export_archive(db, destination, settings)

    with ZipFile(destination) as archive:
        notice_files = {"notices.txt", "notice_assignments.txt", "trip_segments.txt"}
        assert (notice_files <= set(archive.namelist())) if enabled else notice_files.isdisjoint(archive.namelist())
        translations = _csv(archive, "translations.txt")
        assert {row["table_name"] for row in translations} == ({"notices", "stops"} if enabled else {"stops"})
        if enabled:
            assert _csv(archive, "notices.txt") == [{"notice_id": "n", "display_text": "Notice"}]


@pytest.mark.parametrize("settings", [
    {"features": None},
    {"features": []},
    {"features": {"notices": "false"}},
    {"features": {"notices": 0}},
])
def test_invalid_features_fail_without_replacing_output(tmp_path, settings):
    destination = tmp_path / "feed.zip"
    destination.write_bytes(b"existing")
    with temporary_database() as db:
        with pytest.raises(ConfigurationError, match="features"):
            GtfsExporter().export_archive(db, destination, settings)
    assert destination.read_bytes() == b"existing"


@pytest.mark.parametrize("value", ["line\nbreak", "tab\tcharacter", b"binary"])
def test_invalid_csv_values_do_not_replace_output(tmp_path, value):
    destination = tmp_path / "feed.zip"
    destination.write_bytes(b"existing")
    with temporary_database() as db:
        db.execute("INSERT INTO gtfs_stops (stop_id, stop_name) VALUES ('s', ?)", (value,))
        with pytest.raises(ExportError):
            GtfsExporter().export_archive(db, destination, {})
    assert destination.read_bytes() == b"existing"
    assert {path.name for path in tmp_path.iterdir()} == {"feed.zip"}


def test_invalid_geojson_does_not_replace_output(tmp_path):
    destination = tmp_path / "feed.zip"
    destination.write_bytes(b"existing")
    with temporary_database() as db:
        db.execute("INSERT INTO gtfs_locations VALUES ('bad', 'Feature', '{}', 'not json')")
        with pytest.raises(ExportError, match="Invalid GeoJSON location"):
            GtfsExporter().export_archive(db, destination, {})
    assert destination.read_bytes() == b"existing"
    assert {path.name for path in tmp_path.iterdir()} == {"feed.zip"}


def test_no_tables_and_unsafe_names_are_rejected(tmp_path):
    destination = tmp_path / "feed.zip"
    with closing(sqlite3.connect(":memory:")) as db:
        with pytest.raises(ExportError, match="no GTFS tables"):
            GtfsExporter().export_archive(db, destination, {})
        db.execute('CREATE TABLE "gtfs_../escape" (x TEXT)')
        with pytest.raises(ExportError, match="Invalid GTFS table name"):
            GtfsExporter().export_archive(db, destination, {})
    assert not destination.exists()
