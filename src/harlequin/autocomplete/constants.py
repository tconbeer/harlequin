from __future__ import annotations

from harlequin.autocomplete.completion import HarlequinCompletion

KEYWORDS = [
    "alter",
    "and",
    "as",
    "between",
    "by",
    "cascade",
    "case",
    "column",
    "copy",
    "create",
    "cross",
    "current",
    "database",
    "delete",
    "distinct",
    "drop",
    "end",
    "except",
    "exclude",
    "exists",
    "false",
    "filter",
    "following",
    "from",
    "full",
    "function",
    "grant",
    "group",
    "having",
    "if",
    "ilike",
    "inner",
    "insert",
    "intersect",
    "join",
    "lateral",
    "left",
    "like",
    "limit",
    "merge",
    "natural",
    "not",
    "offset",
    "on",
    "or",
    "order",
    "outer",
    "over",
    "owner",
    "partition",
    "preceding",
    "qualify",
    "range",
    "rename",
    "replace",
    "restrict",
    "revoke",
    "right",
    "row",
    "rows",
    "schema",
    "select",
    "sequence",
    "set",
    "similar",
    "table",
    "temp",
    "temporary",
    "then",
    "to",
    "top",
    "true",
    "truncate",
    "unbounded",
    "union",
    "update",
    "using",
    "view",
    "when",
    "where",
    "with",
]

FUNCTIONS = [
    ("abs", "fn"),
    ("ceil", "fn"),
    ("concat", "fn"),
    ("floor", "fn"),
    ("left", "fn"),
    ("lower", "fn"),
    ("ltrim", "fn"),
    ("regexp_extract", "fn"),
    ("regexp_replace", "fn"),
    ("replace", "fn"),
    ("right", "fn"),
    ("round", "fn"),
    ("rtrim", "fn"),
    ("sqrt", "fn"),
    ("avg", "agg"),
    ("bool_and", "agg"),
    ("bool_or", "agg"),
    ("count", "agg"),
    ("max", "agg"),
    ("min", "agg"),
    ("sum", "agg"),
]


def get_keywords() -> list[HarlequinCompletion]:
    """
    Returns a list of HarlequinCompletions.
    """
    keywords = [
        HarlequinCompletion(
            label=kw_name,
            type_label="kw",
            value=kw_name,
            priority=100,
            context=None,
        )
        for kw_name in KEYWORDS
    ]
    return keywords


def get_functions() -> list[HarlequinCompletion]:
    """
    Returns a list of HarlequinCompletions.
    """
    functions = [
        HarlequinCompletion(
            label=fn_name,
            type_label=fn_type,
            value=fn_name,
            priority=200,
            context=None,
        )
        for fn_name, fn_type in FUNCTIONS
    ]
    return functions
