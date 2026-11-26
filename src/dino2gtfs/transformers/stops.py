"""Transform DINO stations and stopping points into GTFS stops."""
import logging
import math
import sqlite3
from collections.abc import Mapping
from typing import Any

from pyproj import CRS, Transformer as CrsTransformer
from pyproj.exceptions import CRSError, ProjError

from dino2gtfs.domain.errors import CollisionError, ConfigurationError, InputError
from dino2gtfs.domain.id_template import IdTemplate
from dino2gtfs.transformers.base_transformer import (
    Row,
    SourceKey,
    TransformerBase,
    optional_source_value as _optional,
    required_source_value as _required,
    source_columns as _columns,
    source_key as _source_key,
    source_rows as _rows,
)

LOGGER = logging.getLogger(__name__)

# GTFS fields written by this transformer
GTFS_FIELDS = (
    "stop_id", "stop_name", "stop_code", "stop_lat", "stop_lon", "location_type", "parent_station", "stop_access"
)
# DINO columns identifying a station, stop area and stop point (source mapping keys)
STATION_KEY = ("VERSION", "STOP_NR")
AREA_KEY = ("VERSION", "STOP_NR", "STOP_AREA_NR")
POINT_KEY = ("VERSION", "STOP_NR", "STOP_AREA_NR", "STOPPING_POINT_NR")

# A source record: row, source key and row context for messages.
StopRecord = tuple[Row, SourceKey, str]
# A converted station: row, GTFS stop ID, row context and responsibility flag.
Station = tuple[Row, str, str, bool]
PointRecord = tuple[Row, SourceKey, str, Station]


def _coordinates(row: Mapping[str, object], x_name: str, y_name: str, context: str) -> tuple[float, float] | None:
    """Read a coordinate pair, returning None when it is missing or flagged as unknown (-1)."""
    values: list[float | None] = []
    for name in (x_name, y_name):
        value = row.get(name)
        if value is None or (isinstance(value, str) and not value.strip()):
            values.append(None)
            continue

        try:
            number = float(value)
        except (ValueError, TypeError) as exc:
            raise InputError(f"{context}: invalid coordinate {name}: {value!r}") from exc
        if not math.isfinite(number):
            raise InputError(f"{context}: invalid coordinate {name}: {value!r}")

        values.append(None if number == -1 else number)

    if values[0] is None or values[1] is None:
        return None

    return values[0], values[1]


