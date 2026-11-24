"""Stream GTFS tables from SQLite into a ZIP archive."""
import csv
import io
import json
import logging
import os
import re
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from dino2gtfs.domain.errors import ConfigurationError, ExportError

LOGGER = logging.getLogger(__name__)


class GtfsExporter:
    """Export each enabled GTFS table into a GTFS compliant ZIP archive."""

    FEATURE_TABLES = {"notices": frozenset({"notices", "notice_assignments", "trip_segments"})}
    TABLE_NAME = re.compile(r"gtfs_[a-z0-9_]+\Z")

    def _enabled_features(self, settings: Mapping[str, Any]) -> dict[str, bool]:
        features = settings.get("features", {})
        if not isinstance(features, Mapping):
            raise ConfigurationError("features must be a mapping")

        enabled = {}
        for name in self.FEATURE_TABLES:
            value = features.get(name, True)
            if not isinstance(value, bool):
                raise ConfigurationError(f"features.{name} must be a boolean")

            enabled[name] = value

        return enabled

    def _write_csv(
        self, archive: ZipFile, database: sqlite3.Connection, table: str, notices: bool, network_files: bool
    ) -> None:
        columns = [row[1] for row in database.execute(f'PRAGMA table_info("{table}")')]
        
        conditional_column = None
        if table == "gtfs_routes" and network_files and "network_id" in columns:
            conditional_column = "network_id"
        elif table == "gtfs_fare_leg_rules" and "rule_priority" in columns:
            conditional_column = "rule_priority"

        if conditional_column is not None:
            has_values = database.execute(
                f'SELECT 1 FROM "{table}" WHERE "{conditional_column}" IS NOT NULL '
                f'AND "{conditional_column}" != \'\' LIMIT 1'
            ).fetchone()

            if not has_values:
                columns.remove(conditional_column)
            elif table == "gtfs_routes":
                LOGGER.warning("Exporting routes.network_id together with networks files violates GTFS file conditions")

        if not columns or any(any(char in name for char in "\t\r\n") for name in columns):
            raise ExportError(f"Invalid GTFS column names in {table}")

        selected = ", ".join('"' + name.replace('"', '""') + '"' for name in columns)
        query = f'SELECT {selected} FROM "{table}"'
        if table == "gtfs_translations" and not notices:
            query += ' WHERE "table_name" != ?'
            cursor = database.execute(query, ("notices",))
        else:
            cursor = database.execute(query)

        filename = table[5:] + ".txt"
        LOGGER.info("writing file %s", filename)
        with archive.open(filename, "w", force_zip64=True) as binary:
            with io.TextIOWrapper(binary, encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream, lineterminator="\n")
                writer.writerow(column[0] for column in cursor.description)
                for row in cursor:
                    for value in row:
                        if isinstance(value, bytes):
                            raise ExportError(f"Binary value in {table} cannot be exported as GTFS text")
                        if isinstance(value, str) and any(char in value for char in "\t\r\n"):
                            raise ExportError(f"Invalid tab or newline in {table}")

                    writer.writerow(row)

    def _write_locations(self, archive: ZipFile, database: sqlite3.Connection) -> None:
        LOGGER.info("writing file locations.geojson")
        
        cursor = database.execute('SELECT id, type, properties, geometry FROM "gtfs_locations"')
        with archive.open("locations.geojson", "w", force_zip64=True) as binary:
            with io.TextIOWrapper(binary, encoding="utf-8", newline="") as stream:
                stream.write('{"type":"FeatureCollection","features":[')
                first = True
                for location_id, feature_type, properties_json, geometry_json in cursor:
                    try:
                        properties = json.loads(properties_json)
                        geometry = json.loads(geometry_json)
                        if (not isinstance(location_id, str) or not location_id
                                or feature_type != "Feature" or not isinstance(properties, dict)
                                or not isinstance(geometry, dict)
                                or geometry.get("type") not in ("Polygon", "MultiPolygon")
                                or not isinstance(geometry.get("coordinates"), list)):
                            raise ValueError("invalid GeoJSON feature")

                        feature = json.dumps(
                            {"type": feature_type, "id": location_id, "properties": properties, "geometry": geometry},
                            ensure_ascii=False, allow_nan=False, separators=(",", ":"),
                        )
                    except (TypeError, ValueError) as exc:
                        raise ExportError(f"Invalid GeoJSON location {location_id!r}: {exc}") from exc
                    if not first:
                        stream.write(",")

                    stream.write(feature)
                    first = False

                stream.write("]}")

    def export_archive(self, database: sqlite3.Connection, destination: Path, settings: Mapping[str, Any]) -> None:
        """Export all enabled GTFS tables, replacing the destination only on success."""
        enabled = self._enabled_features(settings)
        disabled_tables = set().union(
            *(tables for feature, tables in self.FEATURE_TABLES.items() if not enabled[feature])
        )
        
        LOGGER.info("starting GTFS export to %s", destination)
        LOGGER.debug("enabled features: %s", enabled)

        temporary_path = None
        try:
            tables = [row[0] for row in database.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name GLOB 'gtfs_*' ORDER BY name"
            )]

            if not tables:
                raise ExportError("Database contains no GTFS tables")

            for table in tables:
                if not self.TABLE_NAME.fullmatch(table):
                    raise ExportError(f"Invalid GTFS table name: {table}")

            selected_tables = [table for table in tables if table[5:] not in disabled_tables]
            if any(database.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone() is None for table in selected_tables):
                LOGGER.warning("empty GTFS tables are included; file-level GTFS conditions are not validated")

            network_files = bool({"gtfs_networks", "gtfs_route_networks"} & set(selected_tables))

            with NamedTemporaryFile(prefix=".gtfs-export-", dir=destination.parent, delete=False) as temporary:
                temporary_path = Path(temporary.name)

            with ZipFile(temporary_path, "w", compression=ZIP_DEFLATED, allowZip64=True) as archive:
                for table in tables:
                    if table[5:] in disabled_tables:
                        LOGGER.debug("skipping disabled feature table %s", table)
                    elif table == "gtfs_locations":
                        self._write_locations(archive, database)
                    else:
                        self._write_csv(archive, database, table, enabled["notices"], network_files)

            os.replace(temporary_path, destination)
        except (OSError, sqlite3.Error, UnicodeError) as exc:
            raise ExportError(f"Unable to export GTFS archive {destination}: {exc}") from exc
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

        LOGGER.info("completed export of GTFS feed")
