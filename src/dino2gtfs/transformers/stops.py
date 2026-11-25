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

    def convert(
        self, version: str, xy: tuple[float, float] | None, context: str
    ) -> tuple[float, float] | tuple[None, None]:
        if xy is None:
            return None, None
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

    def canonical_value(
        self, sources: Mapping[str, Mapping[str, object]], context: str,
        components: tuple[str, ...] | None = None,
    ) -> str:
        if "stop_point" in sources:
            components = _source_key(
                sources["stop_point"], ("VERSION", "STOP_NR", "STOP_AREA_NR", "STOPPING_POINT_NR"), context
            )
        else:
            components = _source_key(sources["stop"], ("VERSION", "STOP_NR"), context)

        return ":".join(components)

    def _identifier(
        self, mode: str, template: IdTemplate | None, sources: Mapping[str, Mapping[str, object]], context: str
    ) -> str:
        kind = "platform" if "stop_point" in sources else "station"
        source = sources["stop_point"] if kind == "platform" else sources["stop"]
        if mode == "global":
            return _required(source, "GLOBAL_ID", context)
        
        assert template is not None
        return self.render_id(template, sources, context, {"stop_kind": kind})

    def transform(self, database: sqlite3.Connection, settings: Mapping[str, Any]) -> None:
        defaults = self.configured_fields(database, settings, "stops", "defaults")
        overrides = self.configured_fields(database, settings, "stops", "overrides")
        for section, configured in (("defaults", defaults), ("overrides", overrides)):
            if "stop_name" in configured and (
                not isinstance(configured["stop_name"], str) or not configured["stop_name"].strip()
            ):
                raise ConfigurationError(f"{section}.stops.stop_name must be a non-empty string")
        
        ids = settings.get("ids")
        if not isinstance(ids, Mapping) or not isinstance(ids.get("stops"), Mapping):
            raise ConfigurationError("ids.stops must be a mapping with mode")
        
        mode = ids["stops"].get("mode")
        if mode not in ("global", "template"):
            raise ConfigurationError("ids.stops.mode must be global or template")
        
        template = self.make_id_template(settings, "stops", {"stop": "stop.din", "stop_point": "stop_point.din"}) if mode == "template" else None

        stop_columns = _columns(database, "stop", ("VERSION", "STOP_NR"))
        point_columns = _columns(database, "stop_point", ("VERSION", "STOP_NR", "STOP_AREA_NR", "STOPPING_POINT_NR"), optional=True)
        area_columns = _columns(database, "stop_area", ("VERSION", "STOP_NR", "STOP_AREA_NR"), optional=True)
        if template is not None:
            self.validate_template_fields(template, {"stop": stop_columns, "stop_point": point_columns},
                                          ("stop_kind",))
        coordinates = _Coordinates(database, settings)
        existing_ids = {row[0] for row in database.execute("SELECT stop_id FROM gtfs_stops")}
        results: dict[str, tuple[Row, str]] = {}

        def add(identifier: str, values: Row, context: str) -> None:
            values = self.apply_configuration(values, defaults, overrides)
            missing_coordinates = False
            for field in ("stop_lat", "stop_lon"):
                value = values[field]
                if field not in overrides and (value is None or (isinstance(value, str) and not value.strip())):
                    values[field] = 0.0
                    missing_coordinates = True
            
            if missing_coordinates:
                LOGGER.warning("%s: missing coordinates; using 0,0 for missing values", context)
            
            if identifier in existing_ids:
                raise CollisionError(f"identity collision in gtfs_stops: {identifier!r} (existing stop and {context})")
            
            if identifier in results:
                raise CollisionError(f"identity collision in gtfs_stops: {identifier!r} ({results[identifier][1]} and {context})")
            
            results[identifier] = (values, context)

        station_groups: dict[str, list[tuple[Row, SourceKey, str]]] = {}
        station_mappings: list[tuple[SourceKey, str]] = []
        global_kinds: dict[str, tuple[str, str]] = {}

        for rowid, row in _rows(database, "stop"):
            context = f"stop.din row {rowid}"
            key = _source_key(row, ("VERSION", "STOP_NR"), context)
            identity = _required(row, "GLOBAL_ID", context) if mode == "global" else key[1]
            station_groups.setdefault(identity, []).append((row, key, context))

        def preferred_record(
            records: list[tuple[Row, SourceKey, str]], is_responsible: Any
        ) -> tuple[Row, SourceKey, str]:
            return next((record for record in reversed(records) if is_responsible(record[0])), records[-1])

        station_by_source: dict[SourceKey, tuple[Row, str, str, bool]] = {}
        station_by_number: dict[str, tuple[Row, str, str, bool]] = {}
        for identity, records in station_groups.items():
            row, key, context = preferred_record(
                records, lambda candidate: candidate.get("IS_RESPONSIBLE_STOP") == "1"
            )
            
            responsible = row.get("IS_RESPONSIBLE_STOP") == "1"
            identifier = self._identifier(mode, template, {"stop": row}, context)
            if mode == "global":
                previous_kind = global_kinds.get(identifier)
                if previous_kind is not None and previous_kind[0] != "station":
                    raise InputError(
                        f"global ID {identifier!r} cannot represent both a station and a boarding stop "
                        f"({previous_kind[1]} and {context})"
                    )
                
                global_kinds[identifier] = ("station", context)

            name = self.apply_configuration(
                {"stop_name": _optional(row, "STOP_NAME")}, defaults, overrides
            )["stop_name"]
            if not isinstance(name, str) or not name.strip():
                raise InputError(
                    f"{context}: STOP_NAME is required; configure defaults.stops.stop_name or "
                    "overrides.stops.stop_name"
                )

            lat, lon = coordinates.convert(
                key[0], _coordinates(row, "STOP_POS_X", "STOP_POS_Y", context), context
            )
            add(identifier, {
                "stop_id": identifier, "stop_name": name, "stop_code": _optional(row, "STOP_SHORTNAME"),
                "stop_lat": lat, "stop_lon": lon, "location_type": 1, "parent_station": None, "stop_access": None,
            }, context)

            selected = (row, identifier, context, responsible)
            for _, source_key, _ in records:
                previous = station_by_source.get(source_key)
                if previous is not None and previous[1] != identifier:
                    raise CollisionError(
                        f"conflicting map_stop_2_stops source identity {source_key!r} "
                        f"({previous[2]} and {context})"
                    )
                
                station_mappings.append((source_key, identifier))
                station_by_source[source_key] = selected
                station_by_number.setdefault(source_key[1], selected)

        areas: dict[SourceKey, tuple[Row, str]] = {}
        if area_columns:
            for rowid, row in _rows(database, "stop_area"):
                if _optional(row, "STOP_NR") not in station_by_number:
                    continue
                
                context = f"stop_area.din row {rowid}"
                key = _source_key(row, ("VERSION", "STOP_NR", "STOP_AREA_NR"), context)
                if key in areas:
                    raise CollisionError(f"duplicate stop_area.din identity {key!r}: {areas[key][1]} and {context}")

                areas[key] = (row, context)

        def station_for(row: Row) -> tuple[Row, str, str, bool] | None:
            version = _optional(row, "VERSION")
            stop_nr = _optional(row, "STOP_NR")
            if version is None or stop_nr is None:
                return None

            return station_by_source.get((version, stop_nr), station_by_number.get(stop_nr))

        point_groups: dict[str, list[tuple[Row, SourceKey, str, tuple[Row, str, str, bool]]]] = {}
        point_mappings: list[tuple[SourceKey, str]] = []
        point_mapping_by_source: dict[SourceKey, tuple[str, str]] = {}
        if point_columns:
            for rowid, row in _rows(database, "stop_point"):
                context = f"stop_point.din row {rowid}"
                key = _source_key(row, ("VERSION", "STOP_NR", "STOP_AREA_NR", "STOPPING_POINT_NR"), context)
                identity = _required(row, "GLOBAL_ID", context) if mode == "global" else key[1]
                
                station = station_for(row)
                if station is None:
                    raise InputError(f"{context}: parent station STOP_NR {key[1]!r} is missing from stop.din")
                
                if mode == "global":
                    previous_kind = global_kinds.get(identity)
                    if previous_kind is not None and previous_kind[0] != "platform":
                        raise InputError(
                            f"global ID {identity!r} cannot represent both a station and a boarding stop "
                            f"({previous_kind[1]} and {context})"
                        )
                    
                    global_kinds[identity] = ("platform", context)
                
                point_groups.setdefault(identity, []).append((row, key, context, station))

            for records in point_groups.values():
                preferred = next(
                    (record for record in reversed(records) if record[3][3]), records[-1]
                )
                
                row, key, context, station = preferred
                parent, parent_id, parent_context, _ = station
                if results[parent_id][0]["location_type"] != 1:
                    raise ConfigurationError(f"{context}: parent station must have location_type 1")
                
                if "location_type" in overrides and overrides["location_type"] != 0:
                    raise ConfigurationError(f"{context}: boarding stop must have location_type 0")
                
                identifier = self._identifier(mode, template, {"stop": parent, "stop_point": row}, context)
                area = areas.get(key[:3])
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

                for _, source_key, _, _ in records:
                    previous = point_mapping_by_source.get(source_key)
                    if previous is not None and previous[0] != identifier:
                        raise CollisionError(
                            f"conflicting map_stop_point_2_stops source identity {source_key!r} "
                            f"({previous[1]} and {context})"
                        )
                    
                    point_mapping_by_source[source_key] = (identifier, context)
                    point_mappings.append((source_key, identifier))

        if mode == "template":
            global_ids: dict[str, tuple[tuple[str, str], str]] = {}
            for kind, groups in (("station", station_groups), ("platform", point_groups)):
                for identity, records in groups.items():
                    group_identity = (kind, identity)
                    for record in records:
                        row, context = record[0], record[2]
                        
                        global_id = _optional(row, "GLOBAL_ID")
                        if global_id is None:
                            continue
                        
                        previous = global_ids.get(global_id)
                        if previous is not None and previous[0] != group_identity:
                            LOGGER.warning(
                                f"global ID collision in gtfs_stops: {global_id!r} "
                                f"({previous[1]} and {context})"
                            )
                        
                        global_ids.setdefault(global_id, (group_identity, context))

        fields = ("stop_id", "stop_name", "stop_code", "stop_lat", "stop_lon", "location_type", "parent_station", "stop_access")
        self.insert_with_configuration(
            database, "stops", fields, (values for values, _ in results.values()), defaults, overrides
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
        
        LOGGER.info("transformed %d DINO stations and boarding stops", len(results))
