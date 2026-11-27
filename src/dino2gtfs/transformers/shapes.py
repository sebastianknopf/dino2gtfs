"""Transform DINO routes and link geometries into GTFS shapes."""
import hashlib
import logging
import math
import sqlite3
from collections.abc import Mapping
from typing import Any

from dino2gtfs.domain.errors import CollisionError, ConfigurationError, InputError
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
from dino2gtfs.transformers.stops import StopsTransformer, _coordinates

LOGGER = logging.getLogger(__name__)

# GTFS fields written by this transformer
GTFS_FIELDS = ("shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence", "shape_dist_traveled")
# DINO columns identifying a route, i.e. one direction of a line variant (canonical ID components)
ROUTE_KEY = ("VERSION", "LINE_NR", "STR_LINE_VAR", "LINE_DIR_NR")
# DINO columns of a link end point (stop number, stop area number, stopping point number)
LINK_ORIGIN = ("ORIG_STOP_NR", "ORIG_STOP_AREA_NR", "STOPPING_POINT_NR")
LINK_DESTINATION = ("DEST_STOP_NR", "DEST_STOP_AREA_NR", "DEST_STOPPING_POINT_NR")
EARTH_RADIUS = 6371008.8
# DINO columns of a stopping point as shown in messages
STOPPING_POINT_KEY = "(VERSION, STOP_NR, STOP_AREA_NR, STOPPING_POINT_NR)"

# A route: its first row, row context and the ordered (consecutive number, stop, stopping point) records.
Route = tuple[Row, str, list[tuple[int, str, str]]]
# A shape point: latitude and longitude.
Point = tuple[float, float]
# A stopping point inside one version: stop number, stop area number and stopping point number.
PointKey = tuple[str, str, str]
# A version shape: its (version, hash) identity.
ShapeKey = tuple[str, str]


