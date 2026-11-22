# DINO 2.1.2 to GTFS Static: General Mapping Specification

**Direction:** GTFS destination first, DINO source second. 
**Sources:** MENTZ *DINO Exchange Format V2.1.2* (29 September 2020, supplied 95-page PDF); [GTFS Schedule Reference](https://gtfs.org/documentation/schedule/reference/) (revised 27 April 2026); [google/transit PR #638](https://github.com/google/transit/pull/638) (Notices proposal). The three Notices files are **normative in this project's target profile** even though the upstream PR was still open when checked. Pin a revision for implementation. The output contains no non-GTFS sidecars or DINO metadata files.

## 1. Mapping Scope and Normative Transformation Model

This specification defines the destination GTFS data model, source provenance, transformation semantics, identity strategy, conflict handling and validation behavior for DINO 2.1.2 datasets. It is written as a declarative mapping contract. GTFS files unsupported by the source semantics remain absent.

### 1.1 Source Normalization and Temporal Scope

A complete DINO snapshot forms the input. Original physical row order remains available as source metadata for priority tie-breaking. The GTFS feed interval spans the first through last available DINO calendar dates, without inferred extra service dates. Active versions are resolved **per public line and date**, not globally and not per route variant. The greatest `version.din.PERIOD_PRIORITY` takes precedence. Equal or missing priorities resolve to the last applicable source record in physical input order. The winning version replaces all variants and trips of the affected line on the date; unrelated lines retain their independently selected versions. Irreconcilable contradictory facts are fatal errors; distinct legitimate offerings may have distinct `service_id` values.

### 1.2 Calendar Semantics

An active trip date is the intersection of its winning line-version period, the applicable day type and day-attribute membership, and any referenced `service_restriction.din` bitmap. Identical date sets share a `service_id`. `calendar.txt` represents the maximal practical regular weekly pattern; `calendar_dates.txt` contains only the exceptions necessary to preserve the exact DINO date set. The configured `agency_timezone` governs the GTFS service day. Source service-day times are **not shifted** for daylight saving time.

### 1.3 Stops, Times and Passenger Eligibility

`stop.din` represents the GTFS station (`location_type=1`), `stop_area.din` represents an intermediate DINO grouping, and `stop_point.din` represents the GTFS boarding stop (`location_type=0`). Boarding stops reference their GTFS station directly because GTFS does not permit the full DINO station > area > platform parent chain. WGS84 is the target CRS; source CRS transformation is configurable, with a configurable source-CRS default when metadata is absent. Missing source coordinates (`-1` or blank) result in the explicitly agreed output coordinates `0,0`, accompanied by a diagnostic warning.

`stops.stop_name` uses the DINO **name including locality**, primarily `stop.din.STOP_NAME`; this field is defined in DINO as the stop name including the place/city. `stops.stop_code` uses `stop.din.STOP_SHORTNAME` for station rows and the documented `stop_point.din.STOPPING_POINT_SHORTNAME` for platform rows when the source value is a meaningful short public identifier. Platform `stop_name` inherits the station name including locality unless a distinct, verified locality-inclusive public platform name is available. `stop_point.din.STREET_ACCESS` maps only to `stops.stop_access`: 0 > 0, documented nonzero > 1. It has no bearing on wheelchair accessibility.

DINO route positions of type `-1` (pass-through) and `5` (operational/non-passenger stop) have no `stop_times.txt` row; elapsed travel/dwell effects remain in the time calculation. Types 0, 1, 2, 3 and 4 yield pickup/dropoff 0/0, 3/3, 1/0, 0/1 and 0/0 respectively. Type 1 is `MUST_ARRANGE_WITH_DRIVER`. Trip-specific dwell values in `trip_stop_time.din` supersede pattern dwell values. Stop-specific destination text may produce `stop_times.stop_headsign`. Original stop order is retained, with GTFS sequences renumbered after omissions.

### 1.4 Routes, Connections and Ancillary Data

GTFS routes group compatible DINO lines by public line, assigned operator and externally mapped mode. Operator differences create distinct routes. Source variants and directions are represented primarily through trips, stop times and shapes. `connection.din` confirmed same-vehicle through connections correspond to `transfers.txt.transfer_type=4`; reboarding type 5 is reserved for explicitly supported source semantics. Static interchange definitions and expanded area-to-area walking connections correspond to GTFS transfers where valid. Each directed DINO area-to-area footpath expands to every applicable origin-platform/destination-platform pair. DINO fare-zone membership maps to Fares v2 `areas.txt` and `stop_areas.txt`, with overlapping memberships preserved. Network assignments, booking rules, operational vehicle blocks and non-passenger operational content are outside this target profile.

`notices.txt`, `notice_assignments.txt` and `trip_segments.txt` are treated as normative **within this project's GTFS target profile**, based on PR #638. The upstream proposal's final accepted state is not presumed. Notice text is deterministically truncated only when necessary for the pinned target schema. No notice translation is inferred. 

Bicycle rules may result in separate trip instances and date sets when genuinely date-dependent; wheelchair support remains unknown absent independent evidence.

### 1.5 Validation and Failure Semantics

**Every collision is fatal.** Any duplicate GTFS primary identifier across distinct entities, duplicate composite row key, conflicting source identity, conflicting global ID, conflicting template output, incompatible split identity or other uniqueness collision causes immediate conversion failure with a diagnostic containing the GTFS table, generated value, DINO source file and source row(s). No suffixing, renaming, last-write-wins resolution or automatic collision recovery applies to IDs. The separate **calendar priority tie-break** is not an ID-collision policy: it applies only to competing applicable version records and remains last-source-record-wins. Missing mandatory ID-template placeholders are configuration/data errors. Valid repeated references to the *same* canonical entity are deduplicated before output and are not considered collisions.

## 2. Source File Format and Ingestion Contract

- DINO is a set of named `.din` relation files. The first record gives column names; values are delimited, normally by semicolons. Quoted fields may contain delimiters. The exact export dialect must be honored, rather than applying a naive string split. Parse by **header names**, not column position; optional fields and optional relations may be absent. Preserve original file and record ordering for the last-wins priority rule.
- `character_set.din` declares character encoding when present; otherwise use an externally configured input encoding. Normalize GTFS output to UTF-8, with correct GTFS CSV escaping. Parse DINO dates as `YYYYMMDD` and seconds as numeric quantities. Respect composite keys and `VERSION` joins.
- `service_restriction.din.RESTRICTION_DAYS` consists of eight hexadecimal digits per month. The first month is that of `DATE_FROM`, bit 0 represents day 1, bit 1 day 2, etc.; use the declared `DATE_FROM`–`DATE_UNTIL` bounds. In the supplied sample: 4,330 rows across 37 versions, all bitmap lengths and calendar-day bits validated. Example `aA` (version 57, line 3438) operates on 2026-10-24 and 2026-10-31; `aC` on 2026-10-25 and 2026-11-01; `aF` on 2027-01-02. These are regression fixtures, not generic restrictions.
- `coordsys.din` metadata selects a registered input-CRS transformation. The output CRS is WGS84 (EPSG:4326). An external default source CRS is used only when metadata is missing; missing coordinate values use the explicitly agreed `0/0` output rule.
- DINO incremental/delta deliveries must be reconstructed into a complete consistent snapshot before conversion; an isolated delta is not necessarily a full GTFS feed.

## 3. Deterministic Calendar Algorithm

For every date `d` between the earliest and latest supplied calendar dates and for every public line key `L`:

1. Collect versions containing line `L` with `PERIOD_DATE_FROM <= d <= PERIOD_DATE_TO` (use the actual calendar range if a bound is absent and source data permits it).
2. Pick the greatest documented `PERIOD_PRIORITY`; on ties or missing priority select the **last read applicable version record**. Treat the selected version as replacing the entire public line on date `d`, not merely a variant. Other lines are evaluated independently.
3. Find exactly one `day_type_calendar.din` `DAY_TYPE_NR` for `(VERSION,d)` after source-order tie resolution. Resolve memberships through `day_type_2_day_attribute.din`. A trip is eligible only if its `DAY_ATTRIBUTE_NR` is among the memberships.
4. If a trip references a matching restriction, intersect its eligible dates with the set bits of `service_restriction.din.RESTRICTION_DAYS`, respecting line/version/date keys. A restriction does not add a date absent from the day-attribute set.
5. If two competing source rows can be represented as **separate legitimate trips with different active-date sets**, assign different service IDs. If the same source identity on the same date has contradictory irreconcilable route/timing/ownership facts, report an error and stop; never silently union incompatible trips.
6. Group identical active-date sets into services. Choose a weekly `calendar.txt` baseline and minimal necessary `calendar_dates.txt` exceptions; calculate the weekday mask from the actual dates, not from `DAY_TYPE_NR` names. Preserve exact service membership and the entire selected feed period.

**Important:** DINO specifies period priority, but **line-scoped replacement and last-record tie-breaking are project decisions**, not explicit DINO standard mandates. Their behavior must be tested against overlapping real exports.

## 4. GTFS Identifier and Row-Key Mapping

### 4.1 Configurable Identity Modes

Two **equal-status alternatives** exist for source objects with a DINO `GLOBAL_ID`: `global` and `template`. Neither is a fallback for the other. A configured per-entity `id_mode` selects the mode before processing. Entities without a source global ID always use a configured template or a deterministic source-derived identity expression. The default mode for eligible objects is `global`, subject to the GTFS entity's actual granularity. Where one DINO object maps to multiple GTFS entities (e.g., different operators or date-dependent trip splits), a composite GTFS identity expression remains necessary even in `global` mode. This is a defined mapping expression, **not collision recovery**.

A placeholder such as `{VERSION}` refers to the exact DINO column name, with table scope available as `{trip.VERSION}`, `{line.OP_CODE}` and so forth. Computed, explicitly defined context placeholders include `{gtfs.route_type}`, `{gtfs.service_fingerprint}`, `{gtfs.shape_fingerprint}`, `{gtfs.split_key}`, `{gtfs.from_stop_sequence}`, `{gtfs.to_stop_sequence}`, `{gtfs.transfer_type}` and `{gtfs.row_hash}`. A computed placeholder is not represented as a literal DINO field. Encoding and escaping are deterministic and injective (e.g., length-prefixed components); simple unescaped colon concatenation is not guaranteed to be collision-free. Every output key is validated for uniqueness. Global IDs are namespace-qualified where different GTFS entity types might share the same raw value.

The templates below are **configurable defaults**. Where a DINO source relation does not provide an identifier for the GTFS entity, the default is a canonical content-derived identifier over the explicitly listed inputs. Missing required values are errors rather than invitations to silently select another ID mode. The `VERSION` placeholder remains part of local DINO source keys when its scope is necessary, even if equivalent GTFS entities are subsequently canonicalized across versions.

### 4.2 Default identifiers and composite keys, by GTFS destination table

| GTFS file | Identifier or composite key | Global-ID expression, where supported | Default configurable template / source provenance |
|---|---|---|---|
| `agency.txt` | `agency_id` | Not documented | `agency:{operator.OP_CODE}`; operator.din `OP_CODE`, external agency assignment |
| `stops.txt` station | `stop_id` | `station:{stop.GLOBAL_ID}` | `station:{stop.VERSION}:{stop.STOP_NR}` |
| `stops.txt` boarding stop | `stop_id` | `platform:{stop_point.GLOBAL_ID}` | `platform:{stop_point.VERSION}:{stop_point.STOP_NR}:{stop_point.STOP_AREA_NR}:{stop_point.STOPPING_POINT_NR}` |
| `routes.txt` | `route_id` | `route:{line.GLOBAL_ID}:{line.OP_CODE}:{gtfs.route_type}` | `route:{line.LINE_NR}:{line.OP_CODE}:{gtfs.route_type}`; variant dimensions only if required by route-level semantics |
| `trips.txt` | `trip_id` | `trip:{trip.GLOBAL_ID}:{gtfs.split_key}` | `trip:{trip.VERSION}:{trip.LINE_NR}:{trip.STR_LINE_VAR}:{trip.LINE_DIR_NR}:{trip.TRIP_ID}:{gtfs.split_key}` |
| `stop_times.txt` | (`trip_id`,`stop_sequence`) | No row-level global ID | `{gtfs.trip_id}:{gtfs.stop_sequence}`; source `route.LINE_CONSEC_NR`, remapped after omitted stops |
| `calendar.txt` | `service_id` | Not documented | `service:{gtfs.service_fingerprint}` from exact active-date set |
| `calendar_dates.txt` | (`service_id`,`date`) | Not documented | `{gtfs.service_id}:{day_type_calendar.DAY}` or resolved exception date |
| `shapes.txt` | (`shape_id`,`shape_pt_sequence`) | Not documented | `shape:{gtfs.shape_fingerprint}` plus source `link_geometry.LINK_CONSEC_PT_NR` / canonical point sequence |
| `frequencies.txt` | (`trip_id`,`start_time`) | Not documented | `{gtfs.trip_id}:{gtfs.start_time}`; absent unless a proven source frequency exists |
| `transfers.txt` | Composite GTFS transfer identity | Not documented | `transfer:{gtfs.from_stop_id}:{gtfs.to_stop_id}:{gtfs.from_trip_id}:{gtfs.to_trip_id}:{gtfs.transfer_type}`; source `stop_footpath` and `connection` / `interchange` keys |
| `pathways.txt` | `pathway_id` | Not documented | `pathway:{stop_footpath.VERSION}:{stop_footpath.ORIG_STOP_NR}:{stop_footpath.ORIG_STOP_AREA_NR}:{stop_footpath.DEST_STOP_NR}:{stop_footpath.DEST_STOP_AREA_NR}:{gtfs.row_hash}`; only if GTFS endpoints valid |
| `levels.txt` | `level_id` | Not documented | `level:{stop_area.VERSION}:{stop_area.STOP_NR}:{stop_area.STOP_AREA_LEVEL}`; conditional on verified physical-floor semantics |
| `areas.txt` | `area_id` | Not documented | `farearea:{fare_zone.VERSION}:{fare_zone.FARE_ZONE_NR}` |
| `stop_areas.txt` | (`area_id`,`stop_id`) | Not documented | `{gtfs.area_id}:{gtfs.stop_id}`; source `stop.FARE_ZONE1_NR` … `FARE_ZONE6_NR` and verified zone memberships |
| `networks.txt` | `network_id` | Not applicable, excluded | No output |
| `route_networks.txt` | (`route_id`,`network_id`) | Not applicable, excluded | No output |
| `fare_attributes.txt` | `fare_id` | Not applicable without fares | No output |
| `fare_rules.txt` | Composite fare-rule row | Not applicable without fares | No output |
| `fare_products.txt` | `fare_product_id` | Not applicable without fares | No output |
| `fare_leg_rules.txt` | `leg_group_id` / rule row identity | Not applicable without fares | No output |
| `fare_transfer_rules.txt` | Composite rule row | Not applicable without fares | No output |
| `fare_leg_join_rules.txt` | Composite rule row | Not applicable without fares | No output |
| `rider_categories.txt` | `rider_category_id` | Not applicable without fares | No output |
| `fare_media.txt` | `fare_media_id` | Not applicable without fares | No output |
| `timeframes.txt` | `timeframe_group_id` / composite row | Not applicable without proven timeframe fares | No output |
| `location_groups.txt` | `location_group_id` | Not applicable, Flex excluded | No output |
| `location_group_stops.txt` | (`location_group_id`,`stop_id`) | Not applicable, Flex excluded | No output |
| `locations.geojson` | Feature `id` | Not applicable, Flex excluded | No output |
| `booking_rules.txt` | `booking_rule_id` | Not applicable, excluded | No output |
| `translations.txt` | (`table_name`,`field_name`,`language`,`record_id`/`field_value`) | Not documented | Composite GTFS translation row key; emitted only with verified language-tagged source translations |
| `feed_info.txt` | Single publisher/feed record | Not applicable | Single configured feed publisher identity; no `feed_id` is inferred unless externally supplied |
| `attributions.txt` | `attribution_id` if used | Not documented | `attribution:{operator.OP_CODE}:{gtfs.row_hash}`; only when legally supported role exists |
| `notices.txt` | `notice_id` | Not documented | `notice:{notice.VERSION}:{notice.NOTICE}` or a verified notice_str key; source schema key is validated against the actual DINO header |
| `notice_assignments.txt` | (`notice_id`,`table_name`,`record_id`) | Not documented | `{gtfs.notice_id}:{gtfs.table_name}:{gtfs.record_id}` |
| `trip_segments.txt` | `trip_segment_id` | Not documented | `segment:{gtfs.trip_id}:{gtfs.from_stop_sequence}:{gtfs.to_stop_sequence}` |

**Template validation:** `stop_point.STOPPING_POINT_NR`, `stop_area.STOP_AREA_NR`, `line.LINE_NR`, `trip.TRIP_ID` and other source components retain their DINO-defined composite scope. A global ID is only used directly when it identifies precisely one output object. A source global trip ID that is unique only per calendar date is not assumed to be feed-global. Its defined identity expression incorporates a service/split dimension when needed; any remaining collision is fatal.

## 5. Detailed GTFS Destination-Field Mapping

The following section covers each GTFS destination and source candidate. Legend: **D** direct, **R** deterministic, **C** only when semantics/data are proven, **E** external configuration, **O** deliberately omitted. The binding decisions in Sections 1–4 take precedence over any generic conditional wording in the field inventory.

## Detailed GTFS Field-Level Mapping
Every row below is expressed as **GTFS field > DINO origin > conversion/constraints**. Optional fields absent from the list must be omitted unless a supported DINO field and verified rule is added. The companion source inventory further below is not a substitute for these transformation rules.

### `agency.txt`

| GTFS field(s) | DINO source / provenance | Transformation and guard |
|---|---|---|
| `agency_id` | operator.din OP_Code, referenced by line.din OP_Code | R/E: agency for each line is its operator; use external operator mapping when configured |
| `agency_name` | operator.din: operator display/name fields | D/C: passenger-facing name |
| `agency_url` | authoritative feed/operator configuration | E: required, never fabricate |
| `agency_timezone` | externally configured IANA timezone | E: no DST-specific shifting of DINO service-time offsets |
| `agency_lang` | configuration / verified operator language | E/C |
| `agency_phone, agency_email, agency_fare_url` | operator.din / operator_branch_office.din, if matching contact fields exist | C: only public contact endpoints |

### `stops.txt`

| GTFS field(s) | DINO source / provenance | Transformation and guard |
|---|---|---|
| `stop_id` | `stop.din.GLOBAL_ID` / `stop_point.din.GLOBAL_ID` or corresponding DINO composite keys | R: per-object **global or template mode**, selected by configuration; all collisions are fatal; see Section 4 |
| `stop_name` | `stop.din.STOP_NAME` (explicitly including locality) | D/R: station and associated platform rows use the DINO stop name with locality; a distinct verified locality-inclusive platform name may supersede the inherited station name |
| `stop_code` | `stop.din.STOP_SHORTNAME` for station rows; `stop_point.din.STOPPING_POINT_SHORTNAME` for platform rows | D/C: DINO shortname used as the public stop code when present; platform shortname is documented as a public stopping-point name, so the code interpretation is a project convention |
| `stop_lat, stop_lon` | stop.din / stop_area.din / stop_point.din position X/Y + coordsys.din | R: WGS84 (EPSG:4326) transformation; when X or Y is missing (-1/blank), output latitude=0 and longitude=0 per project policy, explicitly an artificial placeholder; when both coordinates exist use configured CRS transformer |
| `location_type` | DINO stop, stop_area, stop_point hierarchy | R/C: stop.din = 1 station by project policy; stop_point.din = 0 stop/platform; stop_area.din requires GTFS-compliant representation, not automatically location_type=4 |
| `parent_station` | stop_point.din STOP_NR and STOP_AREA_NR; stop_area.din STOP_NR | R/C: GTFS parent_station must reference a location_type=1 station; never chain stop_point through a location_type=4 boarding area as parent_station |
| `platform_code` | stop_point.din STOPPING_POINT_SHORTNAME | C: extract only actual platform code |
| `zone_id` | fare_zone.din stop membership | C: only one semantically valid zone; multi-zone use areas/stop_areas |
| `wheelchair_boarding` | stop_attribute.din, stop_area_attribute.din, stop_point_attribute.din + attribute.din | C: only documented accessibility attribute codes |
| `level_id` | `stop_area.din.STOP_AREA_LEVEL` (documented as level information) | C: available only where a verified physical-floor interpretation and a valid levels.txt entity exist; no automatic assumption that DINO level numbers are GTFS floor indices |
| `stop_desc, tts_stop_name, stop_url` | stop_additional_name.din / verified descriptive attributes | C: descriptive name != TTS automatically |

### `routes.txt`

| GTFS field(s) | DINO source / provenance | Transformation and guard |
|---|---|---|
| `route_id` | line.din GLOBAL_ID when unique at chosen GTFS route granularity; otherwise LINE_NR and external ID pattern | R: configurable global/template identity; variants/directions represented in trips and stop_times; every collision is fatal |
| `agency_id` | line.din OP_Code + operator.din OP_Code | R/E: line-assigned operator is GTFS agency; external operator mapping resolves overrides and missing codes |
| `route_short_name` | line.din LINE_NAME | D/C: public line designation |
| `route_long_name` | line.din / route.din endpoints | R/C: only if distinct meaningful long name |
| `route_type` | line.din MOT_NR / branch.din / means_of_transport_desc.din / train_category.din | R: verified DINO mode-to-GTFS enum lookup |
| `route_color, route_text_color` | line_attribute.din + attribute.din, if documented color codes | C: only genuine colors |
| `route_desc, route_url, route_sort_order` | line.din / verified attributes or config | C/E: no invented metadata |
| `network_id` | none | O: network assignments excluded by project decision |

### `trips.txt`

| GTFS field(s) | DINO source / provenance | Transformation and guard |
|---|---|---|
| `trip_id` | trip.din GLOBAL_ID; otherwise VERSION, LINE_NR, TRIP_ID and period/date collision dimensions | R: configurable global/template identity; collision is fatal |
| `route_id` | trip.din LINE_NR and line/route variant | R: map to consolidated GTFS route |
| `service_id` | trip.din DAY_ATTRIBUTE_NR, RESTRICTION; day_type_calendar.din; day_type_2_day_attribute.din; service_restriction.din; version.din | R: exact evaluated active-date set |
| `trip_headsign` | trip_vdt.din + vehicle_destination_text.din | C: resolve destination text for trip |
| `trip_short_name` | trip.din TRIP_ID_PRINTING | C: only public trip number |
| `direction_id` | trip.din LINE_DIR_NR / route.din LINE_DIR_NR | R: stable 0/1 mapping, do not copy 1/2 raw |
| `block_id` | vehicle_block.din | O: deliberately omitted; operational blocks are excluded by project policy |
| `shape_id` | link.din, link_geometry.din, link_force_point.din, route.din | C: only after validated geometry build |
| `wheelchair_accessible, bikes_allowed` | trip.din BIKE_ALLOWED; line.din BIKE_RULE; vehicle_type.din and stop/line attributes if explicit | C: deterministic trip/date-specific value; split source trip into GTFS trip instances with disjoint service calendars when values differ by service day |

### `stop_times.txt`

| GTFS field(s) | DINO source / provenance | Transformation and guard |
|---|---|---|
| `trip_id` | trip.din TRIP_ID and composite keys | R: foreign key |
| `stop_sequence` | route.din LINE_CONSEC_NR | R: stable order, resolve repeated stops |
| `stop_id` | route.din STOP_NR, STOPPING_POINT_NR + stop_point.din | R: use point when resolved; when STOPPING_POINT_NR=0 and no point record exists, DINO explicitly falls back to stop.din |
| `arrival_time, departure_time` | trip.din DEPARTURE_TIME; timing_pattern.din TT_REL (seconds from previous stop) and STOPPING_TIME; trip_stop_time.din STOPPING_TIME | R: trip_stop_time overrides dwell time unconditionally for matching trip/sequence; TT_REL=-1 pass-through requires explicit handling; no modulo-24 or DST shift |
| `pickup_type, drop_off_type` | route.din STOPPING_POINT_TYPE; service_constraint.din SERVICE_INTERDICTION_CODE | R/C: type -1 pass-through OMIT stop_time; 0 normal 0/0; 1 request stop MUST_ARRANGE_WITH_DRIVER = 3/3 (unless explicit boarding/alighting prohibition overrides one side); 2 no boarding 1/0; 3 no alighting 0/1; 5 no passenger service OMIT stop_time; code A alighting-only 1/0, E boarding-only 0/1; type 4 and code I/numeric local-origin-destination bans cannot be represented exactly by local flags; see U19 options below |
| `stop_headsign` | trip_vdt.din LINE_CONSEC_NR, VDT_NR + vehicle_destination_text.din | R: use stop/segment-specific destination overrides; apply from the specified sequence according to source scope |
| `timepoint` | timing_pattern.din / trip_stop_time.din | C: only explicit timing-point semantics; do not infer from dwell |
| `shape_dist_traveled` | link_geometry.din + resolved shape | R/C: cumulative projected geodesic distance along shape |
| `continuous_pickup, continuous_drop_off` | no verified DINO semantics | N |
| `pickup_booking_rule_id, drop_off_booking_rule_id` | none | O: booking rules explicitly excluded |

### `calendar.txt`

| GTFS field(s) | DINO source / provenance | Transformation and guard |
|---|---|---|
| `service_id` | evaluated DINO date-set equivalence class | R: maximize calendar.txt coverage; group equal service patterns |
| `monday..sunday, start_date, end_date` | day_type_calendar.din, day_type_2_day_attribute.din, day_attribute.din, service_restriction.din, version.din | R: optimize exact service-date sets for minimal calendar_dates exceptions, within global min/max feed dates |

### `calendar_dates.txt`

| GTFS field(s) | DINO source / provenance | Transformation and guard |
|---|---|---|
| `service_id` | evaluated trip service-date sets | R |
| `date` | day_type_calendar.din DAY + version.din timetable period + restrictions | R: only exception dates relative to calendar.txt weekly patterns |
| `exception_type` | evaluated active dates | R: 1 added service / 2 removed service relative to chosen calendar.txt pattern |

### `shapes.txt`

| GTFS field(s) | DINO source / provenance | Transformation and guard |
|---|---|---|
| `shape_id` | route.din path identity + link.din references | R |
| `shape_pt_lat, shape_pt_lon` | link_geometry.din, link_force_point.din + coordsys.din | R: correctly ordered WGS84 vertices |
| `shape_pt_sequence` | link geometry vertex order | R: monotonically increasing |
| `shape_dist_traveled` | link geometry path length | R/C: cumulative distance |

### `frequencies.txt`

| GTFS field(s) | DINO source / provenance | Transformation and guard |
|---|---|---|
| `trip_id, start_time, end_time, headway_secs, exact_times` | trip.din scheduled departures | O: scheduled trips retained individually, no frequency compression |

### `transfers.txt`

| GTFS field(s) | DINO source / provenance | Transformation and guard |
|---|---|---|
| `from_stop_id, to_stop_id` | stop_footpath.din; transfer_matrix.din; interchange_definition.din | R/C: translate exact stop/station references |
| `transfer_type` | transfer_matrix.din / interchange_definition.din SECURED_INTERCHANGE / interchange_validity.din | C: only static verified minimum/forbidden/recommended transfer semantics; do not publish dynamic guarantees |
| `min_transfer_time` | stop_footpath.din walking time; transfer_matrix.din transfer time | C: integer seconds, only when semantically minimum |
| `from_route_id, to_route_id, from_trip_id, to_trip_id` | interchange_definition.din + interchange_validity.din | C: trip-specific constraints only when resolvable |
| `other fields including linked-trip fields` | connection.din + vehicle_block.din | C: connection.din same-vehicle through-services -> transfer_type=4, with from_trip_id and to_trip_id required; GTFS transfer_type=5 only for explicit same-vehicle reboarding, never inferred; omit operational blocks |

### `pathways.txt`

| GTFS field(s) | DINO source / provenance | Transformation and guard |
|---|---|---|
| `pathway_id` | stop_footpath.din composite link key | R/C: stable per directed internal station link |
| `from_stop_id, to_stop_id` | stop_footpath.din ORIG_STOP_NR, ORIG_STOP_AREA_NR, DEST_STOP_NR, DEST_STOP_AREA_NR | C: DINO endpoints are stop AREAS, not platform points; require valid GTFS nodes and a complete navigable graph, otherwise map as transfers |
| `pathway_mode` | stop_footpath.din PROPERTY | R/C: 1 level walkway→1, 2 stairs→2, 3 escalator→4, 4 elevator→5, 5 ramp→1 with max_slope only if known; 6 unlit→1; 7 blocked→omit; 8 unattributed→1 only when verified; GTFS pathways cannot express all attributes |
| `is_bidirectional` | stop_footpath.din directional pair | C: only if traversability confirmed in both directions |
| `length, traversal_time` | stop_footpath.din distance / walking duration | C: units and measurement semantics verified |
| `stair_count, max_slope, min_width, signposted_as, reversed_signposted_as` | no reliably established DINO fields | N/S |

### `levels.txt`

| GTFS field(s) | DINO source / provenance | Transformation and guard |
|---|---|---|
| `level_id` | verified physical floor identifier from station attributes | C: no documented universal floor table |
| `level_index` | verified physical vertical ordering | C: numeric floor order only, not stop_area index |
| `level_name` | verified passenger-facing floor name | C: only if provided |

### `areas.txt`

| GTFS field(s) | DINO source / provenance | Transformation and guard |
|---|---|---|
| `area_id` | fare_zone.din zone identifier | R/C: stable tariff zone ID |
| `area_name` | fare_zone.din fare zone name | D/C: human-facing area name |

### `stop_areas.txt`

| GTFS field(s) | DINO source / provenance | Transformation and guard |
|---|---|---|
| `area_id` | fare_zone.din zone ID | R/C: must reference areas.txt |
| `stop_id` | fare zone membership resolved through stop.din / stop_point.din | R/C: include all valid members, preserve overlaps; NOT a copy of DINO stop_area.din |

### `networks.txt` (excluded by project decision)

| GTFS field(s) | DINO source / provenance | Transformation and guard |
|---|---|---|
| `network_id` | branch.din BRANCH_NR / line.din BRANCH_NR / operator.din | C: only proven passenger fare network identity |
| `network_name` | branch.din / operator.din public network name | C: no branch-as-network assumption |

### `route_networks.txt` (excluded by project decision)

| GTFS field(s) | DINO source / provenance | Transformation and guard |
|---|---|---|
| `route_id` | routes.txt derived route ID | R |
| `network_id` | verified line-to-network association | C: supports multiple memberships; forbidden when routes.network_id exists |

### `fare_attributes.txt`

| GTFS field(s) | DINO source / provenance | Transformation and guard |
|---|---|---|
| `fare_id, price, currency_type, payment_method, transfers, agency_id, transfer_duration` | fare_zone.din and transition tables contain topology, not proven price/product data | N: require external tariff dataset |

### `fare_rules.txt`

| GTFS field(s) | DINO source / provenance | Transformation and guard |
|---|---|---|
| `fare_id, route_id, origin_id, destination_id, contains_id` | fare_zone.din, fare_zone_transition.din, neighbour_fare_zone.din | C/E: require priced fare_id and validated zone fare semantics |

### `fare_products.txt`

| GTFS field(s) | DINO source / provenance | Transformation and guard |
|---|---|---|
| `fare_product_id, fare_product_name, amount, currency, rider_category_id, fare_media_id` | no priced product in documented DINO zone topology | N/E: external fare catalog |

### `fare_leg_rules.txt`

| GTFS field(s) | DINO source / provenance | Transformation and guard |
|---|---|---|
| `leg_group_id, network_id, from_area_id, to_area_id, fare_product_id, rule_priority` | fare_zone.din, fare_zone_transition.din and route_networks | C/E: only after tariff products and leg-pricing semantics provided |

### `fare_transfer_rules.txt`

| GTFS field(s) | DINO source / provenance | Transformation and guard |
|---|---|---|
| `from_leg_group_id, to_leg_group_id, transfer_count, duration_limit, fare_transfer_type, fare_product_id` | fare_zone transitions and neighbour zones | N/E: adjacency does not define transfer pricing |

### `fare_leg_join_rules.txt`

| GTFS field(s) | DINO source / provenance | Transformation and guard |
|---|---|---|
| `from_network_id, to_network_id, from_stop_id, to_stop_id` | DINO route/stop topology | C/E: only with actual tariff leg-joining policy |

### `rider_categories.txt`

| GTFS field(s) | DINO source / provenance | Transformation and guard |
|---|---|---|
| `rider_category_id, rider_category_name, is_default_fare_category, eligibility_url` | no explicit rider pricing classes identified | N/E |

### `fare_media.txt`

| GTFS field(s) | DINO source / provenance | Transformation and guard |
|---|---|---|
| `fare_media_id, fare_media_name, fare_media_type` | no documented fare media source | N/E |

### `timeframes.txt`

| GTFS field(s) | DINO source / provenance | Transformation and guard |
|---|---|---|
| `timeframe_group_id, start_time, end_time, service_id` | calendar data supplies service days, not tariff timeframes | N/E |

### `location_groups.txt`

| GTFS field(s) | DINO source / provenance | Transformation and guard |
|---|---|---|
| `location_group_id, location_group_name` | stop.din / stop_area.din grouping | C: only actual request-pickup group, not every stop area |

### `location_group_stops.txt`

| GTFS field(s) | DINO source / provenance | Transformation and guard |
|---|---|---|
| `location_group_id, stop_id` | verified demand-responsive group membership | C: requires explicit pickup/dropoff group semantics |

### `locations.geojson`

| GTFS field(s) | DINO source / provenance | Transformation and guard |
|---|---|---|
| `Feature.id, geometry, properties` | stop.din / fare_zone.din geometry is not necessarily a demand-responsive polygon | N/C: only actual eligible service-zone polygon geometry |

### `booking_rules.txt`

| GTFS field(s) | DINO source / provenance | Transformation and guard |
|---|---|---|
| `booking_rule_id, booking_type, prior_notice_duration_min, prior_notice_duration_max, phone_number, booking_url, info_url, message` | service_constraint.din / route.din request flags plus external booking policy | C/E: request-stop flag is insufficient to populate booking details |

### `translations.txt`

| GTFS field(s) | DINO source / provenance | Transformation and guard |
|---|---|---|
| `table_name, field_name, language, translation, record_id, record_sub_id, field_value` | stop_additional_name.din, stop_alias_placename.din, notice_str.din | C: only explicitly language-tagged translations; aliases are not translations |

### `feed_info.txt`

| GTFS field(s) | DINO source / provenance | Transformation and guard |
|---|---|---|
| `feed_publisher_name, feed_publisher_url, feed_lang` | authoritative feed publisher configuration | E: required metadata when feed_info emitted |
| `feed_start_date, feed_end_date` | version.din period dates | R/C: actual covered service range |
| `feed_version` | version.din VERSION, VERSION_TEXT, TIMETABLE_PERIOD | R/C: stable version label |

### `attributions.txt`

| GTFS field(s) | DINO source / provenance | Transformation and guard |
|---|---|---|
| `attribution_id, agency_id, route_id, trip_id, organization_name, is_producer, is_operator, is_authority, attribution_url, attribution_email, attribution_phone` | operator.din, operator_branch_office.din, branch.din | C: only legally/factually supported role and contact |

### `notices.txt [PR #638]`

| GTFS field(s) | DINO source / provenance | Transformation and guard |
|---|---|---|
| `notice_id` | notice.din NOTICE (and VERSION, LINE_NR scope as necessary) / notice_str.din HINW_STR_CODE | R: stable notice identity |
| `display_text` | notice.din NOTICE_TEXT resolved via notice_str.din HINW_STR_CODE | D/C: plain text; retain up to actual binding maximum in target PR; if normative limit exceeded truncate at Unicode-safe boundary and log warning; do not replace machine-readable GTFS data |

### `notice_assignments.txt [PR #638 target profile]`

| GTFS field(s) | DINO source / provenance | Transformation and guard |
|---|---|---|
| `notice_id` | notice.din / notice_str.din resolved notice key | R: foreign key |
| `table_name` | trip.din notice references; notice_str.din LINE_NR, TRIP_ID, LINE_CONSEC_NR and stop identifiers | R/C: one of routes, trips, trip_segments |
| `record_id` | GTFS route_id, trip_id or trip_segment_id | R: target identifier; preserve assignment scope and conditions |

### `trip_segments.txt [PR #638]`

| GTFS field(s) | DINO source / provenance | Transformation and guard |
|---|---|---|
| `trip_segment_id` | notice scope resolved from DINO trip-stop/route-sequence applicability | R/C: only when actual bounded notice scope exists |
| `trip_id` | trip.din trip identity | R: GTFS trip_id |
| `from_stop_sequence, to_stop_sequence` | route.din LINE_CONSEC_NR / stop_times.txt sequence after conversion | R/C: inclusive valid GTFS stop sequences; do not invent ranges |


## 6. DINO Source Relation Coverage

The inventory below is for **source ingestion and join planning**. Field names are extracted from the PDF text and some longer names were truncated by the PDF layout. **Verify those names against the actual header before implementation.** The GTFS field tables in Section 5, not this source inventory, define what may be exported.

| DINO relation | Candidate columns visible in the PDF extraction |
|---|---|
| `character_set.din` | none automatically detected; inspect the PDF table manually |
| `version.din` | `VERSION`, `VERSION_TEXT`, `TIMETABLE_PERIOD`, `TT_PERIOD_NAME`, `PERIOD_`, `PERIOD_DATE_TO`, `NET_ID`, `PERIOD_PRIORITY` |
| `day_type_calendar.din` | `VERSION`, `DAY`, `DAY_TEXT`, `DAY_TYPE_NR` |
| `day_type.din` | `VERSION`, `DAY_TYPE_NR` |
| `day_type_2_day_attribute.din` | `VERSION`, `DAY_TYPE_NR`, `DAY_ATTRIBUTE_NR` |
| `day_attribute.din` | `VERSION`, `DAY_ATTRIBUTE_NR`, `DAY_ATTRIBUTE_TEXT`, `STR_DAY_ATTRIBUTE` |
| `service_restriction.din` | `VERSION`, `RESTRICTION`, `RESTRICT_TEXT1`, `RESTRICT_TEXT2`, `RESTRICT_TEXT3`, `RESTRICT_TEXT4`, `RESTRICT_TEXT5`, `RESTRICTION_DAYS`, `DATE_FROM`, `DATE_UNTIL`, `LINE_NR` |
| `stop.din` | `VERSION`, `STOP_NR`, `STOP_TYPE`, `STOP_NAME`, `STOP_NAME_WITHOUT_LOCALITY`, `STOP_SHORTNAME`, `STOP_POS_X`, `STOP_POS_Y`, `OCC`, `FARE_ZONE1_NR`, `FARE_ZONE2_NR`, `FARE_ZONE3_NR`, `FARE_ZONE4_NR`, `FARE_ZONE5_NR`, `FARE_ZONE6_NR`, `VALID_FROM`, `VALID_TO`, `PLACE_ID`, `GIS_MOT_FLAG`, `IS_CENTRAL_STOP`, `IS_RESPONSIBLE_STOP`, `INTERCHANGE_TYPE`, `INTERCHANGE_QUALITY` |
| `stop_area.din` | `VERSION`, `STOP_NR`, `STOP_AREA_NR`, `STOP_AREA_POS_X`, `STOP_AREA_POS_Y`, `STOP_AREA_SHORT_NAME`, `STOP_AREA_LONG_NAME`, `STOP_AREA_LEVEL`, `STOP_AREA_TYPE`, `GIS_MOT_FLAG`, `VALID_FROM`, `VALID_TO` |
| `stop_point.din` | `VERSION`, `STOP_NR`, `STOP_AREA_NR`, `STOPPING_POINT_NR`, `STOPPING_POINT`, `SEGMENT_ID`, `SEGMENT_DIST`, `STOP_RBL_NR`, `STOPPING_POINT_SHORTNAME`, `PURPOSE_TTB`, `PURPOSE_STT`, `PURPOSE_JP`, `PURPOSE_CBS`, `GIS_MOT_FLAG`, `VALID_FROM`, `VALID_TO`, `PLATFORM_HEIGHT`, `DISTANCE_TO_RAIL_CEN`, `HAS_MOBILE_RAMP`, `BOARDING_SPACE`, `STREET_ACCESS` |
| `stop_footpath.din` | `VERSION`, `ORIG_STOP_NR`, `ORIG_STOP_AREA_NR`, `DEST_STOP_NR`, `DEST_STOP_AREA_NR`, `TRANSFER_TIME`, `TRANSFER_DISTANCE`, `PROPERTY`, `FIXED_TIME` |
| `stop_additional_name.din` | `VERSION`, `STOP_NR`, `ADD_STOP_NAME_W` |
| `stop_alias_placename.din` | `VERSION`, `STOP_NR`, `ALIAS_PLACE`, `ALIAS_OCC` |
| `coordsys.din` | `VERSION`, `SHORT_NAME`, `LONG_NAME`, `EPSG_CODE`, `TRANS_X`, `TRANS_Y`, `SCALE_X`, `SCALE_Y` |
| `fare_zone.din` | `VERSION`, `FARE_ZONE_NR`, `FARE_ZONE_LONG_NAME`, `FARE_ZONE_TYPE`, `FARE_ZONE_COLOR` |
| `neighbour_fare_zone.din` |  |
| `fare_zone_transition.din` |  |
| `fare_zone_transition_point.din` |  |
| `means_of_transport_desc.din` | `VERSION`, `MOT_NR`, `MOT_NAME`, `TMOT_NR`, `TMOT_NAME` |
| `transfer_matrix.din` | `VERSION`, `ORIGIN_TMOT_NR`, `DEST_TMOT_NR`, `TIME` |
| `vehicle_type.din` |  |
| `operator.din` | `VERSION`, `OP_CODE`, `OP_BRANCH_NR`, `OP_SHORT_NAME`, `OP_LONG_NAME`, `OP_PUBLIC_SHORT`, `OP_LICENCE_NAME`, `OP_TRADING_NAME`, `OP_VAT_REGISTER` |
| `operator_branch_office.din` |  |
| `depot.din` | `VERSION`, `DEPOT_NR`, `DEPOT_TEXT`, `DEPOT_ABBREV` |
| `branch.din` | `VERSION`, `BRANCH_NR` |
| `timing_pattern.din` | `VERSION`, `LINE_NR`, `STR_LINE_VAR`, `LINE_DIR_NR`, `LINE_CONSEC_NR`, `TIMING_GROUP_NR`, `TT_REL`, `STOPPING_TIME` |
| `route.din` | `VERSION`, `LINE_NR`, `STR_LINE_VAR`, `LINE_DIR_NR`, `LINE_CONSEC_NR`, `STOP_NR`, `STOPPING_POINT_NR`, `STOPPING_POINT_TYPE`, `LENGTH` |
| `trip_purpose.din` |  |
| `line.din` | `VERSION`, `BRANCH_NR`, `LINE_NR`, `STR_LINE_VAR`, `LINE_NAME`, `LINE_DIR_NR`, `LAST_MODIFIE`, `MOT_NR`, `VALID_FROM`, `VALID_TO`, `OBO_SHORT_N`, `ROUTE_TYPE`, `GLOBAL_ID`, `BIKE_RULE` |
| `vehicle_destination_text.din` | `VERSION`, `BRANCH_NR`, `VDT_NR`, `VDT_TEXT_DRIVER1`, `VDT_TEXT_DRIVER2`, `VDT_TEXT_FRONT1`, `VDT_TEXT_FRONT2`, `VDT_TEXT_FRONT3`, `VDT_TEXT_FRONT4`, `VDT_TEXT_SIDE1`, `VDT_TEXT_SIDE2`, `VDT_TEXT_SIDE3`, `VDT_TEXT_SIDE4`, `VDT_LONG_NAME`, `VDT_SHORT_NAME` |
| `trip_vdt.din` | `VERSION`, `TIMETABLE_PERIOD`, `LINE_NR`, `STR_LINE_VAR`, `LINE_DIR_NR`, `TRIP_ID`, `LINE_CONSEC_NR`, `VDT_NR` |
| `train_category.din` |  |
| `line_suppression.din` |  |
| `trip.din` | `VERSION`, `LINE_NR`, `STR_LINE_VAR`, `LINE_DIR_NR`, `TIMING_GROUP_NR`, `TRIP_ID`, `TRIP_ID_PRINTING`, `DEPARTURE_TIME`, `DEP_STOP_NR`, `DEP_STOPPING_POINT_NR`, `ARR_STOP_NR`, `ARR_STOPPING_POINT_NR`, `VEH_TYPE_NR`, `DAY_ATTRIBUTE_NR`, `RESTRICTION`, `NOTICE`, `NOTICE_2`, `NOTICE_3`, `NOTICE_4`, `NOTICE_5`, `ROUND_TRIP_NR`, `TRAIN_NR`, `TRAIN_CATEGORY_SHORT_`, `TRIP_EXT_KEY`, `OP_CODE`, `OBO_SHORT_NAME`, `GLOBAL_ID`, `BIKE_ALLOWED`, `PURPOSE_NR` |
| `trip_stop_time.din` | `VERSION`, `LINE_NR`, `TRIP_ID`, `LINE_CONSEC_NR` |
| `vehicle_block.din` | `VERSION`, `DAY_TYPE_NR`, `DEPOT_NR`, `BLOCK_NR`, `VEH_TYP_NR`, `DEP_STOP_NR`, `DEP_STOPPING_POINT_NR`, `BEGIN_OF_BLOCK`, `ARR_STOP_NR`, `ARR_STOPPING_POINT_NR`, `END_OF_BLOCK` |
| `notice.din` |  |
| `notice_str.din` |  |
| `service_constraint.din` |  |
| `connection.din` | `VERSION`, `ORIG_DAY_ATTRIBUTE_NR`, `ORIG_LINE_NR`, `ORIG_LINE_DIR_NR`, `ORIG_STOP_NR`, `ORIG_STOP_AREA_NR`, `ORIG_TIME_INTERVAL_BEGIN`, `ORIG_TIME_INTERVAL_END`, `DEST_DAY_ATTRIBUTE_NR`, `DEST_LINE_NR`, `DEST_LINE_DIR_NR`, `DEST_STOP_NR`, `DEST_STOP_AREA_NR`, `DEST_TIME_INTERVAL_BEGIN`, `DEST_TIME_INTERVAL_END`, `TRANSFER_TIME`, `TRANSFER_DISTANCE`, `CONNECTION_TYPE` |
| `interchange_definition.din` |  |
| `interchange_validity.din` |  |
| `link.din` | `VERSION`, `LINK_ID`, `BRANCH_NR`, `ORIG_STOP_NR`, `ORIG_STOP_AREA_NR`, `STOPPING_POINT_NR`, `DEST_STOP_NR`, `DEST_STOP_AREA_NR`, `DEST_STOPPING_POINT_NR`, `LENGTH`, `GIS_LENGTH` |
| `link_geometry.din` | `VERSION`, `LINK_ID`, `LINK_CONSEC_PT_NR`, `LINK_PT_X`, `LINK_PT_Y` |
| `link_force_point.din` | `VERSION`, `LINK_ID`, `LINK_CONSEC_PT_NR`, `LINK_PT_X`, `LINK_PT_Y` |
| `attribute.din` |  |
| `stop_attribute.din` |  |
| `stop_area_attribute.din` |  |
| `stop_point_attribute.din` |  |
| `line_attribute.din` |  |

## 7. Unsupported Files

- `networks.txt`, `route_networks.txt`: **never**, by project decision.
- `levels.txt`: no verified floor numbering.
- `frequencies.txt`: no proven frequency-based service; explicitly scheduled trips remain individual trips.
- `booking_rules.txt`, `location_groups.txt`, `location_group_stops.txt`, `locations.geojson`: GTFS Flex/booking excluded by decision.
- `fare_attributes.txt`, `fare_rules.txt`, `fare_products.txt`, `fare_leg_rules.txt`, `fare_transfer_rules.txt`, `fare_leg_join_rules.txt`, `fare_media.txt`, `rider_categories.txt`, `timeframes.txt`: no invented fare prices/products/rules.
- `translations.txt`: no verified language-tagged translations, and no notice translations.
- `pathways.txt`: emit only when individual physical pathway edges and GTFS-compatible endpoint types are demonstrable; ordinary area-to-area interchange walking times belong in `transfers.txt`.
- Operational data without GTFS passenger semantics: no block IDs, depots, internal line suppression or operating-purpose metadata.
