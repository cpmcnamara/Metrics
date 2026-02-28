# MetricFlow Testing Environment

A local, open-source MetricFlow (dbt) testing environment using DuckDB. No external database or dbt Cloud required.

Includes a **Streamlit dashboard**, a **"chat with your data" interface**, and **export utilities** to demonstrate real-world use cases.

## Overview

This project demonstrates MetricFlow's semantic layer with a sample e-commerce dataset:
- **Customers** - 10 customers across 3 regions
- **Products** - 6 products in 3 categories (widgets, gadgets, gizmos)
- **Orders** - 25 orders spanning March–November 2024
- **Order Items** - 42 line items

## Quick Start

```bash
# 1. Set up the environment
make setup

# 2. Build everything (seeds + models)
make build

# 3. Run an example query
make query-revenue

# 4. Launch the dashboard
make dashboard

# 5. Launch chat-with-your-data
make chat
```

## Use Cases

### 1. Interactive Dashboard (`make dashboard`)

A Streamlit dashboard with 4 pages:
- **Revenue Overview** - KPI cards, monthly trends, AOV, return rates
- **Regional Breakdown** - Pie charts, bar charts, regional time series
- **Product Analysis** - Category sales and trends
- **Custom Query Builder** - Pick any metric + dimension combo, auto-visualized

```bash
streamlit run apps/dashboard.py
```

### 2. Chat with Your Data (`make chat`)

Ask questions in plain English and get MetricFlow-powered answers with auto-generated charts:

```
"What was our monthly revenue?"
"Show me revenue by region"
"What's the return rate over time?"
"How many items sold by product category?"
"Revenue and order count by quarter"
"Show me US West revenue by month"
```

Works with a rule-based parser out of the box. Set `ANTHROPIC_API_KEY` for full Claude-powered natural language understanding.

```bash
# Basic mode (rule-based)
streamlit run apps/chat_with_data.py

# With Claude AI (full NLU)
export ANTHROPIC_API_KEY=sk-ant-...
streamlit run apps/chat_with_data.py
```

### 3. Export Metrics (`make export`)

Programmatically export metrics to CSV, JSON, or Parquet for downstream consumption:

```bash
# CSV to stdout
python apps/export_metrics.py --metrics total_revenue --group-by metric_time__month --format csv

# JSON to file
python apps/export_metrics.py --metrics total_revenue,total_order_count --group-by metric_time__month --format json -o metrics.json

# Parquet for data pipelines
python apps/export_metrics.py --metrics total_revenue --group-by order_id__customer_region --format parquet -o revenue.parquet
```

### 4. Saved Queries

Pre-defined, reusable query configurations in `models/marts/saved_queries.yml`:
- `monthly_revenue_overview` - Executive-level monthly KPIs
- `regional_performance` - Revenue by region over time
- `product_category_sales` - Product category trends
- `order_quality` - Return rate and completion metrics

### 5. CLI Queries

```bash
source venv/bin/activate

# List all metrics and their dimensions
mf list metrics

# Multi-metric query
mf query --metrics total_revenue,total_order_count,avg_order_value --group-by metric_time__month

# Dimensional slicing
mf query --metrics total_revenue --group-by metric_time__month,order_id__customer_region

# Filtered query
mf query --metrics total_revenue --group-by metric_time__month --where "{{ Dimension('order_id__customer_region') }} = 'us_west'"

# See generated SQL
mf query --metrics order_return_rate --group-by metric_time__month --explain

# Validate all configs
mf validate-configs
```

## Project Structure

```
.
├── dbt_project.yml              # dbt project configuration
├── profiles.yml                 # DuckDB connection profile
├── Makefile                     # All commands
├── seeds/                       # CSV seed data
│   ├── raw_customers.csv
│   ├── raw_products.csv
│   ├── raw_orders.csv
│   └── raw_order_items.csv
├── models/
│   ├── staging/                 # Staging models (views)
│   │   ├── stg_customers.sql
│   │   ├── stg_products.sql
│   │   ├── stg_orders.sql
│   │   └── stg_order_items.sql
│   └── marts/                   # Mart models + semantic layer
│       ├── fct_orders.sql
│       ├── fct_order_items.sql
│       ├── metricflow_time_spine.sql
│       ├── _models.yml              # Time spine config
│       ├── sem_fct_orders.yml       # Semantic model: orders
│       ├── sem_fct_order_items.yml  # Semantic model: order items
│       ├── metrics.yml              # Metric definitions
│       └── saved_queries.yml        # Pre-defined queries
└── apps/
    ├── dashboard.py             # Streamlit dashboard
    ├── chat_with_data.py        # Natural language query interface
    └── export_metrics.py        # CLI export utility
```

## Available Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `total_revenue` | Simple | Total revenue across all orders |
| `total_order_count` | Simple | Total number of orders |
| `avg_order_value` | Simple | Average value per order |
| `total_items_sold` | Simple | Total quantity of items sold |
| `total_completed_orders` | Simple | Completed order count |
| `total_returned_orders` | Simple | Returned order count |
| `order_return_rate` | Derived | % of orders returned |
| `revenue_per_item` | Derived | Revenue / items sold |
| `cumulative_revenue` | Cumulative | Running total of revenue |
| `trailing_7d_revenue` | Cumulative | Rolling 7-day revenue |

## Key Concepts Demonstrated

- **Semantic Models** - Entities, dimensions, and measures on fact tables
- **Simple Metrics** - Direct references to a single measure
- **Derived Metrics** - Combine multiple metrics with expressions
- **Cumulative Metrics** - Running totals and rolling windows
- **Saved Queries** - Reusable, governed query definitions
- **Time Spine** - Required for cumulative and time-based metrics
- **Dashboard Integration** - Streamlit reading from the semantic layer
- **Conversational Analytics** - Natural language to MetricFlow queries
- **Programmatic Export** - CSV/JSON/Parquet for downstream systems
