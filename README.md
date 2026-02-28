# MetricFlow Testing Environment

A local, open-source MetricFlow (dbt) testing environment using DuckDB. No external database or dbt Cloud required.

## Overview

This project demonstrates MetricFlow's semantic layer with a sample e-commerce dataset:
- **Customers** - 10 customers across regions
- **Products** - 6 products in 3 categories
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

# 4. Run all example queries
make query-all
```

## Manual Commands

If you prefer running commands directly:

```bash
source venv/bin/activate

# Seed data and build models
dbt seed --profiles-dir .
dbt run --profiles-dir .

# List available metrics
mf list metrics --profiles-dir .

# Query a metric
mf query --metrics total_revenue --group-by metric_time__month --profiles-dir .

# Query with multiple dimensions
mf query --metrics total_revenue --group-by metric_time__month,order_id__customer_region --profiles-dir .

# Query with a where filter
mf query --metrics total_revenue --group-by metric_time__month --where "{{ TimeDimension('metric_time', 'month') }} >= '2024-06-01'" --profiles-dir .

# Validate semantic layer configs
mf validate-configs --profiles-dir .
```

## Project Structure

```
.
├── dbt_project.yml          # dbt project configuration
├── profiles.yml             # DuckDB connection profile
├── Makefile                 # Common commands
├── seeds/                   # CSV seed data
│   ├── raw_customers.csv
│   ├── raw_products.csv
│   ├── raw_orders.csv
│   └── raw_order_items.csv
└── models/
    ├── staging/             # Staging models (views)
    │   ├── stg_customers.sql
    │   ├── stg_products.sql
    │   ├── stg_orders.sql
    │   └── stg_order_items.sql
    └── marts/               # Mart models + semantic layer
        ├── fct_orders.sql
        ├── fct_order_items.sql
        ├── sem_fct_orders.yml        # Semantic model: orders
        ├── sem_fct_order_items.yml   # Semantic model: order items
        └── metrics.yml               # Metric definitions
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

- **Semantic Models**: Define entities, dimensions, and measures on fact tables
- **Simple Metrics**: Direct references to a single measure
- **Derived Metrics**: Combine multiple metrics with expressions (e.g., return rate)
- **Cumulative Metrics**: Running totals and rolling windows
- **Time Dimensions**: Automatic time-grain support (day, week, month, quarter, year)
- **Dimensional Slicing**: Group by any dimension (region, product category, etc.)
