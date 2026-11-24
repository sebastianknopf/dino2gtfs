import pytest

from dino2gtfs.domain.errors import ConfigurationError, InputError
from dino2gtfs.infrastructure.sqlite import temporary_database
from dino2gtfs.transformers.base_transformer import TransformerBase


class ExampleTransformer(TransformerBase):
    def __init__(self, components):
        self.components = components

    def transform(self, database, settings):
        pass

    def fingerprint_components(self, sources, context):
        return self.components


def test_base_requires_transform_and_fingerprint_implementation():
    with pytest.raises(TypeError):
        TransformerBase()


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


def test_defaults_apply_to_any_gtfs_entity_and_override_values():
    transformer = ExampleTransformer(())
    with temporary_database() as db:
        defaults = transformer.configured_defaults(db, {"defaults": {
            "stops": {"stop_name": "Configured", "wheelchair_boarding": 0, "stop_desc": None}
        }}, "stops")
        assert transformer.apply_defaults(
            {"stop_id": "s", "stop_name": "Source", "wheelchair_boarding": 1, "stop_desc": "Source"},
            defaults,
        ) == {"stop_id": "s", "stop_name": "Configured", "wheelchair_boarding": 0, "stop_desc": None}
        transformer.insert_with_defaults(
            db, "stops", ("stop_id", "stop_name"),
            [{"stop_id": "s", "stop_name": "Source"}], defaults,
        )
        assert db.execute("SELECT stop_id, stop_name, wheelchair_boarding, stop_desc FROM gtfs_stops").fetchall() == [
            ("s", "Configured", 0, None)
        ]


@pytest.mark.parametrize("defaults, message", [
    ({"stops": {"STOP_NAME": "Wrong case"}}, "Unknown GTFS field"),
    ({"stops": {"missing": "x"}}, "Unknown GTFS field"),
    ({"missing": {}}, "Unknown GTFS entity"),
    ({"stops": {"stop_id": "x"}}, "ID field"),
    ({"stops": {"parent_station": "x"}}, "ID field"),
    ({"locations": {"id": "x"}}, "ID field"),
    ({"stops": {"stop_name": ["invalid"]}}, "scalar SQLite value"),
    ({"stops": {"stop_lat": float("nan")}}, "must be finite"),
])
def test_invalid_defaults_are_rejected_even_for_other_entities(defaults, message):
    with temporary_database() as db, pytest.raises(ConfigurationError, match=message):
        ExampleTransformer(()).configured_defaults(db, {"defaults": defaults}, "agency")
