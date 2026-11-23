-- GTFS Schedule reference revised 2026-04-27; notices proposal PR #638
-- at 57663a31114cc494e4eee8742713585a3b4b6a75.
-- Optional or conditionally required values are nullable. Times and dates
-- remain text, including times beyond 24:00:00. Currency amounts use text.
-- No GTFS indexes or foreign keys are created.

CREATE TABLE gtfs_agency (
    agency_id TEXT,
    agency_name TEXT NOT NULL,
    agency_url TEXT NOT NULL,
    agency_timezone TEXT NOT NULL,
    agency_lang TEXT,
    agency_phone TEXT,
    agency_fare_url TEXT,
    agency_email TEXT,
    cemv_support INTEGER
);

CREATE TABLE gtfs_levels (
    level_id TEXT NOT NULL,
    level_index REAL NOT NULL,
    level_name TEXT
);

CREATE TABLE gtfs_stops (
    stop_id TEXT NOT NULL,
    stop_code TEXT,
    stop_name TEXT,
    tts_stop_name TEXT,
    stop_desc TEXT,
    stop_lat REAL,
    stop_lon REAL,
    zone_id TEXT,
    stop_url TEXT,
    location_type INTEGER,
    parent_station TEXT,
    stop_timezone TEXT,
    wheelchair_boarding INTEGER,
    level_id TEXT,
    platform_code TEXT,
    stop_access INTEGER
);

CREATE TABLE gtfs_routes (
    route_id TEXT NOT NULL,
    agency_id TEXT,
    route_short_name TEXT,
    route_long_name TEXT,
    route_desc TEXT,
    route_type INTEGER NOT NULL,
    route_url TEXT,
    route_color TEXT,
    route_text_color TEXT,
    route_sort_order INTEGER,
    continuous_pickup INTEGER,
    continuous_drop_off INTEGER,
    network_id TEXT,
    cemv_support INTEGER
);

CREATE TABLE gtfs_calendar (
    service_id TEXT NOT NULL,
    monday INTEGER NOT NULL,
    tuesday INTEGER NOT NULL,
    wednesday INTEGER NOT NULL,
    thursday INTEGER NOT NULL,
    friday INTEGER NOT NULL,
    saturday INTEGER NOT NULL,
    sunday INTEGER NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL
);

CREATE TABLE gtfs_calendar_dates (
    service_id TEXT NOT NULL,
    date TEXT NOT NULL,
    exception_type INTEGER NOT NULL
);

CREATE TABLE gtfs_shapes (
    shape_id TEXT NOT NULL,
    shape_pt_lat REAL NOT NULL,
    shape_pt_lon REAL NOT NULL,
    shape_pt_sequence INTEGER NOT NULL,
    shape_dist_traveled REAL
);

CREATE TABLE gtfs_trips (
    route_id TEXT NOT NULL,
    service_id TEXT NOT NULL,
    trip_id TEXT NOT NULL,
    trip_headsign TEXT,
    trip_short_name TEXT,
    direction_id INTEGER,
    block_id TEXT,
    shape_id TEXT,
    wheelchair_accessible INTEGER,
    bikes_allowed INTEGER,
    cars_allowed INTEGER,
    safe_duration_factor REAL,
    safe_duration_offset REAL
);

CREATE TABLE gtfs_location_groups (
    location_group_id TEXT NOT NULL,
    location_group_name TEXT
);

-- One row per GeoJSON Feature; properties and geometry are JSON objects.
CREATE TABLE gtfs_locations (
    id TEXT NOT NULL,
    type TEXT NOT NULL,
    properties TEXT NOT NULL,
    geometry TEXT NOT NULL
);

CREATE TABLE gtfs_booking_rules (
    booking_rule_id TEXT NOT NULL,
    booking_type INTEGER NOT NULL,
    prior_notice_duration_min INTEGER,
    prior_notice_duration_max INTEGER,
    prior_notice_last_day INTEGER,
    prior_notice_last_time TEXT,
    prior_notice_start_day INTEGER,
    prior_notice_start_time TEXT,
    prior_notice_service_id TEXT,
    message TEXT,
    pickup_message TEXT,
    drop_off_message TEXT,
    phone_number TEXT,
    info_url TEXT,
    booking_url TEXT
);

CREATE TABLE gtfs_stop_times (
    trip_id TEXT NOT NULL,
    arrival_time TEXT,
    departure_time TEXT,
    stop_id TEXT,
    location_group_id TEXT,
    location_id TEXT,
    stop_sequence INTEGER NOT NULL,
    stop_headsign TEXT,
    start_pickup_drop_off_window TEXT,
    end_pickup_drop_off_window TEXT,
    pickup_type INTEGER,
    drop_off_type INTEGER,
    continuous_pickup INTEGER,
    continuous_drop_off INTEGER,
    shape_dist_traveled REAL,
    timepoint INTEGER,
    pickup_booking_rule_id TEXT,
    drop_off_booking_rule_id TEXT
);

CREATE TABLE gtfs_fare_attributes (
    fare_id TEXT NOT NULL,
    price TEXT NOT NULL,
    currency_type TEXT NOT NULL,
    payment_method INTEGER NOT NULL,
    transfers INTEGER,
    agency_id TEXT,
    transfer_duration INTEGER
);

