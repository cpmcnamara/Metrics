# Intent DSL Guide

A domain-specific language for defining business intents that compile to
MetricFlow YAML, LLM context, and an ontology graph.

## Overview

The Intent DSL is the **single authoring surface** for your analytics layer.
Business analysts write `.intent` files that describe what the business wants
to measure and why. The compiler generates everything downstream:

```
  .intent files (you write these)
       │
       ├──► MetricFlow YAML   (how to calculate)
       ├──► LLM context       (how to answer questions)
       ├──► Ontology graph     (how concepts relate)
       └──► Documentation      (how humans understand)
```

## File Structure

Intent files use the `.intent` extension and live in `intents/`.

```
intents/
├── metrics.intent        # metric definitions
├── revenue.intent        # revenue-related intents
├── operations.intent     # operational intents
└── customers.intent      # customer-related intents
```

---

## Language Reference

### 1. `source` — Define a Data Source

Maps to a MetricFlow semantic model. Declares the table, its entities,
dimensions, and measures.

```
source order_items
  table ref('fct_order_items')
  time order_date day

  entity order_item_id primary
  entity order_id foreign
  entity product_id foreign
  entity customer_id foreign

  dimension order_status categorical
  dimension product_name categorical
  dimension product_category categorical
  dimension customer_name categorical
  dimension customer_region categorical

  measure item_revenue sum(item_total)
  measure items_sold sum(quantity)
  measure avg_item_price average(unit_price)
  measure order_item_count count(order_item_id)
```

**Syntax:**

```
source <name>
  table <ref_expression>
  time <column> <granularity>

  entity <name> <primary|foreign>
  dimension <name> <categorical|time>
  measure <name> <sum|count|average|max|min>(<column>)
```

---

### 2. `metric` — Define a Metric

Declares a named metric with its calculation, description, and vocabulary.

#### Simple metric (wraps a single measure)

```
metric total_revenue
  description "Total revenue across all orders"
  also called "sales", "income", "earnings", "money made"
  from orders.order_revenue
  type simple
```

#### Derived metric (calculated from other metrics)

```
metric order_return_rate
  description "Percentage of orders that were returned"
  also called "return rate", "refund rate", "return percentage"
  formula total_returned_orders / total_order_count
  type derived
```

#### Cumulative metric

```
metric cumulative_revenue
  description "Running total of revenue over all time"
  also called "running total", "revenue to date", "accumulated revenue"
  from orders.order_revenue
  type cumulative
```

#### Cumulative metric with window

```
metric trailing_7d_revenue
  description "Rolling 7-day revenue window"
  also called "weekly rolling", "7-day revenue", "trailing week"
  from orders.order_revenue
  type cumulative
  window 7 days
```

**Syntax:**

```
metric <name>
  description "<text>"
  also called "<synonym1>", "<synonym2>", ...
  from <source>.<measure>           # for simple/cumulative
  formula <expression>              # for derived
  type <simple|derived|cumulative>
  window <N> <days|weeks|months>    # cumulative only
```

---

### 3. `intent` — Define a Business Intent

The core concept. An intent bundles metrics, dimensions, filters, documents,
and business context into a single answerable question.

```
intent "How is the business doing?"
  also asked "business overview", "executive summary", "how are we doing"
  show total_revenue, total_order_count, avg_order_value
  by month
  default

intent "What's our return problem?"
  also asked "return issues", "why are returns high", "refund analysis"
  show total_returned_orders, order_return_rate, total_revenue
  by region, month
  where status = returned
  reference "Return Policy" from docs/policies/returns.md
  reference "Q4 Return Analysis" from docs/analysis/q4-returns.pdf
  context "Return rate above 15% triggers executive review"
  context "West region has historically higher returns due to shipping damage"

intent "Who are our best customers?"
  also asked "top customers", "customer ranking", "biggest buyers"
  show total_revenue, total_order_count
  by customer
  sorted by total_revenue desc
  limit 20

intent "What's selling?"
  also asked "product performance", "category breakdown", "what products"
  show total_items_sold, total_revenue, revenue_per_item
  by product_category, month
  reference "Product Catalog" from docs/products/catalog.md
```

**Syntax:**

```
intent "<business question>"
  also asked "<phrase1>", "<phrase2>", ...
  show <metric1>, <metric2>, ...
  by <dimension1>, <dimension2>, ...
  where <dimension> = <value>
  sorted by <metric> <asc|desc>
  limit <N>
  reference "<name>" from <path>
  context "<business rule or note>"
  default                               # marks as the default/fallback intent
```

---

### 4. `group` — Group Intents by Domain

