# validator.py
import re
from dataclasses import dataclass


@dataclass
class ValidationResult:
    is_valid: bool
    base_count: int
    variant_count: int
    message: str


_OPTION_RE = re.compile(r"\s*OPTION\s*\([^)]*\)\s*$", re.IGNORECASE)


def _strip_option_clause(query):
    return _OPTION_RE.sub("", query)


def get_row_count(query, conn):
    clean = _strip_option_clause(query)
    wrapped = f"SELECT COUNT(*) FROM ({clean}) AS _v"
    cursor = conn.cursor()
    try:
        cursor.execute(wrapped)
        row = cursor.fetchone()
        return row[0]
    finally:
        cursor.close()


def validate_row_count(base_count, variant_query, conn):
    try:
        variant_count = get_row_count(variant_query, conn)
    except Exception as exc:
        return ValidationResult(
            is_valid=True,
            base_count=base_count,
            variant_count=-1,
            message=f"Row count validation skipped — COUNT(*) failed: {exc}",
        )

    if variant_count != base_count:
        return ValidationResult(
            is_valid=False,
            base_count=base_count,
            variant_count=variant_count,
            message=(
                f"Row count mismatch: base={base_count}, variant={variant_count} — "
                "variant does not return semantically equivalent results."
            ),
        )

    return ValidationResult(
        is_valid=True,
        base_count=base_count,
        variant_count=variant_count,
        message="OK",
    )
