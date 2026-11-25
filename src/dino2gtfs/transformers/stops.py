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


def _coordinates(row: Mapping[str, object], x_name: str, y_name: str, context: str) -> tuple[float, float] | None:
    values = []
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


class _Coordinates:
    def __init__(self, database: sqlite3.Connection, settings: Mapping[str, Any]) -> None:
        input_settings = settings.get("input", {})
        if not isinstance(input_settings, Mapping):
            raise ConfigurationError("input must be a mapping")
        
        self.default_crs = input_settings.get("crs")
        if self.default_crs is not None and (
            isinstance(self.default_crs, bool) or not isinstance(self.default_crs, (str, int))
            or (isinstance(self.default_crs, str) and not self.default_crs.strip())
        ):
            raise ConfigurationError("input.crs must be a CRS identifier or null")

        self.metadata: dict[str, tuple[object, float, float, float, float]] = {}
        self.transformers: dict[object, CrsTransformer] = {}
        
        columns = _columns(database, "coordsys", ("VERSION",), optional=True)
        if columns:
            for rowid, row in _rows(database, "coordsys"):
                context = f"coordsys.din row {rowid}"
                version = _required(row, "VERSION", context)
                crs = _optional(row, "EPSG_CODE")
                
                parameters = []
                for field, default in (("TRANS_X", 0.0), ("TRANS_Y", 0.0), ("SCALE_X", 1.0), ("SCALE_Y", 1.0)):
                    value = _optional(row, field)
                    try:
                        number = float(value) if value is not None else default
                    except ValueError as exc:
                        raise InputError(f"{context}: invalid {field}: {value!r}") from exc
                    
                    if not math.isfinite(number) or (field.startswith("SCALE_") and number == 0):
                        raise InputError(f"{context}: invalid {field}: {value!r}")
                    
                    parameters.append(number)
                metadata = (crs, parameters[0], parameters[1], parameters[2], parameters[3])
                if version in self.metadata and self.metadata[version] != metadata:
                    raise CollisionError(f"conflicting coordsys.din records for VERSION {version!r}")
                
                self.metadata[version] = metadata

    def convert(self, version: str, xy: tuple[float, float] | None, context: str) -> tuple[float, float]:
        if xy is None:
            LOGGER.warning("%s: missing coordinates; using 0,0 before defaults", context)
            return 0.0, 0.0
        crs, tx, ty, sx, sy = self.metadata.get(version, (None, 0.0, 0.0, 1.0, 1.0))
        configured = crs is None
        crs = self.default_crs if configured else crs
        if crs is None:
            raise ConfigurationError(f"{context}: input.crs is required when coordsys.din has no EPSG_CODE")
        
        try:
            if crs not in self.transformers:
                self.transformers[crs] = CrsTransformer.from_crs(CRS.from_user_input(crs), "EPSG:4326", always_xy=True)
            
            lon, lat = self.transformers[crs].transform(xy[0] * sx + tx, xy[1] * sy + ty, errcheck=True)
        except (CRSError, ProjError) as exc:
            error = ConfigurationError if configured else InputError
            raise error(f"{context}: unable to transform coordinates from CRS {crs!r}: {exc}") from exc
        if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise InputError(f"{context}: transformed coordinates are outside WGS84 bounds")
        
        return lat, lon


