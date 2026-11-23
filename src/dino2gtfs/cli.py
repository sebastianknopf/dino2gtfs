"""Command-line interface."""
import argparse
import logging
from pathlib import Path
from collections.abc import Sequence
from dino2gtfs.domain.errors import ConfigurationError, InputError, CollisionError, ConverterError
from dino2gtfs.gtfs.exporter import GtfsExporter
from dino2gtfs.infrastructure.config import load_config
from dino2gtfs.infrastructure.importer import DinoImporter
from dino2gtfs.infrastructure.logsetup import configure_logging
from dino2gtfs.infrastructure.sqlite import open_database
from dino2gtfs.profiles.registry import get_profile, list_profiles

LOGGER = logging.getLogger(__name__)

def _same_path(first: Path, second: Path) -> bool:
    return (first.resolve() == second.resolve()
            or (first.exists() and second.exists() and first.samefile(second)))

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dino2gtfs", description="DINO to GTFS converter")
    parser.add_argument("--verbose", action="store_true")
    
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("profiles", help="list supported profiles")
    
    convert = commands.add_parser("convert", help="convert a DINO archive to GTFS")
    convert.add_argument("--profile", required=True)
    convert.add_argument("--config", type=Path, required=True)
    convert.add_argument("--input", type=Path, required=True)
    convert.add_argument("--output", type=Path, required=True)
    convert.add_argument("--database", type=Path, help="keep the SQLite database at this path (replaces an existing file)")
    
    return parser

def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    
    configure_logging(args.verbose)
    
    if args.command == "profiles":
        for profile in list_profiles():
            print(profile)
        
        return 0
    
    try:
        profile = get_profile(args.profile)
        settings = load_config(args.config)
        
        if _same_path(args.input, args.output):
            raise ConfigurationError("Input and output paths must differ")
        if _same_path(args.config, args.output):
            raise ConfigurationError("Config and output paths must differ")

        if args.database is not None:
            for other in (args.input, args.output, args.config):
                if _same_path(args.database, other):
                    raise ConfigurationError("Database path must differ from input, output and config paths")
        
        LOGGER.info("starting conversion with profile %s", args.profile)
        LOGGER.debug("input: %s, output: %s", args.input, args.output)
        LOGGER.debug("config: %s", settings)

        with open_database(args.database) as database:
            # run DINO data importer
            DinoImporter().import_archive(args.input, database, settings)

            # apply profile conversion
            profile.converter.convert(database, args.output, settings)

            # run GTFS export
            GtfsExporter().export_archive(database, args.output, settings)

        LOGGER.info("completed conversion")
        
        return 0
    except (ConfigurationError, InputError, CollisionError, ConverterError) as exc:
        logging.error("%s", exc)
        if isinstance(exc, ConfigurationError):
            return 2
        
        if isinstance(exc, InputError):
            return 3
        
        if isinstance(exc, CollisionError):
            return 4
        
        return 1
    except Exception:
        logging.exception("Unexpected converter failure")
        
        return 1