class StopsTransformer(TransformerBase):
    """Create station and boarding-stop rows from DINO stop relations."""

    def initialize(self, database: sqlite3.Connection, settings: Mapping[str, Any]) -> None:
        database.execute("CREATE TABLE map_stop_2_stops (VERSION TEXT, STOP_ID TEXT, gtfs_id TEXT)")
        database.execute(
            "CREATE TABLE map_stop_point_2_stops "
            "(VERSION TEXT, STOP_ID TEXT, STOP_AREA_ID TEXT, STOPPING_POINT_NR TEXT, gtfs_id TEXT)"
        )

    def fingerprint_components(
        self, sources: Mapping[str, Mapping[str, object]], context: str
    ) -> tuple[str, ...]:
        if "stop_point" in sources:
            return ("platform", *_source_key(sources["stop_point"],
                    ("STOP_NR", "STOP_AREA_NR", "STOPPING_POINT_NR"), context))

        return ("station", *_source_key(sources["stop"], ("STOP_NR",), context))

    def canonical_value(
        self, sources: Mapping[str, Mapping[str, object]], context: str,
        components: tuple[str, ...] | None = None,
    ) -> str:
        if "stop_point" in sources:
            return ":".join(_source_key(sources["stop_point"], POINT_KEY, context))

        return ":".join(_source_key(sources["stop"], STATION_KEY, context))

    def transform(self, database: sqlite3.Connection, settings: Mapping[str, Any]) -> None:
        # Step 1: read and validate the configuration
        self._configure(database, settings)

        # Step 2: validate the DINO source tables
        stop_columns = _columns(database, "stop", STATION_KEY)
        point_columns = _columns(
            database, "stop_point", POINT_KEY, optional=True
        )
        area_columns = _columns(database, "stop_area", AREA_KEY, optional=True)
        if self._template is not None:
            self.validate_template_fields(
                self._template, {"stop": stop_columns, "stop_point": point_columns}, ("stop_kind",)
            )

        self._load_coordinate_systems(database, settings)
        self._existing_ids = {row[0] for row in database.execute("SELECT stop_id FROM gtfs_stops")}
        self._results: dict[str, tuple[Row, str]] = {}
        self._global_kinds: dict[str, tuple[str, str]] = {}

        # Step 3: merge and convert stations
        station_groups = self._group_records(database, "stop", STATION_KEY)
        station_by_source, station_mappings = self._convert_stations(station_groups)

        # Step 4: read stop areas of known stations, used as coordinate fallback
        areas = self._read_areas(database, station_by_source) if area_columns else {}

        # Step 5: merge and convert boarding stops
        point_groups: dict[str, list[PointRecord]] = {}
        point_mappings: list[tuple[SourceKey, str]] = []
        if point_columns:
            point_groups = self._group_points(database, station_by_source)
            point_mappings = self._convert_points(point_groups, areas)

        # Step 6: warn about global IDs shared by different merged stops
        if self._template is not None:
            self._warn_global_id_collisions(station_groups, point_groups)

        # Step 7: write stops and mapping tables
        self.insert_with_configuration(
            database, "stops", GTFS_FIELDS, (values for values, _ in self._results.values()),
            self._defaults, self._overrides,
        )
        database.executemany(
            "INSERT INTO map_stop_2_stops (VERSION, STOP_ID, gtfs_id) VALUES (?, ?, ?)",
            ((*key, identifier) for key, identifier in station_mappings),
        )
        database.executemany(
            "INSERT INTO map_stop_point_2_stops "
            "(VERSION, STOP_ID, STOP_AREA_ID, STOPPING_POINT_NR, gtfs_id) VALUES (?, ?, ?, ?, ?)",
            ((*key, identifier) for key, identifier in point_mappings),
        )

        LOGGER.info("transformed %d DINO stations and boarding stops", len(self._results))

    # -- configuration -------------------------------------------------------------------------

    def _configure(self, database: sqlite3.Connection, settings: Mapping[str, Any]) -> None:
        """Load defaults, overrides, ID mode and ID template."""
        self._defaults = self.configured_fields(database, settings, "stops", "defaults")
        self._overrides = self.configured_fields(database, settings, "stops", "overrides")
        for section, configured in (("defaults", self._defaults), ("overrides", self._overrides)):
            if "stop_name" in configured and (
                not isinstance(configured["stop_name"], str) or not configured["stop_name"].strip()
            ):
                raise ConfigurationError(f"{section}.stops.stop_name must be a non-empty string")

        ids = settings.get("ids")
        if not isinstance(ids, Mapping) or not isinstance(ids.get("stops"), Mapping):
            raise ConfigurationError("ids.stops must be a mapping with mode")

        self._mode = ids["stops"].get("mode")
        if self._mode not in ("global", "template"):
            raise ConfigurationError("ids.stops.mode must be global or template")

        self._template: IdTemplate | None = None
        if self._mode == "template":
            self._template = self.make_id_template(
                settings, "stops", {"stop": "stop.din", "stop_point": "stop_point.din"}
            )

    def _load_coordinate_systems(self, database: sqlite3.Connection, settings: Mapping[str, Any]) -> None:
        """Load the default CRS and the per-version coordinate system parameters."""
        input_settings = settings.get("input", {})
        if not isinstance(input_settings, Mapping):
            raise ConfigurationError("input must be a mapping")

        self._default_crs = input_settings.get("crs")
        if self._default_crs is not None and (
            isinstance(self._default_crs, bool) or not isinstance(self._default_crs, (str, int))
            or (isinstance(self._default_crs, str) and not self._default_crs.strip())
        ):
            raise ConfigurationError("input.crs must be a CRS identifier or null")

        self._coordsys: dict[str, tuple[object, float, float, float, float]] = {}
        self._crs_transformers: dict[object, CrsTransformer] = {}
        if not _columns(database, "coordsys", ("VERSION",), optional=True):
            return

        for rowid, row in _rows(database, "coordsys"):
            context = f"coordsys.din row {rowid}"
            version = _required(row, "VERSION", context)

            parameters: list[float] = []
            for field, default in (("TRANS_X", 0.0), ("TRANS_Y", 0.0), ("SCALE_X", 1.0), ("SCALE_Y", 1.0)):
                value = _optional(row, field)
                try:
                    number = float(value) if value is not None else default
                except ValueError as exc:
                    raise InputError(f"{context}: invalid {field}: {value!r}") from exc

                if not math.isfinite(number) or (field.startswith("SCALE_") and number == 0):
                    raise InputError(f"{context}: invalid {field}: {value!r}")

                parameters.append(number)

            metadata = (_optional(row, "EPSG_CODE"), *parameters)
            if version in self._coordsys and self._coordsys[version] != metadata:
                raise CollisionError(f"conflicting coordsys.din records for VERSION {version!r}")

            self._coordsys[version] = metadata

    # -- shared helpers ------------------------------------------------------------------------

    def _to_wgs84(
        self, version: str, xy: tuple[float, float] | None, context: str
    ) -> tuple[float, float] | tuple[None, None]:
        """Convert a source coordinate pair to WGS84 latitude and longitude."""
        if xy is None:
            return None, None

        crs, tx, ty, sx, sy = self._coordsys.get(version, (None, 0.0, 0.0, 1.0, 1.0))
        configured = crs is None
        crs = self._default_crs if configured else crs
        if crs is None:
            raise ConfigurationError(f"{context}: input.crs is required when coordsys.din has no EPSG_CODE")

        try:
            if crs not in self._crs_transformers:
                self._crs_transformers[crs] = CrsTransformer.from_crs(
                    CRS.from_user_input(crs), "EPSG:4326", always_xy=True
                )

            lon, lat = self._crs_transformers[crs].transform(xy[0] * sx + tx, xy[1] * sy + ty, errcheck=True)
        except (CRSError, ProjError) as exc:
            error = ConfigurationError if configured else InputError
            raise error(f"{context}: unable to transform coordinates from CRS {crs!r}: {exc}") from exc

        if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise InputError(f"{context}: transformed coordinates are outside WGS84 bounds")

        return lat, lon

    def _identifier(self, sources: Mapping[str, Mapping[str, object]], context: str) -> str:
        """Build the GTFS stop ID from the global ID or the ID template."""
        kind = "platform" if "stop_point" in sources else "station"
        if self._mode == "global":
            return _required(sources["stop_point" if kind == "platform" else "stop"], "GLOBAL_ID", context)

        assert self._template is not None
        return self.render_id(self._template, sources, context, {"stop_kind": kind})

    def _claim_global_kind(self, global_id: str, kind: str, context: str) -> None:
        """Ensure that a global ID does not represent both a station and a boarding stop."""
        previous = self._global_kinds.get(global_id)
        if previous is not None and previous[0] != kind:
            raise InputError(
                f"global ID {global_id!r} cannot represent both a station and a boarding stop "
                f"({previous[1]} and {context})"
            )

        self._global_kinds[global_id] = (kind, context)

    def _add_stop(self, identifier: str, values: Row, context: str) -> None:
        """Apply defaults and overrides, fill missing coordinates and register the stop."""
        values = self.apply_configuration(values, self._defaults, self._overrides)
        missing_coordinates = False
        for field in ("stop_lat", "stop_lon"):
            value = values[field]
            if field not in self._overrides and (value is None or (isinstance(value, str) and not value.strip())):
                values[field] = 0.0
                missing_coordinates = True

        if missing_coordinates:
            LOGGER.warning("%s: missing coordinates; using 0,0 for missing values", context)

        if identifier in self._existing_ids:
            raise CollisionError(f"identity collision in gtfs_stops: {identifier!r} (existing stop and {context})")

        if identifier in self._results:
            raise CollisionError(
                f"identity collision in gtfs_stops: {identifier!r} ({self._results[identifier][1]} and {context})"
            )

        self._results[identifier] = (values, context)

    def _group_records(
        self, database: sqlite3.Connection, table: str, key_fields: tuple[str, ...]
    ) -> dict[str, list[StopRecord]]:
        """Group source rows by STOP_NR (template mode) or GLOBAL_ID (global mode)."""
        groups: dict[str, list[StopRecord]] = {}
        for rowid, row in _rows(database, table):
            context = f"{table}.din row {rowid}"
            key = _source_key(row, key_fields, context)
            identity = _required(row, "GLOBAL_ID", context) if self._mode == "global" else key[1]
            groups.setdefault(identity, []).append((row, key, context))

        return groups

    # -- stations ------------------------------------------------------------------------------

    def _convert_stations(
        self, groups: dict[str, list[StopRecord]]
    ) -> tuple[dict[SourceKey, Station], list[tuple[SourceKey, str]]]:
        """Create one station per merged group, preferring the last responsible record."""
        by_source: dict[SourceKey, Station] = {}
        mappings: list[tuple[SourceKey, str]] = []
        for records in groups.values():
            row, key, context = next(
                (record for record in reversed(records) if record[0].get("IS_RESPONSIBLE_STOP") == "1"),
                records[-1],
            )
            identifier = self._identifier({"stop": row}, context)
            if self._mode == "global":
                self._claim_global_kind(identifier, "station", context)

            name = self.apply_configuration(
                {"stop_name": _optional(row, "STOP_NAME")}, self._defaults, self._overrides
            )["stop_name"]
            if not isinstance(name, str) or not name.strip():
                raise InputError(
                    f"{context}: STOP_NAME is required; configure defaults.stops.stop_name or "
                    "overrides.stops.stop_name"
                )

            lat, lon = self._to_wgs84(key[0], _coordinates(row, "STOP_POS_X", "STOP_POS_Y", context), context)
            self._add_stop(identifier, {
                "stop_id": identifier, "stop_name": name, "stop_code": _optional(row, "STOP_SHORTNAME"),
                "stop_lat": lat, "stop_lon": lon, "location_type": 1, "parent_station": None, "stop_access": None,
            }, context)

            station = (row, identifier, context, row.get("IS_RESPONSIBLE_STOP") == "1")
            for _, source_key, _ in records:
                previous = by_source.get(source_key)
                if previous is not None and previous[1] != identifier:
                    raise CollisionError(
                        f"conflicting map_stop_2_stops source identity {source_key!r} "
                        f"({previous[2]} and {context})"
                    )

                mappings.append((source_key, identifier))
                by_source[source_key] = station

        return by_source, mappings

    def _read_areas(
        self, database: sqlite3.Connection, stations: dict[SourceKey, Station]
    ) -> dict[SourceKey, tuple[Row, str]]:
        """Read stop areas that belong to a known station."""
        station_numbers = {key[1] for key in stations}
        areas: dict[SourceKey, tuple[Row, str]] = {}
        for rowid, row in _rows(database, "stop_area"):
            if _optional(row, "STOP_NR") not in station_numbers:
                continue

            context = f"stop_area.din row {rowid}"
            key = _source_key(row, AREA_KEY, context)
            if key in areas:
                raise CollisionError(f"duplicate stop_area.din identity {key!r}: {areas[key][1]} and {context}")

            areas[key] = (row, context)

        return areas

    # -- boarding stops ------------------------------------------------------------------------

    def _group_points(
        self, database: sqlite3.Connection, stations: dict[SourceKey, Station]
    ) -> dict[str, list[PointRecord]]:
        """Group stop points by STOP_NR or GLOBAL_ID and attach their parent station."""
        station_by_number: dict[str, Station] = {}
        for key, station in stations.items():
            station_by_number.setdefault(key[1], station)

        groups: dict[str, list[PointRecord]] = {}
        for rowid, row in _rows(database, "stop_point"):
            context = f"stop_point.din row {rowid}"
            key = _source_key(row, POINT_KEY, context)
            identity = _required(row, "GLOBAL_ID", context) if self._mode == "global" else key[1]

            station = stations.get((key[0], key[1]), station_by_number.get(key[1]))
            if station is None:
                raise InputError(f"{context}: parent station STOP_NR {key[1]!r} is missing from stop.din")

            if self._mode == "global":
                self._claim_global_kind(identity, "platform", context)

            groups.setdefault(identity, []).append((row, key, context, station))

        return groups

    def _convert_points(
        self, groups: dict[str, list[PointRecord]], areas: dict[SourceKey, tuple[Row, str]]
    ) -> list[tuple[SourceKey, str]]:
        """Create one boarding stop per merged group, preferring records of responsible stations."""
        mappings: list[tuple[SourceKey, str]] = []
        mapped: dict[SourceKey, tuple[str, str]] = {}
        for records in groups.values():
            row, key, context, station = next((record for record in reversed(records) if record[3][3]), records[-1])
            parent, parent_id, parent_context, _ = station
            if self._results[parent_id][0]["location_type"] != 1:
                raise ConfigurationError(f"{context}: parent station must have location_type 1")

            if "location_type" in self._overrides and self._overrides["location_type"] != 0:
                raise ConfigurationError(f"{context}: boarding stop must have location_type 0")

            identifier = self._identifier({"stop": parent, "stop_point": row}, context)

            # Coordinates fall back from stop point to stop area to station
            xy = _coordinates(row, "STOPPING_POINT_POS_X", "STOPPING_POINT_POS_Y", context)
            xy_version = key[0]
            area = areas.get(key[:3])
            if xy is None and area is not None:
                xy = _coordinates(area[0], "STOP_AREA_POS_X", "STOP_AREA_POS_Y", area[1])
            if xy is None:
                xy = _coordinates(parent, "STOP_POS_X", "STOP_POS_Y", parent_context)
                xy_version = _required(parent, "VERSION", parent_context)

            lat, lon = self._to_wgs84(xy_version, xy, context)

            self._add_stop(identifier, {
                "stop_id": identifier, "stop_name": self._results[parent_id][0]["stop_name"],
                "stop_code": _optional(row, "STOPPING_POINT_SHORTNAME"), "stop_lat": lat, "stop_lon": lon,
                "location_type": 0, "parent_station": parent_id, "stop_access": self._stop_access(row, context),
            }, context)

            for _, source_key, _, _ in records:
                previous = mapped.get(source_key)
                if previous is not None and previous[0] != identifier:
                    raise CollisionError(
                        f"conflicting map_stop_point_2_stops source identity {source_key!r} "
                        f"({previous[1]} and {context})"
                    )

                mapped[source_key] = (identifier, context)
                mappings.append((source_key, identifier))

        return mappings

    @staticmethod
    def _stop_access(row: Row, context: str) -> int | None:
        """Map STREET_ACCESS to a boolean-like GTFS value."""
        access = _optional(row, "STREET_ACCESS")
        if access is None:
            return None

        try:
            number = int(access)
        except ValueError as exc:
            raise InputError(f"{context}: invalid STREET_ACCESS: {access!r}") from exc
        if number < 0:
            raise InputError(f"{context}: invalid STREET_ACCESS: {access!r}")

        return int(number != 0)

    # -- diagnostics ---------------------------------------------------------------------------

    @staticmethod
    def _warn_global_id_collisions(
        station_groups: dict[str, list[StopRecord]], point_groups: dict[str, list[PointRecord]]
    ) -> None:
        """Warn when one global ID occurs in different merged stations or stops."""
        seen: dict[str, tuple[tuple[str, str], str]] = {}
        for kind, groups in (("station", station_groups), ("platform", point_groups)):
            for identity, records in groups.items():
                for record in records:
                    global_id = _optional(record[0], "GLOBAL_ID")
                    if global_id is None:
                        continue

                    previous = seen.get(global_id)
                    if previous is not None and previous[0] != (kind, identity):
                        LOGGER.warning(
                            f"global ID collision in gtfs_stops: {global_id!r} ({previous[1]} and {record[2]})"
                        )

                    seen.setdefault(global_id, ((kind, identity), record[2]))
