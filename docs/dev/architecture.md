# Architecture

The repository uses a `src/` layout and clean architectural boundaries.

- `application`: `Protocol` ports.
- `domain`: pure calendar and identity functions.
- `infrastructure`: configuration, ZIP, CSV, SQLite and generic DINO import adapters.
- `gtfs`: profile-independent GTFS ZIP export.
- `transformers`: reusable GTFS object transformers implementing the `Transformer` protocol.
- `profiles`: profile-specific converter implementations.

The selected profile is mandatory for conversion, but DINO import is profile-independent. The CLI creates an isolated `.db3`, imports the archive, and passes the database directly to the profile converter. Each root-level `.din` file is imported into a `dino_` table named after the file without its extension. Its headers define `TEXT` columns; no indices are created. Import is streamed in batches of 500 rows. By default the database is temporary and removed on both success and failure; `--database` replaces the specified file and retains the new database. Successful conversion changes are committed, while uncommitted changes are rolled back on failure.

The database adapter initializes the shared `gtfs_` tables as soon as the connection opens. The packaged `gtfs_static_schema.sql` covers the GTFS Schedule reference and the notice, notice assignment and trip segment fields in [google/transit#638](https://github.com/google/transit/pull/638). The GTFS tables have no indexes or foreign keys; primary-key uniqueness and references are not enforced by SQLite.

GTFS times and dates are stored as text, as are fare amounts to preserve decimal precision. Blank values should be stored as `NULL` where the specification permits them, including the few required fields that accept blank values. Each `locations.geojson` feature is stored in `gtfs_locations` with its `id` and `type` and its `properties` and `geometry` objects serialized as JSON; the enclosing `FeatureCollection` is reconstructed when exporting.

After conversion, the CLI exports every enabled `gtfs_` table, including empty tables, and atomically replaces the destination ZIP on success. The export validates serialization but is not a full GTFS validator; including all files can conflict with conditional file rules.
