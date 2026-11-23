import json
import sqlite3

import pytest

from dino2gtfs.infrastructure.sqlite import temporary_database


TABLE_FIELDS = {
    "agency": "agency_id agency_name agency_url agency_timezone agency_lang agency_phone agency_fare_url agency_email cemv_support",
    "stops": "stop_id stop_code stop_name tts_stop_name stop_desc stop_lat stop_lon zone_id stop_url location_type parent_station stop_timezone wheelchair_boarding level_id platform_code stop_access",
    "routes": "route_id agency_id route_short_name route_long_name route_desc route_type route_url route_color route_text_color route_sort_order continuous_pickup continuous_drop_off network_id cemv_support",
    "trips": "route_id service_id trip_id trip_headsign trip_short_name direction_id block_id shape_id wheelchair_accessible bikes_allowed cars_allowed safe_duration_factor safe_duration_offset",
    "stop_times": "trip_id arrival_time departure_time stop_id location_group_id location_id stop_sequence stop_headsign start_pickup_drop_off_window end_pickup_drop_off_window pickup_type drop_off_type continuous_pickup continuous_drop_off shape_dist_traveled timepoint pickup_booking_rule_id drop_off_booking_rule_id",
    "calendar": "service_id monday tuesday wednesday thursday friday saturday sunday start_date end_date",
    "calendar_dates": "service_id date exception_type",
    "fare_attributes": "fare_id price currency_type payment_method transfers agency_id transfer_duration",
    "fare_rules": "fare_id route_id origin_id destination_id contains_id",
    "timeframes": "timeframe_group_id start_time end_time service_id",
    "rider_categories": "rider_category_id rider_category_name is_default_fare_category eligibility_url",
    "fare_media": "fare_media_id fare_media_name fare_media_type",
    "fare_products": "fare_product_id fare_product_name rider_category_id fare_media_id amount currency",
    "fare_leg_rules": "leg_group_id network_id from_area_id to_area_id from_timeframe_group_id to_timeframe_group_id fare_product_id rule_priority",
    "fare_leg_join_rules": "from_network_id to_network_id from_stop_id to_stop_id",
    "fare_transfer_rules": "from_leg_group_id to_leg_group_id transfer_count duration_limit duration_limit_type fare_transfer_type fare_product_id",
    "areas": "area_id area_name",
    "stop_areas": "area_id stop_id",
    "networks": "network_id network_name",
    "route_networks": "network_id route_id",
    "shapes": "shape_id shape_pt_lat shape_pt_lon shape_pt_sequence shape_dist_traveled",
    "frequencies": "trip_id start_time end_time headway_secs exact_times",
    "transfers": "from_stop_id to_stop_id from_route_id to_route_id from_trip_id to_trip_id transfer_type min_transfer_time",
    "pathways": "pathway_id from_stop_id to_stop_id pathway_mode is_bidirectional length traversal_time stair_count max_slope min_width signposted_as reversed_signposted_as",
    "levels": "level_id level_index level_name",
    "location_groups": "location_group_id location_group_name",
    "location_group_stops": "location_group_id stop_id",
    "locations": "id type properties geometry",
    "booking_rules": "booking_rule_id booking_type prior_notice_duration_min prior_notice_duration_max prior_notice_last_day prior_notice_last_time prior_notice_start_day prior_notice_start_time prior_notice_service_id message pickup_message drop_off_message phone_number info_url booking_url",
    "translations": "table_name field_name language translation record_id record_sub_id field_value",
    "feed_info": "feed_publisher_name feed_publisher_url feed_lang default_lang feed_start_date feed_end_date feed_version feed_contact_email feed_contact_url",
    "attributions": "attribution_id agency_id route_id trip_id organization_name is_producer is_operator is_authority attribution_url attribution_email attribution_phone",
    "notices": "notice_id display_text",
    "notice_assignments": "notice_id table_name record_id",
    "trip_segments": "trip_segment_id trip_id from_stop_sequence to_stop_sequence",
}


def test_schema_created_with_all_reference_fields():
    with temporary_database() as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert tables == {"gtfs_" + name for name in TABLE_FIELDS}
        for name, fields in TABLE_FIELDS.items():
            columns = [row[1] for row in db.execute(f'PRAGMA table_info("gtfs_{name}")')]
            assert columns == fields.split(), name
        for name in TABLE_FIELDS:
            assert db.execute(f'PRAGMA index_list("gtfs_{name}")').fetchall() == []
            assert db.execute(f'PRAGMA foreign_key_list("gtfs_{name}")').fetchall() == []


def test_gtfs_tables_do_not_enforce_references_or_uniqueness():
    with temporary_database() as db:
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("INSERT INTO gtfs_trip_segments VALUES ('segment', 'missing', 1, 2)")
        db.execute("INSERT INTO gtfs_notice_assignments VALUES ('missing', 'trip_segments', 'segment')")
        db.execute("INSERT INTO gtfs_notices VALUES ('notice', 'One'), ('notice', 'Two')")
        db.commit()
        assert db.execute("SELECT count(*) FROM gtfs_notices WHERE notice_id = 'notice'").fetchone() == (2,)
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO gtfs_trip_segments VALUES ('bad', 'trip', 2, 1)")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO gtfs_notice_assignments VALUES ('notice', 'stops', 'stop')")


def test_optional_keys_decimal_values_and_geojson():
    with temporary_database() as db:
        db.execute("INSERT INTO gtfs_rider_categories (rider_category_id, rider_category_name) VALUES ('adult', 'Adult')")
        db.execute("INSERT INTO gtfs_fare_products (fare_product_id, amount, currency) VALUES ('p', '0.1234567890123456789', 'EUR')")
        assert db.execute("SELECT amount FROM gtfs_fare_products").fetchone() == ("0.1234567890123456789",)
        db.execute("INSERT INTO gtfs_locations VALUES (?, ?, ?, ?)", (
            "zone", "Feature", '{"stop_name":"Zone"}',
            '{"type":"Polygon","coordinates":[[[0,0],[1,0],[1,1],[0,0]]]}',
        ))
        properties, geometry = db.execute("SELECT properties, geometry FROM gtfs_locations").fetchone()
        assert json.loads(properties)["stop_name"] == "Zone"
        assert json.loads(geometry)["type"] == "Polygon"
