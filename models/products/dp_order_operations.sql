-- Data product: Order Operations
-- Owner: operations-team | Domain: operations | Tier: silver
-- Schedule: hourly

select
  order_date,
  region as customer_region,
  status as order_status,
  count(order_id) as order_count,
  sum(is_completed_order) as completed_orders,
  sum(is_returned_order) as returned_orders,
  case
    when count(order_id) > 0
    then sum(is_returned_order) * 1.0 / count(order_id)
    else 0
  end as return_rate
from {{ ref('fct_orders') }}
group by order_date, region, status
order by order_date