Optional organizational structure.

```
group "Revenue & Sales"
  includes
    "How is the business doing?"
    "What's selling?"

group "Operations"
  includes
    "What's our return problem?"

group "Customer Intelligence"
  includes
    "Who are our best customers?"
```

---

## Dimension Shorthands

The DSL supports shorthand names that map to full MetricFlow dimension paths:

| Shorthand    | Expands to                          |
|------------- |-------------------------------------|
| `month`      | `metric_time__month`                |
| `quarter`    | `metric_time__quarter`              |
| `week`       | `metric_time__week`                 |
| `day`        | `metric_time__day`                  |
| `year`       | `metric_time__year`                 |
| `region`     | `order_id__customer_region`         |
| `customer`   | `order_id__customer_name`           |
| `status`     | `order_id__order_status`            |
| `category`   | `order_item_id__product_category`   |
| `product`    | `order_item_id__product_name`       |

Shorthands are defined per-project and can be extended:

```
alias region = order_id__customer_region
alias category = order_item_id__product_category
```

---

## Filter Syntax

Filters in `where` clauses use simplified syntax that compiles to MetricFlow
where expressions:

| DSL                          | Compiles to                                                    |
|------------------------------|----------------------------------------------------------------|
| `status = completed`         | `{{ Dimension('order_id__order_status') }} = 'completed'`      |
| `region = us_west`           | `{{ Dimension('order_id__customer_region') }} = 'us_west'`     |
| `time >= 2024-06-01`         | `{{ TimeDimension('metric_time', 'month') }} >= '2024-06-01'`  |
| `category in (widgets, gadgets)` | `{{ Dimension('...') }} in ('widgets', 'gadgets')`         |

---

## Compilation Targets

### Target 1: MetricFlow YAML

`metric` and `source` blocks compile directly to MetricFlow semantic model
and metric YAML files. `intent` blocks compile to saved queries.

```bash
intent-compile --target metricflow intents/ -o models/marts/
```

### Target 2: LLM Context

All blocks compile to a structured prompt that the LLM receives at query time.
Metric descriptions and synonyms become the LLM's vocabulary. Intent definitions
become few-shot examples.

```bash
intent-compile --target llm-context intents/ -o apps/llm_context.json
```

Output:

```json
{
  "metrics": [
    {
      "name": "total_revenue",
      "description": "Total revenue across all orders",
      "synonyms": ["sales", "income", "earnings", "money made"]
    }
  ],
  "intents": [
    {
      "question": "How is the business doing?",
      "also_asked": ["business overview", "executive summary"],
      "params": {
        "metrics": "total_revenue,total_order_count,avg_order_value",
        "group_by": "metric_time__month"
      },
      "references": [],
      "context": []
    }
  ],
  "aliases": {
    "region": "order_id__customer_region",
    "category": "order_item_id__product_category"
  }
}
```

### Target 3: Ontology Graph

All blocks compile to a graph of nodes and edges. Can output as JSON, DOT
(Graphviz), or load into Neo4j.

```bash
intent-compile --target ontology intents/ -o docs/ontology.json
```

Output:

```json
{
  "nodes": [
    {"id": "intent:business_overview", "type": "intent", "label": "How is the business doing?"},
    {"id": "metric:total_revenue", "type": "metric", "label": "Total Revenue"},
    {"id": "dimension:month", "type": "dimension", "label": "Month"},
    {"id": "doc:return_policy", "type": "document", "label": "Return Policy"}
  ],
  "edges": [
    {"from": "intent:business_overview", "to": "metric:total_revenue", "relation": "uses"},
    {"from": "intent:business_overview", "to": "dimension:month", "relation": "sliced_by"},
    {"from": "intent:return_problem", "to": "doc:return_policy", "relation": "references"},
    {"from": "metric:order_return_rate", "to": "metric:total_returned_orders", "relation": "depends_on"},
    {"from": "metric:order_return_rate", "to": "metric:total_order_count", "relation": "depends_on"}
  ]
}
```

### Target 4: Documentation

Generates human-readable documentation from the intent definitions.

```bash
intent-compile --target docs intents/ -o docs/metrics-catalog.md
```

---

## Validation Rules

The compiler enforces:

1. **Every metric in an intent must be defined** — no dangling references
2. **Every dimension shorthand must resolve** — typos are caught at compile time
3. **Derived metric formulas must reference existing metrics** — dependency cycles are rejected
4. **Every source must have a time dimension** — MetricFlow requires this
5. **Document references must point to existing files** — broken links are flagged
6. **No duplicate metric or intent names** — names are unique identifiers
7. **At most one `default` intent** — used as the fallback when the LLM can't match

