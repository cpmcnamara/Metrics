with source as (
    select * from {{ ref('raw_customers') }}
)

select
    customer_id,
    first_name,
    last_name,
    first_name || ' ' || last_name as full_name,
    email,
    cast(created_at as date) as customer_created_at,
    region
from source
