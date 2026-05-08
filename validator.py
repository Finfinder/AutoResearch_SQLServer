# validator.py
import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID

import sqlglot


@dataclass
class ValidationResult:
    is_valid: bool
    base_count: int | None
    variant_count: int | None
    message: str
    mode: str = "row_count"
    ordered: bool = False
    strict_requested: bool = False
    strict_applied: bool = False
    strict_source: str | None = None
    fallback_reason: str | None = None
    warnings: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "base_count": self.base_count,
            "variant_count": self.variant_count,
            "message": self.message,
            "mode": self.mode,
            "ordered": self.ordered,
            "strict_requested": self.strict_requested,
            "strict_applied": self.strict_applied,
            "strict_source": self.strict_source,
            "fallback_reason": self.fallback_reason,
            "warnings": self.warnings or [],
        }


_OPTION_RE = re.compile(r"\s*OPTION\s*\([^)]*\)\s*$", re.IGNORECASE)
_STRICT_UNSUPPORTED_TYPE_MARKERS = ("text", "ntext", "image")
_STRICT_XML_TYPE_MARKER = "xml"
_STRICT_SUPPORTED_SQL_TYPES = {
    "bigint",
    "binary",
    "bit",
    "char",
    "date",
    "datetime",
    "datetime2",
    "datetimeoffset",
    "decimal",
    "float",
    "int",
    "money",
    "nchar",
    "numeric",
    "nvarchar",
    "real",
    "smalldatetime",
    "smallint",
    "smallmoney",
    "time",
    "tinyint",
    "uniqueidentifier",
    "varbinary",
    "varchar",
    "xml",
}
STRICT_LOB_MAX_BYTES = 1_000_000


class StrictValidationUnavailable(Exception):
    def __init__(self, reason, warnings=None):
        super().__init__(reason)
        self.reason = reason
        self.warnings = warnings or []


def _strip_option_clause(query):
    return _OPTION_RE.sub("", query)


def _strip_top_level_order_clause(query):
    clean = _strip_option_clause(query)
    try:
        ast = sqlglot.parse_one(clean, dialect="tsql")
    except Exception:
        return clean

    if not ast.args.get("order"):
        return clean
    if ast.args.get("limit") or ast.args.get("offset"):
        return clean

    ast = ast.copy()
    ast.set("order", None)
    return ast.sql(dialect="tsql")


def _type_name_from_code(type_code):
    if type_code is None:
        return ""
    if hasattr(type_code, "__name__"):
        return type_code.__name__.lower()
    return str(type_code).lower()


def _normalize_sql_type_name(type_name):
    if not type_name:
        return ""
    normalized = type_name.strip().lower()
    if "(" in normalized:
        normalized = normalized.split("(", 1)[0]
    if " " in normalized:
        normalized = normalized.split(" ", 1)[0]
    return normalized


def _fetch_sql_type_names(query, conn):
    clean = _strip_option_clause(query)
    cursor = conn.cursor()
    try:
        cursor.execute("EXEC sys.sp_describe_first_result_set @tsql = ?", clean)
        description = cursor.description or []
        rows = cursor.fetchall()
    except Exception as exc:
        raise StrictValidationUnavailable(
            f"Strict validation skipped — result-set metadata lookup failed: {exc}",
            warnings=[f"Strict validation metadata lookup failed: {exc}"],
        ) from exc
    finally:
        cursor.close()

    column_map = {column[0].lower(): index for index, column in enumerate(description) if column and column[0]}
    ordinal_index = column_map.get("column_ordinal")
    type_name_index = column_map.get("system_type_name")
    if ordinal_index is None or type_name_index is None:
        raise StrictValidationUnavailable(
            "Strict validation skipped — result-set metadata is unavailable",
            warnings=["Strict validation metadata is unavailable"],
        )

    sql_types = {}
    for row in rows:
        ordinal = row[ordinal_index]
        type_name = row[type_name_index]
        if ordinal is None or type_name is None:
            continue
        sql_types[int(ordinal) - 1] = str(type_name).lower()

    if not sql_types:
        raise StrictValidationUnavailable(
            "Strict validation skipped — result-set metadata is unavailable",
            warnings=["Strict validation metadata is unavailable"],
        )

    max_index = max(sql_types)
    return [sql_types.get(index, "") for index in range(max_index + 1)]


def _has_explicit_order_by(query):
    clean = _strip_option_clause(query)
    ast = sqlglot.parse_one(clean, dialect="tsql")
    return bool(ast.args.get("order"))


