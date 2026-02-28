with orders as (
    select * from {{ ref('stg_orders') }}
),

order_items as (
    select
        order_id,
        sum(item_total) as order_total,
        sum(quantity) as total_items
    from {{ ref('stg_order_items') }}
    group by order_id
),

customers as (
    select * from {{ ref('stg_customers') }}
)

select
    o.order_id,
    o.customer_id,
    c.full_name as customer_name,
    c.region as customer_region,
    o.order_date,
    o.status as order_status,
    coalesce(oi.order_total, 0) as order_total,
    coalesce(oi.total_items, 0) as total_items,
    case when o.status = 'completed' then 1 else 0 end as is_completed_order,
    case when o.status = 'returned' then 1 else 0 end as is_returned_order
from orders o
left join order_items oi on o.order_id = oi.order_id
inner join customers c on o.customer_id = c.customer_id
