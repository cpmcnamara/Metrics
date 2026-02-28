-- Data product: Product Intelligence
-- Owner: product-analytics | Domain: product | Tier: silver
-- Depends on: Revenue Analytics
-- Schedule: daily 08:00 UTC

select
  order_date,
  category as product_category,
  customer_name,
  sum(quantity) as items_sold,
  sum(item_total) as item_revenue,
  case
    when sum(quantity) > 0
    then sum(item_total) * 1.0 / sum(quantity)
    else 0
  end as revenue_per_item
from {{ ref('fct_order_items') }}
group by order_date, category, customer_name
order by order_date