def _estimate_value_size(value):
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    if isinstance(value, memoryview):
        return len(value.tobytes())
    if isinstance(value, (bytes, bytearray)):
        return len(value)
    return 0


def _normalize_exact_numeric(value):
    normalized = value.normalize() if isinstance(value, Decimal) else Decimal(value)
    normalized = normalized if normalized != normalized.to_integral() else normalized.quantize(Decimal("1"))
    return format(normalized, "f")


def _normalize_float(value):
    if math.isnan(value):
        return "nan"
    if math.isinf(value) and value > 0:
        return "inf"
    if math.isinf(value) and value < 0:
        return "-inf"
    return format(value, ".17g")


def _normalize_value(value):
    if value is None:
        return ["null", None]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, int) and not isinstance(value, bool):
        return ["num", _normalize_exact_numeric(value)]
    if isinstance(value, Decimal):
        return ["num", _normalize_exact_numeric(value)]
    if isinstance(value, float):
        return ["float", _normalize_float(value)]
    if isinstance(value, datetime):
        return ["datetime", value.isoformat()]
    if isinstance(value, date):
        return ["date", value.isoformat()]
    if isinstance(value, time):
        return ["time", value.isoformat()]
    if isinstance(value, UUID):
        return ["uuid", str(value)]
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, (bytes, bytearray)):
        return ["bytes", bytes(value).hex()]
    return [type(value).__name__.lower(), str(value)]


def _ensure_strict_supported(value, type_name, column_index):
    normalized_type_name = _normalize_sql_type_name(type_name)

    if any(marker in normalized_type_name for marker in _STRICT_UNSUPPORTED_TYPE_MARKERS):
        raise StrictValidationUnavailable(
            f"Strict validation skipped — unsupported SQL type in column {column_index}: {type_name or 'unknown'}",
            warnings=[f"Unsupported SQL type for strict validation: {type_name or 'unknown'}"],
        )

    if normalized_type_name and normalized_type_name not in _STRICT_SUPPORTED_SQL_TYPES:
        raise StrictValidationUnavailable(
            f"Strict validation skipped — unsupported SQL type in column {column_index}: {type_name or 'unknown'}",
            warnings=[f"Unsupported SQL type for strict validation: {type_name or 'unknown'}"],
        )

    if _estimate_value_size(value) > STRICT_LOB_MAX_BYTES:
        raise StrictValidationUnavailable(
            (
                f"Strict validation skipped — LOB value in column {column_index} exceeds "
                f"{STRICT_LOB_MAX_BYTES} bytes"
            ),
            warnings=[f"LOB value exceeds strict validation threshold in column {column_index}"],
        )

    if normalized_type_name == _STRICT_XML_TYPE_MARKER and not isinstance(value, (str, type(None))):
        raise StrictValidationUnavailable(
            f"Strict validation skipped — XML value in column {column_index} is not text-serializable",
            warnings=[f"Unsupported XML runtime representation in column {column_index}"],
        )


def _normalize_row(row, description, sql_type_names=None):
    normalized = []
    for column_index, value in enumerate(row):
        type_name = ""
        if sql_type_names and column_index < len(sql_type_names) and sql_type_names[column_index]:
            type_name = sql_type_names[column_index]
        elif description and column_index < len(description):
            type_name = _type_name_from_code(description[column_index][1])
        _ensure_strict_supported(value, type_name, column_index)
        normalized.append(_normalize_value(value))
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _build_strict_signature(query, conn, ordered):
    clean = _strip_option_clause(query)
    sql_type_names = _fetch_sql_type_names(clean, conn)
    cursor = conn.cursor()
    try:
        cursor.execute(clean)
        description = cursor.description or []
        normalized_rows = []
        row_count = 0

        while True:
            row = cursor.fetchone()
            if row is None:
                break
            normalized_rows.append(_normalize_row(row, description, sql_type_names))
            row_count += 1
    finally:
        cursor.close()

    if not ordered:
        normalized_rows.sort()

    digest = hashlib.sha256()
    for normalized_row in normalized_rows:
        digest.update(normalized_row)
        digest.update(b"\x1e")

    return digest.hexdigest(), row_count


