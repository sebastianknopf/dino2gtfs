import pytest

from dino2gtfs.domain.errors import ConfigurationError, InputError
from dino2gtfs.infrastructure.sqlite import temporary_database
from dino2gtfs.transformers.agency import AgencyTransformer
from dino2gtfs.transformers.base_transformer import TransformerBase
from dino2gtfs.transformers.routes import RoutesTransformer
from dino2gtfs.transformers.stops import StopsTransformer


class ExampleTransformer(TransformerBase):
    def __init__(self, components):
        self.components = components

    def initialize(self, database, settings):
        pass

    def transform(self, database, settings):
        pass

    def fingerprint_components(self, sources, context):
        return self.components


def test_base_requires_transform_and_fingerprint_implementation():
    with pytest.raises(TypeError):
        TransformerBase()


def test_initialize_creates_mapping_tables_without_relations_or_indexes():
    with temporary_database() as db:
        AgencyTransformer().initialize(db, {})
        StopsTransformer().initialize(db, {})
        RoutesTransformer().initialize(db, {})
        for table, columns in (
            ("map_operator_2_agency", ["VERSION", "OP_CODE", "gtfs_id"]),
            ("map_stop_2_stops", ["VERSION", "STOP_ID", "gtfs_id"]),
            ("map_stop_point_2_stops", ["VERSION", "STOP_ID", "STOP_AREA_ID", "STOPPING_POINT_NR", "gtfs_id"]),
            ("map_line_2_routes", ["VERSION", "LINE_NR", "STR_LINE_VAR", "LINE_DIR_NR", "gtfs_id"]),
        ):
            info = db.execute("SELECT * FROM pragma_table_info(?)", (table,)).fetchall()
            assert [(row[1], row[2], row[5]) for row in info] == [(column, "TEXT", 0) for column in columns]
            assert db.execute("SELECT * FROM pragma_index_list(?)", (table,)).fetchall() == []
            assert db.execute("SELECT * FROM pragma_foreign_key_list(?)", (table,)).fetchall() == []


def test_fingerprint_is_stable_and_components_are_unambiguous():
    settings = {"ids": {"example": {"mode": "template", "template": "{gtfs.fingerprint}"}}}
    transformer = ExampleTransformer(("A",))
    template = transformer.make_id_template(settings, "example", {})
    transformer.validate_template_fields(template, {})
    assert transformer.render_id(template, {}, "example") == (
        "ead76f8e70b5dd3b1a07a92c25c425b2b27198728862103d65c31c621e52a6aa"
    )
    assert transformer.render_id(template, {}, "example") == ExampleTransformer(("A",)).render_id(
        template, {}, "example"
    )
    assert ExampleTransformer(("ab", "c")).render_id(template, {}, "example") != ExampleTransformer(
        ("a", "bc")
    ).render_id(template, {}, "example")


def test_fingerprint_is_only_computed_when_requested():
    transformer = ExampleTransformer(())
    template = transformer.make_id_template(
        {"ids": {"example": {"mode": "template", "template": "{source.ID}"}}},
        "example", {"source": "source.din"},
    )
    transformer.validate_template_fields(template, {"source": ["ID"]})
    assert transformer.render_id(template, {"source": {"ID": "1"}}, "source row 1") == "1"

    fingerprint = transformer.make_id_template(
        {"ids": {"example": {"mode": "template", "template": "{gtfs.fingerprint}"}}}, "example", {}
    )
    with pytest.raises(InputError, match="fingerprint requires canonical string components"):
        transformer.render_id(fingerprint, {}, "source row 1")


def test_entity_placeholder_does_not_require_identity_components():
    transformer = ExampleTransformer(())
    template = transformer.make_id_template(
        {"ids": {"routes": {"mode": "template", "template": "{gtfs.entity}"}}}, "routes", {}
    )
    transformer.validate_template_fields(template, {})
    assert transformer.render_id(template, {}, "example") == "routes"