def _distance(first: Point, second: Point) -> float:
    """Return the great-circle distance of two points in meters."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*first, *second))
    a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS * math.asin(math.sqrt(a))


def _integer(value: str, name: str, context: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise InputError(f"{context}: invalid {name}: {value!r}") from exc


def _label(version: str, point: PointKey) -> str:
    return f"({version}, {', '.join(point)})"


class ShapesTransformer(TransformerBase):
    """Create shapes per DINO version first and deduplicate them by their final stop sequence afterwards."""

    # Coordinate handling is shared with the stops transformer
    _load_coordinate_systems = StopsTransformer._load_coordinate_systems
    _to_wgs84 = StopsTransformer._to_wgs84

    def initialize(self, database: sqlite3.Connection, settings: Mapping[str, Any]) -> None:
        database.execute(
            "CREATE TABLE map_route_2_shapes "
            "(VERSION TEXT, LINE_NR TEXT, STR_LINE_VAR TEXT, LINE_DIR_NR TEXT, gtfs_id TEXT)"
        )

    def fingerprint_components(
        self, sources: Mapping[str, Mapping[str, object]], context: str
    ) -> tuple[str, ...]:
        return _source_key(sources["route"], ROUTE_KEY, context)

    def canonical_value(
        self, sources: Mapping[str, Mapping[str, object]], context: str,
        components: tuple[str, ...] | None = None,
    ) -> str:
        return ":".join(components or self.fingerprint_components(sources, context))

    def transform(self, database: sqlite3.Connection, settings: Mapping[str, Any]) -> None:
        # Step 1: read and validate the configuration
        defaults = self.configured_fields(database, settings, "shapes", "defaults")
        overrides = self.configured_fields(database, settings, "shapes", "overrides")

        ids = settings.get("ids")
        if not isinstance(ids, Mapping) or not isinstance(ids.get("shapes"), Mapping):
            raise ConfigurationError("ids.shapes must be a mapping with mode")

        if ids["shapes"].get("mode") != "template":
            raise ConfigurationError("ids.shapes.mode must be template")

        template = self.make_id_template(settings, "shapes", {"route": "route.din"})

        # Step 2: validate the DINO source tables
        route_columns = _columns(database, "route", (*ROUTE_KEY, "LINE_CONSEC_NR", "STOP_NR", "STOPPING_POINT_NR"))
        self.validate_template_fields(template, {"route": route_columns})
        self._load_coordinate_systems(database, settings)
        self._read_version_ranks(database)

        # Step 3: read the stopping points and coordinates of each version, independent of the final stops
        self._read_stopping_points(database)

        # Step 4: read the links and link geometries of each version
        self._read_links(database)

        # Step 5: build one shape per version and distinct stop sequence, identified by a hash
        routes = self._read_routes(database)
        route_shapes: dict[SourceKey, ShapeKey] = {}
        version_shapes: dict[ShapeKey, tuple[list[Point], str]] = {}
        sequences: dict[ShapeKey, list[PointKey]] = {}
        for key, (row, context, records) in routes.items():
            sequence = self._resolve_sequence(key[0], records, context)
            digest = hashlib.sha1("|".join(":".join(point) for point in sequence).encode("utf-8")).hexdigest()
            shape_key = (key[0], digest)
            route_shapes[key] = shape_key
            if shape_key not in version_shapes:
                sequences[shape_key] = sequence
                version_shapes[shape_key] = (self._build_path(key[0], sequence, context), context)

        # Step 6: map the version shapes to final stops and deduplicate them; the highest ranked version wins
        finals = self._read_final_stops(database)
        final_sequences: dict[ShapeKey, tuple[str, ...]] = {}
        winners: dict[tuple[str, ...], SourceKey] = {}
        for key in routes:
            shape_key = route_shapes[key]
            if shape_key not in final_sequences:
                final_sequences[shape_key] = self._map_final(
                    shape_key[0], sequences[shape_key], finals, version_shapes[shape_key][1]
                )

            final = final_sequences[shape_key]
            if len(final) < 2:
                LOGGER.warning("%s: route has fewer than two final stop points; no shape created", routes[key][1])
                continue

            current = winners.get(final)
            if current is None or self._rank(key[0]) >= self._rank(current[0]):
                winners[final] = key

        # Step 7: render the shape IDs and the shape points of the winning routes
        existing_ids = {row[0] for row in database.execute("SELECT shape_id FROM gtfs_shapes")}
        shape_ids: dict[tuple[str, ...], str] = {}
        contexts: dict[str, str] = {}
        rows: list[dict[str, object]] = []
        for final, key in winners.items():
            row, context, _ = routes[key]
            identifier = self.render_id(template, {"route": row}, context)
            if identifier in existing_ids:
                raise CollisionError(
                    f"Identity collision in gtfs_shapes: {identifier!r} (existing shape and {context})"
                )

            if identifier in contexts:
                raise CollisionError(
                    f"Identity collision in gtfs_shapes: {identifier!r} ({contexts[identifier]} and {context})"
                )

            contexts[identifier] = context
            shape_ids[final] = identifier
            rows.extend(self._shape_rows(identifier, version_shapes[route_shapes[key]][0]))

        # Step 8: write the shapes and map every source route to its shape
        self.insert_with_configuration(database, "shapes", GTFS_FIELDS, rows, defaults, overrides)
        database.executemany(
            "INSERT INTO map_route_2_shapes (VERSION, LINE_NR, STR_LINE_VAR, LINE_DIR_NR, gtfs_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                (*key, shape_ids[final_sequences[route_shapes[key]]]) for key in routes
                if final_sequences[route_shapes[key]] in shape_ids
            ),
        )

        LOGGER.info(
            "transformed %d DINO routes into %d version shapes and %d GTFS shapes",
            len(routes), len(version_shapes), len(shape_ids),
        )

    # -- source reading ------------------------------------------------------------------------

    def _read_version_ranks(self, database: sqlite3.Connection) -> None:
        """Read the priority of each version; versions without priority rank as zero."""
        self._priorities: dict[str, int] = {}
        if not _columns(database, "version", ("VERSION",), optional=True):
            return

        for rowid, row in _rows(database, "version"):
            context = f"version.din row {rowid}"
            priority = _optional(row, "PERIOD_PRIORITY")
            self._priorities[_required(row, "VERSION", context)] = (
                _integer(priority, "PERIOD_PRIORITY", context) if priority is not None else 0
            )

    def _rank(self, version: str) -> tuple[int, int, str]:
        """Order versions by priority first and by version number second; the highest wins."""
        number = int(version) if version.strip().lstrip("-").isdigit() else -(2**62)
        return self._priorities.get(version, 0), number, version

    def _read_stopping_points(self, database: sqlite3.Connection) -> None:
        """Read the stop areas of each stopping point and the WGS84 positions of stops and stop areas."""
        self._areas: dict[tuple[str, str, str], list[str]] = {}
        self._positions: dict[tuple[str, ...], Point] = {}
        point_key = ("VERSION", "STOP_NR", "STOP_AREA_NR", "STOPPING_POINT_NR")
        if _columns(database, "stop_point", point_key, optional=True):
            for rowid, row in _rows(database, "stop_point"):
                version, stop, area, point = _source_key(row, point_key, f"stop_point.din row {rowid}")
                areas = self._areas.setdefault((version, stop, point), [])
                if area not in areas:
                    areas.append(area)

        for table, key, x, y in (
            ("stop", ("VERSION", "STOP_NR"), "STOP_POS_X", "STOP_POS_Y"),
            ("stop_area", ("VERSION", "STOP_NR", "STOP_AREA_NR"), "STOP_AREA_POS_X", "STOP_AREA_POS_Y"),
        ):
            if not _columns(database, table, key, optional=True):
                continue

            for rowid, row in _rows(database, table):
                context = f"{table}.din row {rowid}"
                xy = _coordinates(row, x, y, context)
                if xy is not None:
                    version = _required(row, "VERSION", context)
                    lat, lon = self._to_wgs84(version, xy, context)
                    assert lat is not None and lon is not None
                    self._positions[_source_key(row, key, context)] = (lat, lon)

    def _read_links(self, database: sqlite3.Connection) -> None:
        """Read the links between stopping points and the WGS84 vertices of each link, per version."""
        self._links: dict[tuple[str, PointKey, PointKey], str] = {}
        self._geometries: dict[tuple[str, str], list[Point]] = {}
        link_columns = ("VERSION", "LINK_ID", *LINK_ORIGIN, *LINK_DESTINATION)
        if _columns(database, "link", link_columns, optional=True):
            for rowid, row in _rows(database, "link"):
                context = f"link.din row {rowid}"
                version = _required(row, "VERSION", context)
                origin = _source_key(row, LINK_ORIGIN, context)
                destination = _source_key(row, LINK_DESTINATION, context)
                self._links[(version, origin, destination)] = _required(row, "LINK_ID", context)

        geometry_columns = ("VERSION", "LINK_ID", "LINK_CONSEC_PT_NR", "LINK_PT_X", "LINK_PT_Y")
        if not _columns(database, "link_geometry", geometry_columns, optional=True):
            return

        vertices: dict[tuple[str, str], list[tuple[int, Point]]] = {}
        for rowid, row in _rows(database, "link_geometry"):
            context = f"link_geometry.din row {rowid}"
            link = (_required(row, "VERSION", context), _required(row, "LINK_ID", context))
            position = _integer(_required(row, "LINK_CONSEC_PT_NR", context), "LINK_CONSEC_PT_NR", context)
            xy = _coordinates(row, "LINK_PT_X", "LINK_PT_Y", context)
            if xy is None:
                raise InputError(f"{context}: LINK_PT_X and LINK_PT_Y are required")

            lat, lon = self._to_wgs84(link[0], xy, context)
            assert lat is not None and lon is not None
            vertices.setdefault(link, []).append((position, (lat, lon)))

        self._geometries = {link: [point for _, point in sorted(items)] for link, items in vertices.items()}

    @staticmethod
    def _read_routes(database: sqlite3.Connection) -> dict[SourceKey, Route]:
        """Read the ordered stopping points of each route."""
        routes: dict[SourceKey, Route] = {}
        for rowid, row in _rows(database, "route"):
            context = f"route.din row {rowid}"
            key = _source_key(row, ROUTE_KEY, context)
            consecutive = _integer(_required(row, "LINE_CONSEC_NR", context), "LINE_CONSEC_NR", context)
            record = (consecutive, _required(row, "STOP_NR", context), _required(row, "STOPPING_POINT_NR", context))
            routes.setdefault(key, (row, context, []))[2].append(record)

        for _, _, records in routes.values():
            records.sort(key=lambda record: record[0])

        return routes

    @staticmethod
    def _read_final_stops(database: sqlite3.Connection) -> dict[tuple[str, str, str, str], str]:
        """Read the final stop ID of each source stopping point from the stop mapping."""
        return {
            (version, stop, area, point): identifier
            for version, stop, area, point, identifier in database.execute(
                "SELECT VERSION, STOP_ID, STOP_AREA_ID, STOPPING_POINT_NR, gtfs_id FROM map_stop_point_2_stops"
            )
        }

    # -- shape building ------------------------------------------------------------------------

    def _resolve_sequence(
        self, version: str, records: list[tuple[int, str, str]], context: str
    ) -> list[PointKey]:
        """Resolve the route records to stopping points of the version, collapsing direct repetitions."""
        sequence: list[PointKey] = []
        for _, stop, point in records:
            areas = self._areas.get((version, stop, point), [])
            if not areas:
                raise InputError(
                    f"{context}: stopping point {stop}:{point} of VERSION {version!r} is missing in stop_point.din"
                )

            if len(areas) > 1:
                raise InputError(
                    f"{context}: stopping point {stop}:{point} of VERSION {version!r} is ambiguous across stop areas"
                )

            key = (stop, areas[0], point)
            if not sequence or sequence[-1] != key:
                sequence.append(key)

        return sequence

    def _build_path(self, version: str, sequence: list[PointKey], context: str) -> list[Point]:
        """Join the link geometries along the stopping points, falling back to the source stop positions."""
        path: list[Point] = []
        for origin, destination in zip(sequence, sequence[1:]):
            link = self._links.get((version, origin, destination))
            vertices = self._geometries.get((version, link)) if link is not None else None
            if not vertices:
                LOGGER.warning(
                    "%s: no link %s between stopping points %s = %s and %s; using a straight line",
                    context, "geometry" if link is not None else "in link.din", STOPPING_POINT_KEY,
                    _label(version, origin), _label(version, destination),
                )
                vertices = [self._position(version, origin, context), self._position(version, destination, context)]

            path.extend(point for point in vertices if not path or point != path[-1])

        return path

    def _position(self, version: str, point: PointKey, context: str) -> Point:
        """Look up the position of a stopping point from its stop area, then from its stop."""
        position = self._positions.get((version, point[0], point[1])) or self._positions.get((version, point[0]))
        if position is None:
            raise InputError(
                f"{context}: no coordinates for stopping point {STOPPING_POINT_KEY} = {_label(version, point)}"
            )

        return position

    @staticmethod
    def _map_final(
        version: str, sequence: list[PointKey], finals: Mapping[tuple[str, str, str, str], str], context: str
    ) -> tuple[str, ...]:
        """Map the stopping points of a version shape to final stop IDs, collapsing repeated final stops."""
        result: list[str] = []
        for stop, area, point in sequence:
            identifier = finals.get((version, stop, area, point))
            if identifier is None:
                raise InputError(
                    f"{context}: stopping point {STOPPING_POINT_KEY} = {_label(version, (stop, area, point))} "
                    "has no final stop in the stop mappings"
                )

            if not result or result[-1] != identifier:
                result.append(identifier)

        return tuple(result)

    @staticmethod
    def _shape_rows(identifier: str, path: list[Point]) -> list[dict[str, object]]:
        """Number the shape points and accumulate the travelled distance."""
        rows: list[dict[str, object]] = []
        distance = 0.0
        for index, point in enumerate(path):
            if index:
                distance += _distance(path[index - 1], point)

            rows.append({
                "shape_id": identifier, "shape_pt_lat": point[0], "shape_pt_lon": point[1],
                "shape_pt_sequence": index, "shape_dist_traveled": round(distance, 3),
            })

        return rows