def build_strict_validation_context(base_query, conn):
    try:
        ordered = _has_explicit_order_by(base_query)
    except Exception as exc:
        return {
            "ordered": False,
            "base_signature": None,
            "base_row_count": None,
            "fallback_reason": f"Could not analyze ORDER BY for strict validation: {exc}",
            "warnings": [f"Strict validation disabled: ORDER BY analysis failed: {exc}"],
        }

    try:
        base_signature, base_row_count = _build_strict_signature(base_query, conn, ordered)
        return {
            "ordered": ordered,
            "base_signature": base_signature,
            "base_row_count": base_row_count,
            "fallback_reason": None,
            "warnings": [],
        }
    except StrictValidationUnavailable as exc:
        return {
            "ordered": ordered,
            "base_signature": None,
            "base_row_count": None,
            "fallback_reason": exc.reason,
            "warnings": exc.warnings,
        }
    except Exception as exc:
        return {
            "ordered": ordered,
            "base_signature": None,
            "base_row_count": None,
            "fallback_reason": f"Strict validation setup failed: {exc}",
            "warnings": [f"Strict validation setup failed: {exc}"],
        }


def _with_row_count_fallback(
    base_count,
    variant_query,
    conn,
    *,
    ordered,
    strict_requested,
    strict_source,
    fallback_reason,
    warnings=None,
):
    if base_count is None:
        return ValidationResult(
            is_valid=True,
            base_count=None,
            variant_count=-1,
            message=f"Strict validation skipped — {fallback_reason}; base row count unavailable",
            mode="row_count",
            ordered=ordered,
            strict_requested=strict_requested,
            strict_applied=False,
            strict_source=strict_source,
            fallback_reason=fallback_reason,
            warnings=warnings or [],
        )

    result = validate_row_count(base_count, variant_query, conn)
    result.ordered = ordered
    result.strict_requested = strict_requested
    result.strict_source = strict_source
    result.strict_applied = False
    result.fallback_reason = fallback_reason
    result.warnings = (warnings or []) + (result.warnings or [])
    if result.message == "OK":
        result.message = f"Strict validation skipped — {fallback_reason}; row count OK"
    else:
        result.message = f"Strict validation skipped — {fallback_reason}; {result.message}"
    return result


def get_row_count(query, conn):
    clean = _strip_top_level_order_clause(query)
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
            warnings=[f"COUNT(*) failed: {exc}"],
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


def validate_query_results(
    base_count,
    variant_query,
    conn,
    *,
    strict_requested=False,
    strict_source=None,
    strict_context=None,
):
    if not strict_requested:
        result = validate_row_count(base_count, variant_query, conn)
        result.strict_requested = False
        result.strict_source = None
        return result

    strict_context = strict_context or {}
    ordered = strict_context.get("ordered", False)
    base_signature = strict_context.get("base_signature")
    context_warnings = list(strict_context.get("warnings", []))
    fallback_reason = strict_context.get("fallback_reason")
    effective_base_count = base_count
    if effective_base_count is None:
        effective_base_count = strict_context.get("base_row_count")

    if not base_signature:
        reason = fallback_reason or "Strict validation context is unavailable"
        return _with_row_count_fallback(
            effective_base_count,
            variant_query,
            conn,
            ordered=ordered,
            strict_requested=True,
            strict_source=strict_source,
            fallback_reason=reason,
            warnings=context_warnings,
        )

    try:
        variant_signature, variant_count = _build_strict_signature(variant_query, conn, ordered)
    except StrictValidationUnavailable as exc:
        return _with_row_count_fallback(
            effective_base_count,
            variant_query,
            conn,
            ordered=ordered,
            strict_requested=True,
            strict_source=strict_source,
            fallback_reason=exc.reason,
            warnings=context_warnings + exc.warnings,
        )
    except Exception as exc:
        return _with_row_count_fallback(
            effective_base_count,
            variant_query,
            conn,
            ordered=ordered,
            strict_requested=True,
            strict_source=strict_source,
            fallback_reason=f"Strict validation failed: {exc}",
            warnings=context_warnings + [f"Strict validation failed: {exc}"],
        )

    if variant_signature != base_signature:
        same_count = effective_base_count == variant_count
        message = (
            "Strict hash mismatch: base and variant return different result sets"
            if same_count
            else (
                f"Strict hash mismatch: base={effective_base_count}, variant={variant_count} — "
                "variant does not return semantically equivalent results."
            )
        )
        return ValidationResult(
            is_valid=False,
            base_count=effective_base_count,
            variant_count=variant_count,
            message=message,
            mode="strict_hash",
            ordered=ordered,
            strict_requested=True,
            strict_applied=True,
            strict_source=strict_source,
            warnings=context_warnings,
        )

    return ValidationResult(
        is_valid=True,
        base_count=effective_base_count,
        variant_count=variant_count,
        message="OK",
        mode="strict_hash",
        ordered=ordered,
        strict_requested=True,
        strict_applied=True,
        strict_source=strict_source,
        warnings=context_warnings,
    )
