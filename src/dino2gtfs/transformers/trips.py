"""Transform DINO trips and their timing patterns into GTFS trips and stop times."""
import logging
import re
import sqlite3
from collections.abc import Mapping
from typing import Any

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
TRIP_FIELDS = ("trip_id", "route_id", "service_id", "trip_headsign", "trip_short_name", "direction_id", "shape_id", "bikes_allowed")
STOP_TIME_FIELDS = (
    "trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence", "stop_headsign",
    "pickup_type", "drop_off_type", "shape_dist_traveled",
)
# GTFS bikes_allowed by trip.din BIKE_ALLOWED; any other value is unknown (0)
BIKES_ALLOWED = {"0": 2, "1": 1}
# DINO columns identifying a route (one direction of a line variant) and a trip (source mapping keys)
LINE_KEY = ("VERSION", "LINE_NR", "STR_LINE_VAR", "LINE_DIR_NR")
TRIP_KEY = (*LINE_KEY, "TRIP_ID")
# DINO stopping point types without a stop time row: pass-through and operational stop
OMITTED_TYPES = (-1, 5)
# GTFS (pickup_type, drop_off_type) by DINO stopping point type; 3 is MUST_ARRANGE_WITH_DRIVER.
# Every other type (0, 4, unknown or blank) is a regular stop.
PICKUP_DROP_OFF = {1: (3, 3), 2: (1, 0), 3: (0, 1)}
# Column of vehicle_destination_text.din used as headsign
HEADSIGN_COLUMN = "VDT_TEXT_FRONT1"
# Placeholders allowed in the configured trip_headsign, e.g. {vehicle_destination_text.VDT_TEXT_INTERIOR1}
PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
DESTINATION_FIELD = re.compile(r"vehicle_destination_text\.([A-Za-z0-9_]+)")
# Whitespace and control characters, which GTFS values must not contain (tabs and newlines break the export)
UNSAFE_CHARACTERS = re.compile(r"[\s\x00-\x1f\x7f]+")

# A route stop: LINE_CONSEC_NR, STOP_NR, STOPPING_POINT_NR, STOPPING_POINT_TYPE and the distance in meters
# travelled from the first stop of the route.
RouteStop = tuple[int, str, str, int, float]
# A timing pattern entry: travel time from the previous stop and dwell time in seconds.
Timing = tuple[int, int]


