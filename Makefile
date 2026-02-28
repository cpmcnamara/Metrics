.PHONY: setup build seed run test query-all clean help dashboard chat export catalog compile-products compile-all

ACTIVATE = . venv/bin/activate &&
DBT = $(ACTIVATE) dbt --profiles-dir .
MF = $(ACTIVATE) mf

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

setup: ## Create venv and install dependencies
	python3 -m venv venv
	$(ACTIVATE) pip install --upgrade pip setuptools wheel
	$(ACTIVATE) pip install dbt-core dbt-duckdb dbt-metricflow streamlit plotly

seed: ## Load seed data into DuckDB
	$(DBT) seed

build: seed ## Build all models (seeds + models)
	$(DBT) run

test-dbt: build ## Run dbt tests
	$(DBT) test

parse: ## Parse project and validate semantic layer
	$(DBT) parse

list-metrics: build ## List all available metrics
	$(MF) list metrics

list-dimensions: build ## List all dimensions for a metric
	$(MF) list dimensions --metrics total_revenue

query-revenue: build ## Query total revenue by month
	$(MF) query --metrics total_revenue --group-by metric_time__month

query-orders: build ## Query order count by region
	$(MF) query --metrics total_order_count --group-by order_id__customer_region

query-aov: build ## Query average order value by month
	$(MF) query --metrics avg_order_value --group-by metric_time__month

query-return-rate: build ## Query the derived return rate metric
	$(MF) query --metrics order_return_rate --group-by metric_time__month

query-cumulative: build ## Query cumulative revenue over time
	$(MF) query --metrics cumulative_revenue --group-by metric_time__month

query-all: build ## Run all example queries
	@echo "\n=== Total Revenue by Month ==="
	$(MF) query --metrics total_revenue --group-by metric_time__month
	@echo "\n=== Order Count by Region ==="
	$(MF) query --metrics total_order_count --group-by order_id__customer_region
	@echo "\n=== Average Order Value by Month ==="
	$(MF) query --metrics avg_order_value --group-by metric_time__month
	@echo "\n=== Return Rate by Month ==="
	$(MF) query --metrics order_return_rate --group-by metric_time__month

validate: build ## Validate the semantic layer configuration
	$(MF) validate-configs

dashboard: build ## Launch Streamlit metrics dashboard
	$(ACTIVATE) streamlit run apps/dashboard.py

chat: build ## Launch chat-with-your-data interface
	$(ACTIVATE) streamlit run apps/chat_with_data.py

export: build ## Export revenue metrics to CSV (example)
	$(ACTIVATE) python apps/export_metrics.py --metrics total_revenue,total_order_count --group-by metric_time__month --format csv

catalog: build ## Launch data product catalog UI
	$(ACTIVATE) streamlit run apps/data_product_server.py

compile-products: ## Compile intent files to product manifests and contracts
	$(ACTIVATE) python -m apps.intent_compiler intents/ --target products -o build
	$(ACTIVATE) python -m apps.intent_compiler intents/ --target contracts -o build
	$(ACTIVATE) python -m apps.intent_compiler intents/ --target catalog -o build

compile-all: ## Compile intent files to all targets
	$(ACTIVATE) python -m apps.intent_compiler intents/ --target all -o build

clean: ## Remove build artifacts
	rm -rf target/ dbt_packages/ logs/ build/

full-reset: clean ## Full reset including venv
	rm -rf venv/
