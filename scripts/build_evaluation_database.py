from __future__ import annotations

import argparse
import csv
import sqlite3
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_PATH = (
    REPO_ROOT / "evaluation_corpus" / "datasets" / "evaluation_incidents_v1.csv"
)
DEFAULT_DATABASE_PATH = (
    REPO_ROOT / "artifacts" / "evaluation" / "fixtures" / "evaluation_incidents_v1.sqlite3"
)


# Parse explicit paths and keep replacement opt-in because it discards a generated fixture.
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the read-only SQLite fixture from the canonical evaluation CSV."
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE_PATH)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace an existing generated database at the exact target path.",
    )
    return parser.parse_args()


# Load the canonical CSV without inferring types or permitting a different schema.
def load_incident_rows(source_path: Path) -> list[dict[str, str]]:
    with source_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != _incident_columns():
            raise ValueError("Evaluation incident CSV schema does not match the fixture schema.")
        rows = [
            {column: row.get(column, "") or "" for column in _incident_columns()}
            for row in reader
        ]
    if not rows:
        raise ValueError("Evaluation incident CSV must contain at least one row.")
    if len({row["incident_id"] for row in rows}) != len(rows):
        raise ValueError("Evaluation incident CSV contains duplicate incident_id values.")
    return rows


# Build a dedicated SQLite file from the versioned CSV and return its inserted row count.
def build_database(
    *,
    source_path: Path,
    database_path: Path,
    replace: bool = False,
) -> int:
    if database_path.exists() and not replace:
        raise FileExistsError(
            f"Refusing to overwrite existing evaluation database: {database_path}"
        )
    rows = load_incident_rows(source_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    if database_path.exists():
        database_path.unlink()
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            """
            CREATE TABLE incidents (
                incident_id TEXT PRIMARY KEY,
                detected_at TEXT NOT NULL,
                service TEXT NOT NULL,
                severity TEXT NOT NULL,
                status TEXT NOT NULL,
                customer_impact TEXT NOT NULL,
                owner TEXT NOT NULL,
                mitigation_status TEXT NOT NULL,
                next_update_at TEXT NOT NULL
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO incidents(
                incident_id, detected_at, service, severity, status,
                customer_impact, owner, mitigation_status, next_update_at
            ) VALUES (
                :incident_id, :detected_at, :service, :severity, :status,
                :customer_impact, :owner, :mitigation_status, :next_update_at
            )
            """,
            rows,
        )
        connection.execute(
            "CREATE INDEX incidents_severity_status_idx ON incidents(severity, status)"
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return len(rows)


# Keep the fixture table shape in one canonical ordered list.
def _incident_columns() -> list[str]:
    return [
        "incident_id",
        "detected_at",
        "service",
        "severity",
        "status",
        "customer_impact",
        "owner",
        "mitigation_status",
        "next_update_at",
    ]


# Execute the bounded fixture generation command and print only non-sensitive metadata.
def main() -> int:
    args = parse_args()
    inserted_rows = build_database(
        source_path=args.source.resolve(),
        database_path=args.database.resolve(),
        replace=args.replace,
    )
    print(f"database={args.database.resolve()}")
    print(f"inserted_rows={inserted_rows}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
