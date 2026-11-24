import pytest

from dino2gtfs.domain.errors import ConfigurationError, InputError
from dino2gtfs.domain.id_template import IdTemplate


def test_id_template_supports_multiple_sources_and_computed_fields():
    template = IdTemplate(
        {"ids": {"routes": {"mode": "template", "template": "route:{{{line.LINE_NR}}}:{gtfs.route_type}"}}},
        "routes", {"line": "line.din", "gtfs": "GTFS context"},
    )
    template.validate_fields({"line": ["line_nr"], "gtfs": ["route_type"]})
    assert template.render({"line": {"LINE_NR": "42"}, "gtfs": {"route_type": "3"}}, "line.din row 1") == "route:{42}:3"


def test_id_template_reports_missing_fields_and_values():
    template = IdTemplate(
        {"ids": {"routes": {"mode": "template", "template": "{line.LINE_NR}:{gtfs.route_type}"}}},
        "routes", {"line": "line.din", "gtfs": "GTFS context"},
    )
    with pytest.raises(InputError, match=r"GTFS context is missing column.*gtfs.route_type"):
        template.validate_fields({"line": ["LINE_NR"], "gtfs": []})
    with pytest.raises(InputError, match=r"line.din row 2: gtfs.route_type is required"):
        template.render({"line": {"LINE_NR": "1"}, "gtfs": {"route_type": ""}}, "line.din row 2")


def test_id_template_rejects_unavailable_namespace():
    with pytest.raises(ConfigurationError, match=r"ids.routes.template"):
        IdTemplate({"ids": {"routes": {"mode": "template", "template": "{trip.TRIP_ID}"}}},
                   "routes", {"line": "line.din"})
