with source as (
    select * from {{ ref('raw_order_items') }}
)

select
    order_item_id,
    order_id,
    product_id,
    quantity,
    cast(unit_price as decimal(10, 2)) as unit_price,
    cast(quantity * unit_price as decimal(10, 2)) as item_total
from source
