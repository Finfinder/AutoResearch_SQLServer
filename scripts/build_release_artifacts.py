from __future__ import annotations

import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


SOURCE_BUNDLE_FILES = (
    ".env.example",
    "CHANGELOG.md",
    "GUARDRAILS.md",
    "LICENSE",
    "README.md",
    "aggregator.py",
    "db.py",
    "guardrails.py",
    "main.py",
    "query.sql",
    "requirements.txt",
    "runner.py",
    "stats_parser.py",
    "validator.py",
    "variants.py",
    "version.py",
)


def build_source_bundle(output_dir: Path, version: str, project_root: Path | None = None) -> Path:
    root = Path(project_root) if project_root is not None else Path(__file__).resolve().parents[1]
    destination_dir = Path(output_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)

    archive_path = destination_dir / f"AutoResearch_SQLServer-{version}-source.zip"
    archive_prefix = Path(f"AutoResearch_SQLServer-{version}")

    with ZipFile(archive_path, "w", compression=ZIP_DEFLATED) as archive:
        for relative_path in SOURCE_BUNDLE_FILES:
            source_path = root / relative_path
            if not source_path.exists():
                raise FileNotFoundError(f"Missing release source file: {source_path}")

            archive.write(source_path, arcname=(archive_prefix / relative_path).as_posix())

    return archive_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build source release artifacts for AutoResearch_SQLServer.")
    parser.add_argument("--version", required=True, help="Release version without the leading 'v'.")
    parser.add_argument(
        "--output-dir",
        default="dist-release",
        help="Directory where release artifacts should be written.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    archive_path = build_source_bundle(output_dir=Path(args.output_dir), version=args.version)
    print(archive_path)


if __name__ == "__main__":
    main()