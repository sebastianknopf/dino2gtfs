"""Command-line interface."""
import argparse
import logging
from pathlib import Path
from collections.abc import Sequence
from dino2gtfs.application.convert import convert_archive
from dino2gtfs.domain.errors import ConfigurationError, InputError, CollisionError, ConverterError
from dino2gtfs.infrastructure.config import load_config
from dino2gtfs.infrastructure.logsetup import configure_logging
from dino2gtfs.profiles.registry import get_profile, list_profiles

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dino2gtfs", description="DINO to GTFS converter")
    parser.add_argument("--verbose", action="store_true")
    
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("profiles", help="List supported profiles")
    
    convert = commands.add_parser("convert", help="Convert a DINO ZIP to GTFS")
    convert.add_argument("--profile", required=True)
    convert.add_argument("--config", type=Path, required=True)
    convert.add_argument("--input", type=Path, required=True)
    convert.add_argument("--output", type=Path, required=True)
    
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
        
        if args.input.resolve() == args.output.resolve():
            raise ConfigurationError("Input and output paths must differ")
        
        convert_archive(profile, settings, args.input, args.output)
        
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