def _integer(value: str, name: str, context: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise InputError(f"{context}: invalid {name}: {value!r}") from exc


def _time(seconds: int) -> str:
    """Format seconds after midnight as GTFS time; hours may exceed 24 for trips running over midnight."""
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


class TripsTransformer(TransformerBase):
    """Create trips and their stop times from DINO trips, routes and timing patterns."""

    def initialize(self, database: sqlite3.Connection, settings: Mapping[str, Any]) -> None:
        database.execute(
            "CREATE TABLE map_trip_2_trips "
            "(VERSION TEXT, LINE_NR TEXT, STR_LINE_VAR TEXT, LINE_DIR_NR TEXT, TRIP_ID TEXT, "
            "DAY_ATTRIBUTE_NR TEXT, RESTRICTION TEXT, gtfs_id TEXT)"
        )

    def fingerprint_components(
        self, sources: Mapping[str, Mapping[str, object]], context: str
    ) -> tuple[str, ...]:
        # The same DINO trip may run on several day attributes and restrictions
        trip = sources["trip"]
        restriction = _optional(trip, "RESTRICTION")
        return (
            *_source_key(trip, (*TRIP_KEY, "DAY_ATTRIBUTE_NR"), context),
            *((restriction,) if restriction else ()),
        )

    def canonical_value(
        self, sources: Mapping[str, Mapping[str, object]], context: str,
        components: tuple[str, ...] | None = None,
    ) -> str:
        return ":".join(components or self.fingerprint_components(sources, context))

    def transform(self, database: sqlite3.Connection, settings: Mapping[str, Any]) -> None:
        # Step 1: read and validate the configuration
        self._configure(database, settings)

        # Step 2: validate the DINO source tables
        trip_columns = _columns(database, "trip", (*TRIP_KEY, "TIMING_GROUP_NR", "DEPARTURE_TIME", "DAY_ATTRIBUTE_NR"))
        _columns(database, "route", (*LINE_KEY, "LINE_CONSEC_NR", "STOP_NR", "STOPPING_POINT_NR", "LENGTH"))
        _columns(database, "timing_pattern", (*LINE_KEY, "LINE_CONSEC_NR", "TIMING_GROUP_NR", "TT_REL"))
        if self._template is not None:
            self.validate_template_fields(self._template, {"trip": trip_columns})

        # Step 3: read the resolved route, service, shape and stop IDs from the mapping tables
        self._read_mappings(database)

        # Step 4: read route stops, timing patterns and trip specific dwell times
        self._read_route_stops(database)
        self._read_timings(database)
        self._read_trip_dwell_times(database)

        # Step 5: read the destination texts of trips and stops
        self._read_headsigns(database)

        # Step 6: convert the trips and derive their stop times
        existing_ids = {row[0] for row in database.execute("SELECT trip_id FROM gtfs_trips")}
        trips: list[Row] = []
        stop_times: list[Row] = []
        mappings: list[tuple[tuple[str | None, ...], str]] = []
        contexts: dict[str, str] = {}
        for rowid, row in _rows(database, "trip"):
            context = f"trip.din row {rowid}"
            key = _source_key(row, TRIP_KEY, context)
            identifier = self._identifier(row, context)
            if identifier in existing_ids:
                raise CollisionError(f"Identity collision in gtfs_trips: {identifier!r} (existing trip and {context})")

            if identifier in contexts:
                raise CollisionError(
                    f"Identity collision in gtfs_trips: {identifier!r} ({contexts[identifier]} and {context})"
                )

            contexts[identifier] = context
            converted = self._convert_trip(row, key, identifier, context)
            if converted is not None:
                trips.append(converted[0])
                stop_times.extend(converted[1])
                mappings.append(((*key, row["DAY_ATTRIBUTE_NR"], _optional(row, "RESTRICTION")), identifier))

        # Step 7: write trips, stop times and the trip mapping table
        self.insert_with_configuration(database, "trips", TRIP_FIELDS, trips, self._defaults, self._overrides)
        self.insert_with_configuration(
            database, "stop_times", STOP_TIME_FIELDS, stop_times,
            self._stop_time_defaults, self._stop_time_overrides,
        )
        database.executemany(
            "INSERT INTO map_trip_2_trips "
            "(VERSION, LINE_NR, STR_LINE_VAR, LINE_DIR_NR, TRIP_ID, DAY_ATTRIBUTE_NR, RESTRICTION, gtfs_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ((*key, identifier) for key, identifier in mappings),
        )

        LOGGER.info(
            "transformed %d DINO trips into %d GTFS trips with %d stop times",
            len(contexts), len(trips), len(stop_times),
        )

    # -- configuration -------------------------------------------------------------------------

    def _configure(self, database: sqlite3.Connection, settings: Mapping[str, Any]) -> None:
        """Load defaults, overrides, ID mode and ID template."""
        self._defaults = self.configured_fields(database, settings, "trips", "defaults")
        self._overrides = self.configured_fields(database, settings, "trips", "overrides")
        self._stop_time_defaults = self.configured_fields(database, settings, "stop_times", "defaults")
        self._stop_time_overrides = self.configured_fields(database, settings, "stop_times", "overrides")

        # The trip headsign is resolved per trip, so it is not applied as a plain default or override
        self._headsign_default = self._defaults.pop("trip_headsign", None)
        self._headsign_override = self._overrides.pop("trip_headsign", None)
        self._headsign_fields: set[str] = set()
        for section, configured in (("defaults", self._headsign_default), ("overrides", self._headsign_override)):
            for placeholder in PLACEHOLDER.findall(str(configured or "")):
                field = DESTINATION_FIELD.fullmatch(placeholder)
                if field is None:
                    raise ConfigurationError(
                        f"{section}.trips.trip_headsign: unsupported placeholder {{{placeholder}}}; "
                        "use {vehicle_destination_text.COLUMN}"
                    )

                self._headsign_fields.add(field.group(1).upper())

        ids = settings.get("ids")
        if not isinstance(ids, Mapping) or not isinstance(ids.get("trips"), Mapping):
            raise ConfigurationError("ids.trips must be a mapping with mode")

        self._mode = ids["trips"].get("mode")
        if self._mode not in ("global", "template"):
            raise ConfigurationError("ids.trips.mode must be global or template")

        self._template: IdTemplate | None = None
        if self._mode == "template":
            self._template = self.make_id_template(settings, "trips", {"trip": "trip.din"})

    def _identifier(self, row: Row, context: str) -> str:
        """Build the GTFS trip ID from the global ID or the ID template, without whitespace or control characters."""
        if self._mode == "global":
            identifier = _required(row, "GLOBAL_ID", context)
        else:
            assert self._template is not None
            identifier = self.render_id(self._template, {"trip": row}, context)

        normalized = UNSAFE_CHARACTERS.sub("_", identifier.strip())
        if not normalized:
            raise InputError(f"{context}: trip ID is empty")

        return normalized

    # -- source reading ------------------------------------------------------------------------

    def _read_mappings(self, database: sqlite3.Connection) -> None:
        """Read the GTFS IDs of routes, services, shapes and stops by their DINO source keys."""
        self._route_ids: dict[SourceKey, str] = {
            tuple(row[:4]): row[4] for row in database.execute(
                "SELECT VERSION, LINE_NR, STR_LINE_VAR, LINE_DIR_NR, gtfs_id FROM map_line_2_routes"
            )
        }
        self._service_ids: dict[tuple[str, str, str | None], str] = {
            (row[0], row[1], row[2] or None): row[3] for row in database.execute(
                "SELECT VERSION, DAY_ATTRIBUTE_NR, RESTRICTION, gtfs_id FROM map_calendar_2_calendar"
            )
        }
        self._shape_ids: dict[SourceKey, str] = {
            tuple(row[:4]): row[4] for row in database.execute(
                "SELECT VERSION, LINE_NR, STR_LINE_VAR, LINE_DIR_NR, gtfs_id FROM map_route_2_shapes"
            )
        }
        self._point_ids: dict[SourceKey, set[str]] = {}
        for version, stop, _, point, identifier in database.execute(
            "SELECT VERSION, STOP_ID, STOP_AREA_ID, STOPPING_POINT_NR, gtfs_id FROM map_stop_point_2_stops"
        ):
            self._point_ids.setdefault((version, stop, point), set()).add(identifier)

        self._station_ids: dict[SourceKey, str] = {
            (row[0], row[1]): row[2] for row in database.execute(
                "SELECT VERSION, STOP_ID, gtfs_id FROM map_stop_2_stops"
            )
        }

    def _read_route_stops(self, database: sqlite3.Connection) -> None:
        """Read the ordered stops of each route with their cumulative distance."""
        segments: dict[SourceKey, list[tuple[int, str, str, int, float]]] = {}
        for rowid, row in _rows(database, "route"):
            context = f"route.din row {rowid}"
            stop_type = _optional(row, "STOPPING_POINT_TYPE")
            length = _required(row, "LENGTH", context)
            try:
                distance = float(length)
            except ValueError as exc:
                raise InputError(f"{context}: invalid LENGTH: {length!r}") from exc

            if distance < 0:
                raise InputError(f"{context}: invalid LENGTH: {length!r}")

            segments.setdefault(_source_key(row, LINE_KEY, context), []).append((
                _integer(_required(row, "LINE_CONSEC_NR", context), "LINE_CONSEC_NR", context),
                _required(row, "STOP_NR", context), _required(row, "STOPPING_POINT_NR", context),
                int(stop_type) if stop_type is not None and re.fullmatch(r"-?\d+", stop_type.strip()) else 0,
                distance,
            ))

        # The LENGTH of a stop is the distance from its predecessor, so the first stop starts at 0
        self._route_stops: dict[SourceKey, list[RouteStop]] = {}
        for key, stops in segments.items():
            stops.sort()
            travelled = 0.0
            ordered: list[RouteStop] = []
            for index, (consecutive, stop, point, stop_type, distance) in enumerate(stops):
                travelled += distance if index else 0.0
                ordered.append((consecutive, stop, point, stop_type, round(travelled, 3)))

            self._route_stops[key] = ordered

    def _read_timings(self, database: sqlite3.Connection) -> None:
        """Read the travel and dwell times of each timing group and route position."""
        self._timings: dict[SourceKey, dict[int, Timing]] = {}
        for rowid, row in _rows(database, "timing_pattern"):
            context = f"timing_pattern.din row {rowid}"
            key = _source_key(row, (*LINE_KEY, "TIMING_GROUP_NR"), context)
            consecutive = _integer(_required(row, "LINE_CONSEC_NR", context), "LINE_CONSEC_NR", context)
            dwell = _optional(row, "STOPPING_TIME")
            self._timings.setdefault(key, {})[consecutive] = (
                _integer(_required(row, "TT_REL", context), "TT_REL", context),
                _integer(dwell, "STOPPING_TIME", context) if dwell is not None else 0,
            )

    def _read_trip_dwell_times(self, database: sqlite3.Connection) -> None:
        """Read the dwell times of single trips, which supersede the timing pattern."""
        self._trip_dwell: dict[SourceKey, dict[int, int]] = {}
        columns = _columns(
            database, "trip_stop_time", ("VERSION", "LINE_NR", "TRIP_ID", "LINE_CONSEC_NR"), optional=True
        )
        if "STOPPING_TIME" not in columns:
            return

        for rowid, row in _rows(database, "trip_stop_time"):
            context = f"trip_stop_time.din row {rowid}"
            dwell = _optional(row, "STOPPING_TIME")
            if dwell is None:
                continue

            key = _source_key(row, ("VERSION", "LINE_NR", "TRIP_ID"), context)
            consecutive = _integer(_required(row, "LINE_CONSEC_NR", context), "LINE_CONSEC_NR", context)
            self._trip_dwell.setdefault(key, {})[consecutive] = _integer(dwell, "STOPPING_TIME", context)

    def _read_headsigns(self, database: sqlite3.Connection) -> None:
        """Read the destination texts and the destination changes along each trip."""
        self._branches: dict[SourceKey, str] = {}
        if _columns(database, "line", ("VERSION", "LINE_NR"), optional=True):
            for rowid, row in _rows(database, "line"):
                context = f"line.din row {rowid}"
                self._branches[_source_key(row, ("VERSION", "LINE_NR"), context)] = _optional(row, "BRANCH_NR") or ""

        # The branch of a destination text is optional; texts without branch are valid for every branch
        self._texts: dict[SourceKey, Row] = {}
        columns = _columns(database, "vehicle_destination_text", ("VERSION", "VDT_NR"), optional=True)
        missing = self._headsign_fields - columns if columns else set()
        if missing:
            raise ConfigurationError(
                "trips.trip_headsign refers to columns missing in vehicle_destination_text.din: "
                + ", ".join(sorted(missing))
            )

        for rowid, row in _rows(database, "vehicle_destination_text") if columns else ():
            context = f"vehicle_destination_text.din row {rowid}"
            branch = _optional(row, "BRANCH_NR") or ""
            self._texts[(*_source_key(row, ("VERSION",), context), branch, _required(row, "VDT_NR", context))] = row

        self._trip_vdt: dict[SourceKey, list[tuple[int, str]]] = {}
        required = (*TRIP_KEY, "LINE_CONSEC_NR", "VDT_NR")
        if _columns(database, "trip_vdt", required, optional=True):
            for rowid, row in _rows(database, "trip_vdt"):
                context = f"trip_vdt.din row {rowid}"
                consecutive = _integer(_required(row, "LINE_CONSEC_NR", context), "LINE_CONSEC_NR", context)
                self._trip_vdt.setdefault(_source_key(row, TRIP_KEY, context), []).append(
                    (consecutive, _required(row, "VDT_NR", context))
                )

            for changes in self._trip_vdt.values():
                changes.sort()

    # -- conversion ----------------------------------------------------------------------------

    def _convert_trip(
        self, row: Row, key: SourceKey, identifier: str, context: str
    ) -> tuple[Row, list[Row]] | None:
        """Convert one DINO trip into a GTFS trip with its stop times, or None when it has no stop times."""
        version, direction = key[0], key[3]
        line_key = key[:4]

        # Resolve the route, service, shape and direction
        route_id = self._route_ids.get(line_key)
        if route_id is None:
            raise InputError(f"{context}: no GTFS route for the line of the trip in map_line_2_routes")

        restriction = _optional(row, "RESTRICTION")
        attribute = _required(row, "DAY_ATTRIBUTE_NR", context)
        service_id = self._service_ids.get((version, attribute, restriction))
        if service_id is None:
            raise InputError(
                f"{context}: no GTFS service for DAY_ATTRIBUTE_NR {attribute!r} and RESTRICTION "
                f"{restriction!r} in map_calendar_2_calendar"
            )

        shape_id = self._shape_ids.get(line_key)
        if shape_id is None:
            LOGGER.warning("%s: trip %r has no shape", context, identifier)

        if direction not in ("1", "2"):
            raise InputError(f"{context}: invalid LINE_DIR_NR: {direction!r}")

        # Derive the stop times and the destination texts
        stops = self._route_stops.get(line_key)
        if not stops:
            raise InputError(f"{context}: route.din has no stops for the line of the trip")

        times = self._stop_times(row, key, stops, context)
        stop_rows = self._stop_rows(identifier, key, stops, times, context)
        if not stop_rows:
            LOGGER.warning("%s: trip %r has no stop times; skipped", context, identifier)
            return None

        destination = self._trip_destination(key, stops)
        source_headsign = self._headsign_text(destination)
        trip_headsign = self._resolve_headsign(source_headsign, destination)
        if trip_headsign is None and not any(stop["stop_headsign"] for stop in stop_rows):
            LOGGER.warning("%s: no headsign found for trip %r or its stop times", context, identifier)

        # A stop headsign is only needed where the destination text differs from the trip's
        for stop in stop_rows:
            if stop["stop_headsign"] == source_headsign:
                stop["stop_headsign"] = None

        return {
            "trip_id": identifier, "route_id": route_id, "service_id": service_id,
            "trip_headsign": trip_headsign, "trip_short_name": _optional(row, "TRIP_ID_PRINTING"),
            "direction_id": int(direction) - 1, "shape_id": shape_id,
            "bikes_allowed": BIKES_ALLOWED.get((_optional(row, "BIKE_ALLOWED") or "").strip(), 0),
        }, stop_rows

    def _stop_times(
        self, row: Row, key: SourceKey, stops: list[RouteStop], context: str
    ) -> dict[int, tuple[int, int]]:
        """Derive arrival and departure seconds per route position from the start time and timing pattern."""
        start = _integer(_required(row, "DEPARTURE_TIME", context), "DEPARTURE_TIME", context)
        if start < 0:
            raise InputError(f"{context}: invalid DEPARTURE_TIME: {row['DEPARTURE_TIME']!r}")

        group = _required(row, "TIMING_GROUP_NR", context)
        timing = self._timings.get((*key[:4], group))
        if timing is None:
            raise InputError(f"{context}: timing_pattern.din has no entries for TIMING_GROUP_NR {group!r}")

        overrides = self._trip_dwell.get((key[0], key[1], key[4]), {})
        result: dict[int, tuple[int, int]] = {}
        departure = start
        for index, (consecutive, _, _, stop_type, _) in enumerate(stops):
            entry = timing.get(consecutive)
            if entry is None:
                raise InputError(
                    f"{context}: timing_pattern.din has no entry for LINE_CONSEC_NR {consecutive} "
                    f"of TIMING_GROUP_NR {group!r}"
                )

            travel, dwell = entry
            if travel < 0 and index and stop_type not in OMITTED_TYPES:
                raise InputError(
                    f"{context}: timing_pattern.din has no travel time for LINE_CONSEC_NR {consecutive} "
                    f"of TIMING_GROUP_NR {group!r}"
                )

            arrival = departure + max(travel, 0) if index else start
            departure = arrival + overrides.get(consecutive, dwell)
            result[consecutive] = (arrival, departure)

        return result

    def _stop_rows(
        self, identifier: str, key: SourceKey, stops: list[RouteStop], times: Mapping[int, tuple[int, int]],
        context: str,
    ) -> list[Row]:
        """Create the stop time rows of all passenger stops, renumbering the sequence after omitted stops."""
        changes = self._trip_vdt.get(key, [])
        branch = self._branches.get((key[0], key[1]))
        rows: list[Row] = []
        for consecutive, stop, point, stop_type, distance in stops:
            if stop_type in OMITTED_TYPES:
                continue

            # Unlisted types are regular stops
            active = [vdt for position, vdt in changes if position <= consecutive]
            arrival, departure = times[consecutive]
            pickup, drop_off = PICKUP_DROP_OFF.get(stop_type, (0, 0))
            rows.append({
                "trip_id": identifier, "arrival_time": _time(arrival), "departure_time": _time(departure),
                "stop_id": self._stop_id(key[0], stop, point, context), "stop_sequence": len(rows) + 1,
                "stop_headsign": self._headsign_text(self._destination(key[0], branch or "", active[-1])) if active else None,
                "pickup_type": pickup, "drop_off_type": drop_off, "shape_dist_traveled": distance,
            })

        return rows

    def _destination(self, version: str, branch: str, number: str) -> Row | None:
        """Find a destination text by branch, falling back to texts without branch."""
        return self._texts.get((version, branch, number)) or self._texts.get((version, "", number))

    def _trip_destination(self, key: SourceKey, stops: list[RouteStop]) -> Row | None:
        """Use the destination text that is valid at the first passenger stop as the trip's destination."""
        first = next((stop[0] for stop in stops if stop[3] not in OMITTED_TYPES), None)
        active = [vdt for position, vdt in self._trip_vdt.get(key, []) if first is not None and position <= first]
        branch = self._branches.get((key[0], key[1]), "")
        return self._destination(key[0], branch, active[-1]) if active else None

    @staticmethod
    def _headsign_text(destination: Row | None) -> str | None:
        text = _optional(destination, HEADSIGN_COLUMN) if destination else None
        return UNSAFE_CHARACTERS.sub(" ", text).strip() or None if text else None

    def _resolve_headsign(self, source_headsign: str | None, destination: Row | None) -> str | None:
        """Prefer the configured override, then the destination text, then the configured default."""
        return (
            self._render_headsign(self._headsign_override, destination)
            or source_headsign
            or self._render_headsign(self._headsign_default, destination)
        )

    @staticmethod
    def _render_headsign(configured: object, destination: Row | None) -> str | None:
        """Substitute the vehicle_destination_text placeholders of a configured headsign."""
        if configured is None:
            return None

        def value(match: re.Match[str]) -> str:
            field = DESTINATION_FIELD.fullmatch(match.group(1))
            text = _optional(destination, field.group(1).upper()) if destination and field else None
            return text.strip() if text else ""

        return UNSAFE_CHARACTERS.sub(" ", PLACEHOLDER.sub(value, str(configured))).strip() or None

    def _stop_id(self, version: str, stop: str, point: str, context: str) -> str:
        """Resolve the final stop ID of a route stop, falling back to the station for stopping point 0."""
        identifiers = self._point_ids.get((version, stop, point), set())
        if len(identifiers) > 1:
            raise InputError(
                f"{context}: stopping point {stop}:{point} of VERSION {version!r} is ambiguous across stop areas"
            )

        if identifiers:
            return next(iter(identifiers))

        station = self._station_ids.get((version, stop)) if point == "0" else None
        if station is None:
            raise InputError(
                f"{context}: stopping point {stop}:{point} of VERSION {version!r} has no final stop in the stop mappings"
            )

        return station
