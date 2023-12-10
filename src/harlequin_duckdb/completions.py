from __future__ import annotations

import duckdb


def get_completion_data(
    cur: duckdb.DuckDBPyConnection,
) -> list[tuple[str, str, int, str | None]]:
    keyword_data = cur.execute(
        """
        select distinct
            keyword_name as label,
            'kw' as type_label,
            case when keyword_category = 'reserved' then 100 else 1000 end as priority,
            null as context
        from duckdb_keywords()
        """
    ).fetchall()

    function_data = cur.execute(
        """
        select distinct
            function_name as label,
            case when function_type = 'pragma' then 'pragma'
                when function_type = 'macro' then 'macro'
                when function_type = 'aggregate' then 'agg'
                when function_type = 'scalar' then 'fn'
                when function_type = 'table' then 'fn->T'
                else 'fn' end as type_label,
            1000 as priority,
            case 
                when database_name == 'system' then null 
                else schema_name 
            end as context
        from duckdb_functions()
        where database_name != 'temp'
        """
    ).fetchall()

    settings_data = cur.execute(
        """
        select distinct
            name as label,
            'set' as type_label,
            2000 as priority,
            null as context
        from duckdb_settings()
        """
    ).fetchall()

    type_data = cur.execute(
        """
        with
            system_types as (
                select distinct
                    type_name as label, 
                    'type' as type_label, 
                    1000 as priority, 
                    null as context
                from duckdb_types()
                where database_name = 'system'
            ),
            custom_types as (
                select distinct
                    type_name as label,
                    'type' as type_label,
                    1000 as priority,
                    schema_name as context
                from duckdb_types()
                where
                    database_name not in ('system', 'temp')
                    and type_name not in (select label from system_types)
            )
        select *
        from system_types
        union all
        select *
        from custom_types
        """
    ).fetchall()

    return [
        *keyword_data,
        *function_data,
        *settings_data,
        *type_data,
    ]
