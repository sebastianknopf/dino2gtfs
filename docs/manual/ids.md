# GTFS ID Templates

Configure identifiers under `ids.<GTFS entity>` in your YAML file. For example:

```yaml
ids:
  entity_name:
    mode: template
    template: "{gtfs.entity}:{gtfs.canonical}"
```

Replace `entity_name` with a GTFS entity supported by the selected profile. Edit the literal text or add supported placeholders to customize the ID. Quote the template as a YAML string. `{{` and `}}` produce literal braces; format specifications and conversions such as `:>5` or `!r` are not supported.

The [sample configuration](../../config/default.yaml) includes settings for entities and modes that may not yet be implemented by the selected profile.

## DINO Fields

Write a DINO column as `{table.COLUMN}`, using the `.din` filename without its extension for `table`. Column names are matched case-insensitively; the table namespace must match the transformer. For example, `{operator.OP_CODE}` reads the `OP_CODE` column from `operator.din` when that source is supplied by the transformer.

## Virtual GTFS Placeholders

These values are computed by transformers, not read from DINO columns:

| Placeholder | Value |
|---|---|
| `{gtfs.entity}` | The entity name in `ids.<GTFS entity>`. |
| `{gtfs.canonical}` | The transformer's ordered canonical identity components, each prefixed by its UTF-8 byte length and a colon. For components `ab` and `c`, the value is `2:ab1:c`. |
| `{gtfs.fingerprint}` | A lowercase SHA-256 hexadecimal digest over the ordered canonical identity components, each UTF-8 encoded and prefixed by its byte length as an eight-byte big-endian integer. |
| `{gtfs.stop_kind}` | For stops, `station` or `platform`. |
| `{gtfs.route_type}` | For routes, the resolved GTFS transport mode from `mapping.mode_of_transport` or `defaults.routes.route_type`. |

The transformer defines the identity components; they are not otherwise normalized. The canonical key exposes their values, while the fingerprint is compact. Both are stable for the same ordered components. Collision checks still apply, especially when combining placeholders with other values.