-- Data product: Revenue Analytics
-- Owner: analytics-engineering | Domain: finance | Tier: gold
-- Schedule: daily 06:00 UTC

select
  order_date,
  sum(order_total) as order_revenue,
  avg(order_total) as average_order_value,
  count(order_id) as order_count
from {{ ref('fct_orders') }}
group by order_date
order by order_date
