# dino2gtfs

Converter for DINO schedule data to GTFS static.

## Usage

```bash
dino2gtfs convert --profile dino212_default --config config/default.yaml --input input/dino.zip --output output/gtfs.zip [--database output/database.db3]
```

The profile and YAML configuration are mandatory. See a sample configuration in [config/default.yaml](config/default.yaml).

Use `--database output/database.db3` to retain the SQLite database for inspection. Without this option, the database is temporary.

## License

This project is licensed unter the Apache 2.0 license. See [LICENSE](LICENSE.md) for more information.