CREATE TABLE gtfs_fare_rules (
    fare_id TEXT NOT NULL,
    route_id TEXT,
    origin_id TEXT,
    destination_id TEXT,
    contains_id TEXT
);

CREATE TABLE gtfs_timeframes (
    timeframe_group_id TEXT NOT NULL,
    start_time TEXT,
    end_time TEXT,
    service_id TEXT NOT NULL
);

CREATE TABLE gtfs_rider_categories (
    rider_category_id TEXT NOT NULL,
    rider_category_name TEXT NOT NULL,
    is_default_fare_category INTEGER,
    eligibility_url TEXT
);

CREATE TABLE gtfs_fare_media (
    fare_media_id TEXT NOT NULL,
    fare_media_name TEXT,
    fare_media_type INTEGER NOT NULL
);

CREATE TABLE gtfs_fare_products (
    fare_product_id TEXT NOT NULL,
    fare_product_name TEXT,
    rider_category_id TEXT,
    fare_media_id TEXT,
    amount TEXT NOT NULL,
    currency TEXT NOT NULL
);

CREATE TABLE gtfs_areas (
    area_id TEXT NOT NULL,
    area_name TEXT
);

CREATE TABLE gtfs_stop_areas (
    area_id TEXT NOT NULL,
    stop_id TEXT NOT NULL
);

CREATE TABLE gtfs_networks (
    network_id TEXT NOT NULL,
    network_name TEXT
);

CREATE TABLE gtfs_route_networks (
    network_id TEXT NOT NULL,
    route_id TEXT NOT NULL
);

CREATE TABLE gtfs_fare_leg_rules (
    leg_group_id TEXT,
    network_id TEXT,
    from_area_id TEXT,
    to_area_id TEXT,
    from_timeframe_group_id TEXT,
    to_timeframe_group_id TEXT,
    fare_product_id TEXT NOT NULL,
    rule_priority INTEGER
);

CREATE TABLE gtfs_fare_leg_join_rules (
    from_network_id TEXT NOT NULL,
    to_network_id TEXT NOT NULL,
    from_stop_id TEXT,
    to_stop_id TEXT
);

CREATE TABLE gtfs_fare_transfer_rules (
    from_leg_group_id TEXT,
    to_leg_group_id TEXT,
    transfer_count INTEGER,
    duration_limit INTEGER,
    duration_limit_type INTEGER,
    fare_transfer_type INTEGER NOT NULL,
    fare_product_id TEXT
);

CREATE TABLE gtfs_frequencies (
    trip_id TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    headway_secs INTEGER NOT NULL,
    exact_times INTEGER
);

CREATE TABLE gtfs_transfers (
    from_stop_id TEXT,
    to_stop_id TEXT,
    from_route_id TEXT,
    to_route_id TEXT,
    from_trip_id TEXT,
    to_trip_id TEXT,
    transfer_type INTEGER,
    min_transfer_time INTEGER
);

CREATE TABLE gtfs_pathways (
    pathway_id TEXT NOT NULL,
    from_stop_id TEXT NOT NULL,
    to_stop_id TEXT NOT NULL,
    pathway_mode INTEGER NOT NULL,
    is_bidirectional INTEGER NOT NULL,
    length REAL,
    traversal_time INTEGER,
    stair_count INTEGER,
    max_slope REAL,
    min_width REAL,
    signposted_as TEXT,
    reversed_signposted_as TEXT
);

CREATE TABLE gtfs_location_group_stops (
    location_group_id TEXT NOT NULL,
    stop_id TEXT NOT NULL
);

CREATE TABLE gtfs_notices (
    notice_id TEXT NOT NULL,
    display_text TEXT NOT NULL
);

CREATE TABLE gtfs_trip_segments (
    trip_segment_id TEXT NOT NULL,
    trip_id TEXT NOT NULL,
    from_stop_sequence INTEGER NOT NULL CHECK (from_stop_sequence >= 0),
    to_stop_sequence INTEGER NOT NULL CHECK (to_stop_sequence >= from_stop_sequence)
);

CREATE TABLE gtfs_notice_assignments (
    notice_id TEXT NOT NULL,
    table_name TEXT NOT NULL CHECK (table_name IN ('routes', 'trips', 'trip_segments')),
    record_id TEXT NOT NULL
);

CREATE TABLE gtfs_translations (
    table_name TEXT NOT NULL,
    field_name TEXT NOT NULL,
    language TEXT NOT NULL,
    translation TEXT NOT NULL,
    record_id TEXT,
    record_sub_id TEXT,
    field_value TEXT
);

CREATE TABLE gtfs_feed_info (
    feed_publisher_name TEXT NOT NULL,
    feed_publisher_url TEXT NOT NULL,
    feed_lang TEXT NOT NULL,
    default_lang TEXT,
    feed_start_date TEXT,
    feed_end_date TEXT,
    feed_version TEXT,
    feed_contact_email TEXT,
    feed_contact_url TEXT
);

CREATE TABLE gtfs_attributions (
    attribution_id TEXT,
    agency_id TEXT,
    route_id TEXT,
    trip_id TEXT,
    organization_name TEXT NOT NULL,
    is_producer INTEGER,
    is_operator INTEGER,
    is_authority INTEGER,
    attribution_url TEXT,
    attribution_email TEXT,
    attribution_phone TEXT
);

