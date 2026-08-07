#!/usr/bin/env python3
"""
Upload files to Pinata IPFS storage with metadata.

This CLI tool uploads any file to Pinata with custom metadata.
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# Add parent directory to path to import stargazer modules
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from stargazer.assets.component import ComponentFile

from stargazer.utils.pinata import PinataClient


async def upload_file(
    file_path: Path,
    keyvalues: dict,
    client: PinataClient,
    update_config: bool = False,
    config_path: Path | None = None,
):
    """Upload a single file to Pinata."""

    if not file_path.exists():
        print(f"ERROR: File not found: {file_path}")
        return None

    print(f"Uploading: {file_path}")
    print(f"  Size: {file_path.stat().st_size:,} bytes")
    print(f"  Metadata: {keyvalues}")

    try:
        comp = ComponentFile(path=file_path, keyvalues=dict(keyvalues))
        await client.upload(comp)

        print("  Success!")
        print(f"    CID: {comp.cid}")
        print()

        # Update config.py if requested
        if update_config and config_path:
            update_config_file(config_path, file_path.name, comp.cid)

        return comp

    except Exception as e:
        print(f"  Failed: {e}\n")
        return None


def update_config_file(config_path: Path, filename: str, cid: str):
    """Update tests/config.py with the new CID."""
    if not config_path.exists():
        print(f"  Config file not found: {config_path}")
        return

    # Read current config
    config_content = config_path.read_text()

    # Try to update existing empty entry
    old_line_pattern = f'    "{filename}": ""'
    new_line = f'    "{filename}": "{cid}"'

    if old_line_pattern in config_content:
        config_content = config_content.replace(old_line_pattern, new_line)
        config_path.write_text(config_content)
        print(f"  Updated {filename} in {config_path}")
    else:
        print(f"  No empty entry for {filename} in config.py")
        print(f'     Add manually: "{filename}": "{cid}"')


async def main():
    parser = argparse.ArgumentParser(
        description="Upload files to Pinata IPFS storage with metadata",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Upload a single file with metadata
  %(prog)s /path/to/file.fa --metadata '{"type": "reference", "build": "GRCh38"}'

  # Upload and update config.py
  %(prog)s tests/fixtures/GRCh38_TP53.fa --metadata '{"type": "reference"}' --update-config

  # Upload with metadata from command line key=value pairs
  %(prog)s myfile.txt -m type=data -m env=test -m version=1.0

  # Upload multiple files with the same metadata
  %(prog)s file1.txt file2.txt --metadata '{"type": "test", "env": "dev"}'
        """,
    )

    parser.add_argument(
        "files",
        nargs="+",
        type=Path,
        help="File(s) to upload to Pinata",
    )

    parser.add_argument(
        "--metadata",
        "--keyvalues",
        type=str,
        help='Metadata as JSON string (e.g., \'{"type": "reference", "env": "test"}\')',
    )

    parser.add_argument(
        "-m",
        "--meta",
        action="append",
        metavar="KEY=VALUE",
        help="Metadata key=value pairs (can be used multiple times)",
    )

    parser.add_argument(
        "--update-config",
        action="store_true",
        help="Update tests/config.py CIDS dictionary with uploaded CIDs",
    )

    parser.add_argument(
        "--config-path",
        type=Path,
        default=Path(__file__).parent.parent / "tests" / "config.py",
        help="Path to config.py file (default: tests/config.py)",
    )

    args = parser.parse_args()

    # Check for API key
    if not os.environ.get("PINATA_JWT"):
        print("ERROR: PINATA_JWT environment variable not set")
        print("Please set your Pinata JWT token:")
        print("  export PINATA_JWT='your_jwt_token_here'")
        return 1

    # Parse metadata
    keyvalues = {}

    # First, parse JSON metadata if provided
    if args.metadata:
        try:
            keyvalues = json.loads(args.metadata)
        except json.JSONDecodeError as e:
            print(f"ERROR: Invalid JSON in --metadata: {e}")
            return 1

    # Then, add/override with -m key=value pairs
    if args.meta:
        for pair in args.meta:
            if "=" not in pair:
                print(f"ERROR: Invalid metadata format: {pair}")
                print("Expected format: key=value")
                return 1
            key, value = pair.split("=", 1)
            keyvalues[key.strip()] = value.strip()

    # Ensure we have some metadata
    if not keyvalues:
        print("ERROR: No metadata provided")
        print("Use --metadata with JSON or -m key=value")
        return 1

    # Initialize client
    client = PinataClient()

    print(f"Uploading {len(args.files)} file(s) to Pinata...\n")

    uploaded_count = 0
    for file_path in args.files:
        result = await upload_file(
            file_path=file_path,
            keyvalues=keyvalues,
            client=client,
            update_config=args.update_config,
            config_path=args.config_path,
        )
        if result:
            uploaded_count += 1

    # Summary
    print("=" * 60)
    print(f"Upload Summary: {uploaded_count}/{len(args.files)} files uploaded")
    print("=" * 60)

    return 0 if uploaded_count > 0 else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
