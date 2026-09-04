"""Timezone gate: every chart buckets/compares time in Europe/Moscow, never bare UTC or a
naive-vs-timestamptz mismatch. See docs/FORMAT.md, "Timezone", for the sanctioned idioms.

Called from validate._check_card, so it gates validate/diff/apply alike (mbcode/cli.py).

Native SQL cards (rules A-D) match against the query text with `--` comments stripped and
whitespace collapsed, so line-wrapped SQL doesn't dodge a check by accident. An inline
`-- tz-ok: <reason>` comment anywhere in the query exempts the whole card from A-D — use it
only for a column that is genuinely `timestamptz` already (session TZ handles it).

MBQL cards (rule E) are exempted by name in MBQL_TZ_OK below, since there is no SQL to
annotate; each entry must record why.
"""
from __future__ import annotations

import re

TZ_OK_RE = re.compile(r"--\s*tz-ok\s*:", re.IGNORECASE)

FORBIDDEN_PATTERNS = (
    ("CURRENT_DATE", re.compile(r"\bCURRENT_DATE\b", re.IGNORECASE)),
    ("CURRENT_TIMESTAMP", re.compile(r"\bCURRENT_TIMESTAMP\b", re.IGNORECASE)),
    ("LOCALTIMESTAMP", re.compile(r"\bLOCALTIMESTAMP\b", re.IGNORECASE)),
    ("::date", re.compile(r"::date\b", re.IGNORECASE)),
)

ALLOWED_ZONES = frozenset(("UTC", "Europe/Moscow"))
TZ_LITERAL_RE = re.compile(r"AT\s+TIME\s+ZONE\s*'([^']+)'", re.IGNORECASE)
BARE_NOW_RE = re.compile(r"\bnow\s*\(\s*\)(?!\s*AT\s+TIME\s+ZONE\b)", re.IGNORECASE)
DATE_TRUNC_CALL_RE = re.compile(r"date_trunc\s*\(", re.IGNORECASE)

# MBQL cards have no SQL to annotate; exempt by key with a reason instead. Empty once every
# GUI card buckets on a convert-timezone expression (see cards/pca-*.yaml for the pattern).
MBQL_TZ_OK: dict[str, str] = {}


def check_card_timezone(path, key, doc, problems):
    query = doc.get("dataset_query")
    if not isinstance(query, dict):
        return
    for stage in query.get("stages") or []:
        if not isinstance(stage, dict):
            continue
        if stage.get("lib/type") == "mbql.stage/native":
            _check_native_stage(path, stage, problems)
        if stage.get("lib/type") == "mbql.stage/mbql":
            _check_mbql_stage(path, key, stage, problems)


def _check_native_stage(path, stage, problems):
    sql = stage.get("native")
    if not isinstance(sql, str) or TZ_OK_RE.search(sql):
        return
    flat = _flatten_sql(sql)
    for label, pattern in FORBIDDEN_PATTERNS:
        if pattern.search(flat):
            problems.append(
                f"{path}: SQL uses {label} instead of an explicit AT TIME ZONE conversion")
    for zone in TZ_LITERAL_RE.findall(flat):
        if zone not in ALLOWED_ZONES:
            problems.append(f"{path}: AT TIME ZONE '{zone}' is not UTC or Europe/Moscow")
    if BARE_NOW_RE.search(flat):
        problems.append(
            f"{path}: bare now() — wrap as (now() AT TIME ZONE 'UTC') or 'Europe/Moscow', "
            "or mark the column tz-ok if it is already timestamptz")
    for arg in _call_args(flat, DATE_TRUNC_CALL_RE):
        if "AT TIME ZONE" not in arg.upper():
            problems.append(
                f"{path}: date_trunc(...) argument has no AT TIME ZONE conversion: "
                f"{arg.strip()[:80]}")


def _flatten_sql(sql):
    lines = (line.split("--", 1)[0] for line in sql.splitlines())
    return re.sub(r"\s+", " ", " ".join(lines))


def _call_args(flat, call_re):
    """Yield the balanced-paren argument text for every match of call_re in flat."""
    for match in call_re.finditer(flat):
        depth = 1
        i = match.end()
        while i < len(flat) and depth:
            depth += flat[i] == "("
            depth -= flat[i] == ")"
            i += 1
        yield flat[match.end():i - 1]


def _check_mbql_stage(path, key, stage, problems):
    if key in MBQL_TZ_OK:
        return
    moscow_exprs = _moscow_expression_names(stage)
    for clause in stage.get("breakout") or []:
        _check_breakout(path, clause, moscow_exprs, problems)
    for clause in _iter_filters(stage.get("filters") or []):
        _check_time_interval(path, clause, moscow_exprs, problems)


def _moscow_expression_names(stage):
    names = set()
    for clause in stage.get("expressions") or []:
        if not isinstance(clause, list) or len(clause) < 4 or clause[0] != "convert-timezone":
            continue
        if clause[3] != "Europe/Moscow" or not isinstance(clause[1], dict):
            continue
        names.add(clause[1].get("lib/expression-name"))
    return names


def _iter_filters(filters):
    for clause in filters:
        if not isinstance(clause, list) or not clause:
            continue
        if clause[0] in ("and", "or"):
            yield from _iter_filters(clause[2:])
            continue
        if clause[0] == "not":
            yield from _iter_filters(clause[2:3])
            continue
        yield clause


def _check_breakout(path, clause, moscow_exprs, problems):
    if not isinstance(clause, list) or len(clause) < 2:
        return
    tag, opts = clause[0], clause[1]
    if not isinstance(opts, dict) or "temporal-unit" not in opts:
        return
    _check_temporal_target(path, "breakout", tag, clause, moscow_exprs, problems)


def _check_time_interval(path, clause, moscow_exprs, problems):
    if not isinstance(clause, list) or clause[:1] != ["time-interval"] or len(clause) < 3:
        return
    target = clause[2]
    if not isinstance(target, list) or not target:
        return
    _check_temporal_target(path, "time-interval filter", target[0], target, moscow_exprs, problems)


def _check_temporal_target(path, where, tag, clause, moscow_exprs, problems):
    if tag == "field":
        problems.append(
            f"{path}: {where} buckets a raw field in report-timezone-agnostic SQL — add a "
            "convert-timezone expression to Europe/Moscow and breakout/filter on that instead")
        return
    if tag != "expression":
        return
    name = clause[2] if len(clause) > 2 else None
    if name not in moscow_exprs:
        problems.append(
            f"{path}: {where} expression {name!r} is not a convert-timezone to "
            "Europe/Moscow defined in this stage")
