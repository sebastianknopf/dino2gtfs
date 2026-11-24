"""Shared functionality for GTFS object transformers."""
import hashlib
import sqlite3
from abc import ABC, abstractmethod
from collections.abc import Collection, Mapping
from typing import Any

from dino2gtfs.domain.errors import InputError
from dino2gtfs.domain.id_template import IdTemplate
from dino2gtfs.transformers.intf_transformer import Transformer


class TransformerBase(Transformer, ABC):
    """Provide shared template handling and canonical entity identity."""

    @abstractmethod
    def transform(self, database: sqlite3.Connection, settings: Mapping[str, Any]) -> None:
        """Transform imported data into a GTFS object table."""

    @abstractmethod
    def fingerprint_components(
        self, sources: Mapping[str, Mapping[str, object]], context: str
    ) -> tuple[str, ...]:
        """Return ordered canonical identity components for the entity."""

    def make_id_template(
        self, settings: Mapping[str, Any], entity: str, sources: Mapping[str, str]
    ) -> IdTemplate:
        if "gtfs" in sources:
            raise ValueError("gtfs is reserved for virtual template fields")
        return IdTemplate(settings, entity, {**sources, "gtfs": "GTFS context"})

    def validate_template_fields(self, template: IdTemplate, fields: Mapping[str, Collection[str]]) -> None:
        if "gtfs" in fields:
            raise ValueError("gtfs is reserved for virtual template fields")
        template.validate_fields({**fields, "gtfs": ("entity", "canonical", "fingerprint")})

    def render_id(
        self, template: IdTemplate, sources: Mapping[str, Mapping[str, object]], context: str
    ) -> str:
        if "gtfs" in sources:
            raise ValueError("gtfs is reserved for virtual template fields")
        virtual = {"entity": template.entity}
        needs_key = template.requires_field("gtfs", "canonical")
        needs_fingerprint = template.requires_field("gtfs", "fingerprint")
        if needs_key or needs_fingerprint:
            components = self.fingerprint_components(sources, context)
            if not components or any(not isinstance(component, str) for component in components):
                field = "fingerprint" if needs_fingerprint else "canonical"
                raise InputError(f"{context}: {field} requires canonical string components")
            encoded = [component.encode("utf-8") for component in components]
            if needs_key:
                virtual["canonical"] = "".join(
                    f"{len(data)}:{component}" for component, data in zip(components, encoded)
                )
            if needs_fingerprint:
                digest = hashlib.sha256()
                for data in encoded:
                    digest.update(len(data).to_bytes(8, "big"))
                    digest.update(data)
                virtual["fingerprint"] = digest.hexdigest()
        return template.render({**sources, "gtfs": virtual}, context)