class StopsTransformer(TransformerBase):
    """Create station and boarding-stop rows from DINO stop relations."""

    def initialize(self, database: sqlite3.Connection, settings: Mapping[str, Any]) -> None:
        database.execute(
            "CREATE TABLE map_stop_2_stops (VERSION TEXT, STOP_ID TEXT, gtfs_id TEXT)"
        )

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

    def _identifier(
        self, mode: str, template: IdTemplate | None, sources: Mapping[str, Mapping[str, object]], context: str
    ) -> str:
        components = self.fingerprint_components(sources, context)
        kind = components[0]
        source = sources["stop_point"] if kind == "platform" else sources["stop"]
        if mode == "global":
            return _required(source, "GLOBAL_ID", context)
        
        assert template is not None
        return self.render_id(template, sources, context, {"stop_kind": kind})

    def transform(self, database: sqlite3.Connection, settings: Mapping[str, Any]) -> None:
        defaults = self.configured_defaults(database, settings, "stops")
        if "stop_name" in defaults and (
            not isinstance(defaults["stop_name"], str) or not defaults["stop_name"].strip()
        ):
            raise ConfigurationError("defaults.stops.stop_name must be a non-empty string")
        
        ids = settings.get("ids")
        if not isinstance(ids, Mapping) or not isinstance(ids.get("stops"), Mapping):
            raise ConfigurationError("ids.stops must be a mapping with mode")
        
        mode = ids["stops"].get("mode")
        if mode not in ("global", "template"):
            raise ConfigurationError("ids.stops.mode must be global or template")
        
        template = self.make_id_template(settings, "stops", {"stop": "stop.din", "stop_point": "stop_point.din"}) if mode == "template" else None

        stop_columns = _columns(database, "stop", ("VERSION", "STOP_NR", "IS_RESPONSIBLE_STOP"))
        point_columns = _columns(database, "stop_point", ("VERSION", "STOP_NR", "STOP_AREA_NR", "STOPPING_POINT_NR"), optional=True)
        area_columns = _columns(database, "stop_area", ("VERSION", "STOP_NR", "STOP_AREA_NR"), optional=True)
        if template is not None:
            self.validate_template_fields(template, {"stop": stop_columns, "stop_point": point_columns},
                                          ("stop_kind",))
        coordinates = _Coordinates(database, settings)
        existing_ids = {row[0] for row in database.execute("SELECT stop_id FROM gtfs_stops")}
        results: dict[str, tuple[Row, str]] = {}
        global_ids: dict[tuple[str, str], dict[SourceKey, str]] = {}
        
        stations: dict[str, tuple[Row, str, str]] = {}
        station_versions: dict[str, str] = {}
        global_stations: dict[str, tuple[Row, str, str]] = {}
        point_ids: dict[SourceKey, str] = {}
        station_mappings: dict[SourceKey, str] = {}
        point_mappings: dict[SourceKey, str] = {}
        point_aliases: dict[SourceKey, SourceKey] = {}

        def check_global_id(row: Row, key: SourceKey, kind: str, context: str) -> None:
            global_id = _optional(row, "GLOBAL_ID")
            if global_id is None:
                return

            identities = global_ids.setdefault((key[0], global_id), {})
            identity = (kind, *key[1:])
            if identity not in identities:
                if identities:
                    prior = next(iter(identities.values()))
                    message = f"global ID collision in gtfs_stops: {global_id!r} ({prior} and {context})"
                    if mode == "global":
                        raise CollisionError(message)
                    LOGGER.warning("%s", message)
                
                identities[identity] = context

        def reuse_global_id(identifier: str, kind: str, context: str) -> bool:
            previous = results.get(identifier) if mode == "global" else None
            if previous is None:
                return False
            
            if (identifier in global_stations) != (kind == "station"):
                raise InputError(
                    f"global ID {identifier!r} cannot represent both a station and a boarding stop "
                    f"({previous[1]} and {context})"
                )
            
            return True

        def add(identifier: str, values: Row, context: str) -> None:
            values = self.apply_defaults(values, defaults)
            if identifier in existing_ids:
                raise CollisionError(f"identity collision in gtfs_stops: {identifier!r} (existing stop and {context})")
            if identifier in results:
                raise CollisionError(f"identity collision in gtfs_stops: {identifier!r} ({results[identifier][1]} and {context})")
            results[identifier] = (values, context)

        for rowid, row in _rows(database, "stop"):
            if row.get("IS_RESPONSIBLE_STOP") != "1":
                continue
            
            context = f"stop.din row {rowid}"
            key = _source_key(row, ("VERSION", "STOP_NR"), context)
            if key[1] in stations:
                station_mappings[key] = stations[key[1]][1]
                continue
            
            station_versions[key[1]] = key[0]
            check_global_id(row, key, "station", context)
            
            identifier = self._identifier(mode, template, {"stop": row}, context)
            station_mappings[key] = identifier
            if reuse_global_id(identifier, "station", context):
                stations[key[1]] = global_stations[identifier]
                continue
            
            name = defaults.get("stop_name", _optional(row, "STOP_NAME"))
            if name is None:
                LOGGER.warning("%s: STOP_NAME is missing and no defaults.stops.stop_name is configured; "
                               "the resulting stops may be invalid in GTFS", context)
            
            lat, lon = coordinates.convert(key[0], _coordinates(row, "STOP_POS_X", "STOP_POS_Y", context), context)
            add(identifier, {
                "stop_id": identifier, "stop_name": name, "stop_code": _optional(row, "STOP_SHORTNAME"),
                "stop_lat": lat, "stop_lon": lon, "location_type": 1, "parent_station": None, "stop_access": None,
            }, context)
            
            stations[key[1]] = (row, identifier, context)
            if mode == "global":
                global_stations[identifier] = stations[key[1]]

        areas: dict[SourceKey, tuple[Row, str]] = {}
        if area_columns:
            for rowid, row in _rows(database, "stop_area"):
                if _optional(row, "STOP_NR") not in stations:
                    continue
                
                context = f"stop_area.din row {rowid}"
                key = _source_key(row, ("VERSION", "STOP_NR", "STOP_AREA_NR"), context)
                if key in areas:
                    raise CollisionError(f"duplicate stop_area.din identity {key!r}: {areas[key][1]} and {context}")

                areas[key] = (row, context)

        if point_columns:
            preferred_points: dict[tuple[str, str], Row] = {}
            preferred_global_ids: dict[tuple[str, str], Row] = {}
            for _, row in _rows(database, "stop_point"):
                area_nr = _optional(row, "STOP_AREA_NR")
                stop_nr = _optional(row, "STOP_NR")
                point_nr = _optional(row, "STOPPING_POINT_NR")
                global_id = _optional(row, "GLOBAL_ID")
                if area_nr is not None and area_nr != "0" and stop_nr is not None and stop_nr in stations:
                    if point_nr is not None:
                        preferred_points.setdefault((stop_nr, point_nr), row)
                    
                    if global_id is not None:
                        preferred_global_ids.setdefault((stop_nr, global_id), row)

            for rowid, row in _rows(database, "stop_point"):
                stop_nr = _optional(row, "STOP_NR")
                if stop_nr not in stations:
                    continue
                context = f"stop_point.din row {rowid}"
                key = _source_key(row, ("VERSION", "STOP_NR", "STOP_AREA_NR", "STOPPING_POINT_NR"), context)
                if key[2] == "0":
                    preferred = preferred_points.get((stop_nr, key[3]))
                    global_id = _optional(row, "GLOBAL_ID")
                    if preferred is None and global_id is not None:
                        preferred = preferred_global_ids.get((stop_nr, global_id))
                    
                    if preferred is not None:
                        point_aliases[key] = _source_key(
                            preferred, ("STOP_NR", "STOP_AREA_NR", "STOPPING_POINT_NR"), context
                        )
                        
                        continue

                if key[0] == station_versions[key[1]]:
                    check_global_id(row, key, "platform", context)
                
                if key[1:] in point_ids:
                    point_mappings[key] = point_ids[key[1:]]
                    continue
                
                parent, parent_id, parent_context = stations[key[1]]
                if results[parent_id][0]["location_type"] != 1:
                    raise ConfigurationError(f"{context}: parent station must have location_type 1")
                
                if "location_type" in defaults and defaults["location_type"] != 0:
                    raise ConfigurationError(f"{context}: boarding stop must have location_type 0")
                
                area = areas.get(key[:3])
                identifier = self._identifier(mode, template, {"stop": parent, "stop_point": row}, context)
                point_ids[key[1:]] = identifier
                point_mappings[key] = identifier
                if reuse_global_id(identifier, "platform", context):
                    continue
                
                xy = _coordinates(row, "STOPPING_POINT_POS_X", "STOPPING_POINT_POS_Y", context)
                xy_version = key[0]
                if xy is None and area is not None:
                    xy = _coordinates(area[0], "STOP_AREA_POS_X", "STOP_AREA_POS_Y", area[1])
                if xy is None:
                    xy = _coordinates(parent, "STOP_POS_X", "STOP_POS_Y", parent_context)
                    xy_version = _required(parent, "VERSION", parent_context)
                
                lat, lon = coordinates.convert(xy_version, xy, context)
                
                access = _optional(row, "STREET_ACCESS")
                if access is not None:
                    try:
                        access_number = int(access)
                    except ValueError as exc:
                        raise InputError(f"{context}: invalid STREET_ACCESS: {access!r}") from exc
                    if access_number < 0:
                        raise InputError(f"{context}: invalid STREET_ACCESS: {access!r}")
                
                    stop_access: int | None = int(access_number != 0)
                else:
                    stop_access = None
                
                add(identifier, {
                    "stop_id": identifier, "stop_name": results[parent_id][0]["stop_name"],
                    "stop_code": _optional(row, "STOPPING_POINT_SHORTNAME"), "stop_lat": lat, "stop_lon": lon,
                    "location_type": 0, "parent_station": parent_id, "stop_access": stop_access,
                }, context)

        fields = ("stop_id", "stop_name", "stop_code", "stop_lat", "stop_lon", "location_type", "parent_station", "stop_access")
        for key, preferred_key in point_aliases.items():
            point_mappings[key] = point_ids[preferred_key]

        self.insert_with_defaults(database, "stops", fields, (values for values, _ in results.values()), defaults)
        
        database.executemany(
            "INSERT INTO map_stop_2_stops (VERSION, STOP_ID, gtfs_id) VALUES (?, ?, ?)",
            ((*key, identifier) for key, identifier in station_mappings.items()),
        )
        
        database.executemany(
            "INSERT INTO map_stop_point_2_stops "
            "(VERSION, STOP_ID, STOP_AREA_ID, STOPPING_POINT_NR, gtfs_id) VALUES (?, ?, ?, ?, ?)",
            ((*key, identifier) for key, identifier in point_mappings.items()),
        )
        
        LOGGER.info("transformed %d DINO stations and boarding stops", len(results))