---

## Complete Example

A single file that defines everything for the order analytics domain:

```
# ── Sources ──────────────────────────────────────────

source orders
  table ref('fct_orders')
  time order_date day

  entity order_id primary
  entity customer_id foreign

  dimension order_status categorical
  dimension customer_region categorical
  dimension customer_name categorical

  measure order_revenue sum(order_total)
  measure order_count count(order_id)
  measure average_order_value average(order_total)
  measure total_items_sold sum(total_items)
  measure completed_orders sum(is_completed_order)
  measure returned_orders sum(is_returned_order)

source order_items
  table ref('fct_order_items')
  time order_date day

  entity order_item_id primary
  entity order_id foreign

  dimension product_name categorical
  dimension product_category categorical

  measure item_revenue sum(item_total)
  measure items_sold sum(quantity)

# ── Aliases ──────────────────────────────────────────

alias month = metric_time__month
alias quarter = metric_time__quarter
alias week = metric_time__week
alias day = metric_time__day
alias year = metric_time__year
alias region = order_id__customer_region
alias customer = order_id__customer_name
alias status = order_id__order_status
alias category = order_item_id__product_category
alias product = order_item_id__product_name

# ── Metrics ──────────────────────────────────────────

metric total_revenue
  description "Total revenue across all orders"
  also called "sales", "income", "earnings", "money made"
  from orders.order_revenue
  type simple

metric total_order_count
  description "Total number of orders placed"
  also called "order volume", "number of orders", "how many orders"
  from orders.order_count
  type simple

metric avg_order_value
  description "Average value per order"
  also called "AOV", "basket size", "average purchase", "average basket"
  from orders.average_order_value
  type simple

metric total_items_sold
  description "Total quantity of items sold"
  also called "units sold", "items shipped", "volume sold"
  from orders.total_items_sold
  type simple

metric total_completed_orders
  description "Number of orders successfully fulfilled"
  also called "fulfilled orders", "successful orders"
  from orders.completed_orders
  type simple

metric total_returned_orders
  description "Number of orders returned by customers"
  also called "returns", "refunds", "orders sent back"
  from orders.returned_orders
  type simple

metric order_return_rate
  description "Percentage of orders that were returned"
  also called "return rate", "refund rate", "return percentage"
  formula total_returned_orders / total_order_count
  type derived

metric revenue_per_item
  description "Average revenue generated per item sold"
  also called "per item revenue", "revenue per unit", "dollars per item"
  formula total_revenue / total_items_sold
  type derived

metric cumulative_revenue
  description "Running total of revenue over all time"
  also called "running total", "revenue to date", "accumulated revenue"
  from orders.order_revenue
  type cumulative

metric trailing_7d_revenue
  description "Rolling 7-day revenue window"
  also called "weekly rolling", "7-day revenue", "trailing week revenue"
  from orders.order_revenue
  type cumulative
  window 7 days

# ── Intents ──────────────────────────────────────────

intent "How is the business doing?"
  also asked "business overview", "executive summary", "how are we doing"
  show total_revenue, total_order_count, avg_order_value
  by month
  default

intent "What's our return problem?"
  also asked "return issues", "why are returns high", "refund analysis"
  show total_returned_orders, order_return_rate, total_revenue
  by region, month
  where status = returned
  reference "Return Policy" from docs/policies/returns.md
  context "Return rate above 15% triggers executive review"
  context "West region has historically higher returns due to shipping damage"

intent "Who are our best customers?"
  also asked "top customers", "customer ranking", "biggest buyers", "VIPs"
  show total_revenue, total_order_count
  by customer
  sorted by total_revenue desc
  limit 20

intent "What's selling?"
  also asked "product performance", "category breakdown", "what products sell"
  show total_items_sold, total_revenue, revenue_per_item
  by category, month
  reference "Product Catalog" from docs/products/catalog.md

intent "How is revenue trending?"
  also asked "revenue trend", "revenue over time", "growth", "trajectory"
  show total_revenue, cumulative_revenue, trailing_7d_revenue
  by month
  context "Board expects 10% QoQ growth"

intent "How does each region perform?"
  also asked "regional breakdown", "geography", "regional performance"
  show total_revenue, total_order_count
  by region, month

# ── Groups ───────────────────────────────────────────

group "Executive"
  includes
    "How is the business doing?"
    "How is revenue trending?"

group "Operations"
  includes
    "What's our return problem?"
    "How does each region perform?"

group "Product & Sales"
  includes
    "What's selling?"
    "Who are our best customers?"
```
