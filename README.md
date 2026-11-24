# dino2gtfs

Converter for DINO schedule data to GTFS static.

## Usage

```bash
dino2gtfs convert --profile dino212_default --config config/default.yaml --input input/dino.zip --output output/gtfs.zip [--database output/database.db3]
```

The profile and YAML configuration are mandatory. See a sample configuration in [config/default.yaml](config/default.yaml).
For more information, see the [user manual](docs/manual/index.md).

Use `--database output/database.db3` to retain the SQLite database for inspection. Without this option, the database is temporary.

After profile conversion, all enabled `gtfs_` tables are exported into a UTF-8 ZIP, including header-only files for empty tables. Locations are exported as `locations.geojson`. Different features can be enabled or disabled by `features.featureName` in the configuration file.

The export does not validate the complete feed. Including empty tables can violate GTFS file-level conditions; the exporter logs a warning where applicable.

## License

This project is licensed unter the Apache 2.0 license. See [LICENSE](LICENSE.md) for more information.