def test_canonical_preserves_component_boundaries_and_utf8_lengths():
    transformer = ExampleTransformer(("é", "a:b"))
    template = transformer.make_id_template(
        {"ids": {"example": {"mode": "template", "template": "{gtfs.canonical}"}}}, "example", {}
    )
    transformer.validate_template_fields(template, {})
    assert transformer.render_id(template, {}, "example") == "2:é3:a:b"
    assert ExampleTransformer(("ab", "c")).render_id(template, {}, "example") == "2:ab1:c"
    assert ExampleTransformer(("a", "bc")).render_id(template, {}, "example") == "1:a2:bc"
    with pytest.raises(InputError, match="canonical requires canonical string components"):
        ExampleTransformer(()).render_id(template, {}, "example")


def test_key_and_fingerprint_share_the_same_identity_components():
    class CountingTransformer(ExampleTransformer):
        calls = 0

        def fingerprint_components(self, sources, context):
            self.calls += 1
            return self.components

    transformer = CountingTransformer(("A",))
    template = transformer.make_id_template(
        {"ids": {"example": {"mode": "template", "template": "{gtfs.canonical}:{gtfs.fingerprint}"}}},
        "example", {},
    )
    transformer.validate_template_fields(template, {})
    assert transformer.render_id(template, {}, "example") == (
        "1:A:ead76f8e70b5dd3b1a07a92c25c425b2b27198728862103d65c31c621e52a6aa"
    )
    assert transformer.calls == 1


def test_defaults_only_fill_missing_or_blank_values():
    transformer = ExampleTransformer(())
    assert transformer.apply_configuration(
        {"stop_name": "Source", "stop_code": "", "stop_desc": "  ", "stop_lat": 0.0, "stop_access": False},
        {"stop_name": "Default", "stop_code": "Code", "stop_desc": "Description", "stop_lat": 52,
         "stop_access": True, "stop_lon": 13},
        {},
    ) == {
        "stop_name": "Source", "stop_code": "Code", "stop_desc": "Description",
        "stop_lat": 0.0, "stop_access": False, "stop_lon": 13,
    }


def test_overrides_take_precedence_and_all_configured_columns_are_inserted():
    transformer = ExampleTransformer(())
    with temporary_database() as db:
        settings = {
            "defaults": {"stops": {"stop_name": "Default", "wheelchair_boarding": 1, "stop_desc": "Description"}},
            "overrides": {"stops": {"stop_name": "Forced", "stop_desc": None, "stop_code": "Code"}},
        }
        defaults = transformer.configured_fields(db, settings, "stops", "defaults")
        overrides = transformer.configured_fields(db, settings, "stops", "overrides")
        transformer.insert_with_configuration(
            db, "stops", ("stop_id", "stop_name"),
            [{"stop_id": "s", "stop_name": "Source"}], defaults, overrides,
        )
        assert db.execute(
            "SELECT stop_id, stop_name, wheelchair_boarding, stop_desc, stop_code FROM gtfs_stops"
        ).fetchall() == [
            ("s", "Forced", 1, None, "Code")
        ]


@pytest.mark.parametrize("section", ["defaults", "overrides"])
@pytest.mark.parametrize("configured, message", [
    (None, "must be a mapping"),
    ({"stops": None}, "must be an entity mapping"),
    ({"stops": {"STOP_NAME": "Wrong case"}}, "Unknown GTFS field"),
    ({"stops": {"missing": "x"}}, "Unknown GTFS field"),
    ({"missing": {}}, "Unknown GTFS entity"),
    ({"stops": {"stop_id": "x"}}, "ID field"),
    ({"stops": {"parent_station": "x"}}, "ID field"),
    ({"locations": {"id": "x"}}, "ID field"),
    ({"stops": {"stop_name": ["invalid"]}}, "scalar SQLite value"),
    ({"stops": {"stop_lat": float("nan")}}, "must be finite"),
    ({"stops": {"wheelchair_boarding": 2**63}}, "SQLite integer range"),
])
def test_invalid_configured_fields_are_rejected_even_for_other_entities(section, configured, message):
    with temporary_database() as db, pytest.raises(ConfigurationError, match=message):
        ExampleTransformer(()).configured_fields(db, {section: configured}, "agency", section)
