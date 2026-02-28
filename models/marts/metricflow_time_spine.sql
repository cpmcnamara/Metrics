{{
    config(
        materialized = 'table',
    )
}}

with days as (
    {{
        dbt.date_spine(
            'day',
            "DATE '2024-01-01'",
            "DATE '2025-12-31'"
        )
    }}
)

select cast(date_day as date) as date_day
from days
