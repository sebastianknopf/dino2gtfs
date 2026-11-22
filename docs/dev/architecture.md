# Architecture

The repository uses a `src/` layout and clean architectural boundaries.

- `application`: orchestration and `Protocol` ports.
- `domain`: pure calendar and identity functions.
- `infrastructure`: configuration, ZIP, CSV and SQLite adapters.
- `profiles`: profile-specific importer and converter implementations.

The selected profile is mandatory. Every run creates an isolated `.db3` in a temporary directory. Import is streamed in batches of 500 rows. The database is removed on both success and failure.
