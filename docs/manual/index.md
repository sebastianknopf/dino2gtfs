# User Manual

dino2gtfs imports a DINO ZIP, converts it using a selected profile and writes a GTFS ZIP. Start with the [sample configuration](../../config/default.yaml) and run:

```bash
dino2gtfs convert --profile dino212_default --config config/default.yaml --input input/dino.zip --output output/gtfs.zip
```

Use `dino2gtfs profiles` to list profiles. Add `--database output/database.db3` to keep the staging database for inspection; an existing file at that path is replaced.

The default profile currently converts agencies only. Other GTFS tables are exported empty, so the resulting feed is not a complete, validated timetable.

## Configuration

- Set `defaults.<entity>.<column>` for fallback values, using the exact GTFS table and column names. Defaults apply only when the transformed value is absent, `null`, empty or whitespace-only. Numeric zero and `false` are valid values and are preserved.
- Set `overrides.<entity>.<column>` to force a value, including `null` for optional fields. Precedence is **overrides > source/mapped values > defaults**. Move existing defaults to `overrides` if they must continue replacing source values. Required-field and structural validation still applies.
- Both sections use the same schema validation: unknown entities or fields are errors, and ID columns (`id`, names ending in `_id`, and `parent_station`) cannot be configured; use `ids` separately. Values may be strings, numbers, booleans or `null`, subject to entity-specific validation.
- The sample agency name and URL are examples; replace them with authoritative values before producing a feed.
- [GTFS ID Templates](ids.md): configure identifiers and use the fingerprint placeholder.

For example:

```yaml
defaults:
  routes:
    route_type: 3
    route_short_name: "Unnamed"
overrides:
  routes:
    route_color: "123456"
```

Mapped route modes and nonblank DINO line names are preserved by these defaults, while every route receives the configured color. An `overrides.routes.route_type` value also determines the route's identity and ID placeholders. For stops, coordinate defaults are used only after point, area and station coordinates are exhausted, before falling back to zero.
