# Architecture

The repository uses a `src/` layout and clean architectural boundaries.

- `application`: `Protocol` ports.
- `domain`: pure calendar and identity functions.
- `infrastructure`: configuration, ZIP, CSV, SQLite and generic DINO import adapters.
- `profiles`: profile-specific converter implementations.

The selected profile is mandatory for conversion, but DINO import is profile-independent. The CLI creates an isolated `.db3` in a temporary directory, imports the archive, and passes the database directly to the profile converter. Each root-level `.din` file is imported into a `dino_` table named after the file without its extension. Its headers define `TEXT` columns; no indices are created. Import is streamed in batches of 500 rows. The database is removed on both success and failure.
