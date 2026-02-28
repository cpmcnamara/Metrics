with order_items as (
    select * from {{ ref('stg_order_items') }}
),

orders as (
    select * from {{ ref('stg_orders') }}
),

products as (
    select * from {{ ref('stg_products') }}
),

customers as (
    select * from {{ ref('stg_customers') }}
)

select
    oi.order_item_id,
    oi.order_id,
    o.order_date,
    o.status as order_status,
    oi.product_id,
    p.product_name,
    p.category as product_category,
    o.customer_id,
    c.full_name as customer_name,
    c.region as customer_region,
    oi.quantity,
    oi.unit_price,
    oi.item_total,
    case when o.status = 'returned' then true else false end as is_returned,
    case when o.status = 'cancelled' then true else false end as is_cancelled
from order_items oi
inner join orders o on oi.order_id = o.order_id
inner join products p on oi.product_id = p.product_id
inner join customers c on o.customer_id = c.customer_id
