"""
Compiler for Intent DSL.

Takes parsed IntentFile objects and generates:
  1. MetricFlow YAML (semantic models + metrics + saved queries)
  2. LLM context JSON (for the chat engine)
  3. Ontology graph JSON (nodes + edges)
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from apps.intent_parser import IntentFile, Metric, Product, Source


# ── Target 1: MetricFlow YAML ────────────────────────────────────────────────


def compile_semantic_model(source: Source) -> dict:
    """Compile a Source into a MetricFlow semantic model definition."""
    primary = next((e for e in source.entities if e.kind == "primary"), None)

    model = {
        "name": source.name,
        "defaults": {"agg_time_dimension": source.time_column},
        "model": source.table,
        "entities": [],
        "dimensions": [
            {
                "name": source.time_column,
                "type": "time",
                "type_params": {"time_granularity": source.time_granularity},
            }
        ],
        "measures": [],
    }

    # Entities
    for entity in source.entities:
        model["entities"].append({"name": entity.name, "type": entity.kind})

    # Dimensions (non-time)
    for dim in source.dimensions:
        model["dimensions"].append({"name": dim.name, "type": dim.kind})

    # Measures
    for measure in source.measures:
        measure_def = {
            "name": measure.name,
            "agg": measure.agg,
            "expr": measure.column,
        }
        if measure.agg == "average":
            measure_def["agg"] = "average"
        model["measures"].append(measure_def)

    return {"semantic_models": [model]}


def compile_metric(metric: Metric, all_metrics: dict[str, Metric]) -> dict:
    """Compile a Metric into a MetricFlow metric definition."""
    metric_def: dict = {
        "name": metric.name,
        "description": metric.description,
        "type": metric.metric_type,
    }

    if metric.metric_type == "simple":
        source_name, measure_name = metric.source_ref.split(".")
        metric_def["type_params"] = {"measure": measure_name}

    elif metric.metric_type == "derived":
        # Parse formula to extract metric references
        # Formula like: total_returned_orders / total_order_count
        import re
        tokens = re.findall(r'[a-z_]\w*', metric.formula)
        input_metrics = [t for t in tokens if t in all_metrics]

        # Build expr with single-letter labels
        expr = metric.formula
        labels = {}
        for idx, m in enumerate(input_metrics):
            label = chr(ord('a') + idx)
            labels[m] = label

        # Replace metric names with labels in expression (longest first)
        for m_name in sorted(labels.keys(), key=len, reverse=True):
            expr = expr.replace(m_name, labels[m_name])

        # Add * 1.0 for division to avoid integer division
        if '/' in expr:
            parts = expr.split('/')
            expr = f"{parts[0].strip()} * 1.0 / {parts[1].strip()}"

        metric_def["type_params"] = {
            "expr": expr,
            "metrics": [
                {"name": m_name, "alias": label}
                for m_name, label in labels.items()
            ],
        }

    elif metric.metric_type == "cumulative":
        source_name, measure_name = metric.source_ref.split(".")
        metric_def["type_params"] = {"measure": measure_name}
        if metric.window:
            parts = metric.window.split()
            metric_def["type_params"]["window"] = {
                "count": int(parts[0]),
                "period": parts[1],
            }

    return metric_def


def compile_saved_query(intent, aliases: dict[str, str]) -> dict:
    """Compile an Intent into a MetricFlow saved query."""
    # Slugify the question for the query name
    import re
    slug = re.sub(r'[^a-z0-9]+', '_', intent.question.lower()).strip('_')

    query: dict = {
        "name": slug,
        "description": intent.question,
        "query_params": {
            "metrics": [{"name": m} for m in intent.metrics],
            "group_by": [],
        },
    }

    for dim in intent.dimensions:
        resolved = aliases.get(dim, dim)
        if resolved.startswith("metric_time__"):
            grain = resolved.split("__")[1]
            query["query_params"]["group_by"].append({
                "name": "metric_time",
                "grain": grain,
            })
        else:
            query["query_params"]["group_by"].append({"name": resolved})

    if intent.where:
        # Simple where: "status = returned" -> MetricFlow filter
        query["query_params"]["where"] = [compile_where(intent.where, aliases)]

    return query


def compile_where(where_str: str, aliases: dict[str, str]) -> str:
    """Compile a DSL where clause to MetricFlow where syntax."""
    # Parse "dimension = value" or "dimension in (val1, val2)"
    import re

    # Handle "dim = value"
    eq_match = re.match(r'(\w+)\s*=\s*(\S+)', where_str)
    if eq_match:
        dim_name = eq_match.group(1)
        value = eq_match.group(2)
        resolved = aliases.get(dim_name, dim_name)
        if resolved.startswith("metric_time"):
            return f"{{{{ TimeDimension('metric_time', 'day') }}}} = '{value}'"
        return f"{{{{ Dimension('{resolved}') }}}} = '{value}'"

    # Handle "dim in (val1, val2)"
    in_match = re.match(r'(\w+)\s+in\s+\((.+)\)', where_str)
    if in_match:
        dim_name = in_match.group(1)
        values = [v.strip() for v in in_match.group(2).split(',')]
        resolved = aliases.get(dim_name, dim_name)
        values_str = ', '.join(f"'{v}'" for v in values)
        return f"{{{{ Dimension('{resolved}') }}}} in ({values_str})"

    # Handle time comparisons "time >= 2024-01-01"
    time_match = re.match(r'(\w+)\s*(>=|<=|>|<)\s*(\S+)', where_str)
    if time_match:
        dim_name = time_match.group(1)
        op = time_match.group(2)
        value = time_match.group(3)
        resolved = aliases.get(dim_name, dim_name)
        if 'time' in dim_name or 'time' in resolved:
            return f"{{{{ TimeDimension('metric_time', 'month') }}}} {op} '{value}'"
        return f"{{{{ Dimension('{resolved}') }}}} {op} '{value}'"

    return where_str


def compile_metricflow(intent_file: IntentFile) -> dict[str, str]:
    """
    Compile an IntentFile to MetricFlow YAML files.
    Returns a dict of {filename: yaml_content}.
    """
    output = {}
    all_metrics = {m.name: m for m in intent_file.metrics}

    # Semantic models
    for source in intent_file.sources:
        model = compile_semantic_model(source)
        filename = f"sem_{source.name}.yml"
        output[filename] = yaml.dump(model, default_flow_style=False, sort_keys=False)

    # Metrics
    metrics_list = []
    for metric in intent_file.metrics:
        metrics_list.append(compile_metric(metric, all_metrics))
    output["metrics.yml"] = yaml.dump(
        {"metrics": metrics_list}, default_flow_style=False, sort_keys=False,
    )

    # Saved queries
    saved_queries = []
    for intent in intent_file.intents:
        saved_queries.append(compile_saved_query(intent, intent_file.aliases))
    output["saved_queries.yml"] = yaml.dump(
        {"saved_queries": saved_queries}, default_flow_style=False, sort_keys=False,
    )

    return output


# ── Target 2: LLM Context ────────────────────────────────────────────────────


def compile_llm_context(intent_file: IntentFile) -> dict:
    """
    Compile an IntentFile to LLM context JSON.
    This is what gets fed to the LLM at query time.
    """
    metrics = []
    for m in intent_file.metrics:
        metrics.append({
            "name": m.name,
            "description": m.description,
            "synonyms": m.synonyms,
            "type": m.metric_type,
        })

    intents = []
    for intent in intent_file.intents:
        resolved_dims = [
            intent_file.resolve_alias(d) for d in intent.dimensions
        ]
        intent_data = {
            "question": intent.question,
            "also_asked": intent.also_asked,
            "params": {
                "metrics": ",".join(intent.metrics),
                "group_by": ",".join(resolved_dims),
            },
            "references": [
                {"name": name, "path": path}
                for name, path in intent.references
            ],
            "context": intent.context,
            "is_default": intent.is_default,
        }
        if intent.where:
            intent_data["params"]["where"] = compile_where(
                intent.where, intent_file.aliases,
            )
        intents.append(intent_data)

    return {
        "metrics": metrics,
        "intents": intents,
        "aliases": intent_file.aliases,
    }


# ── Target 3: Ontology Graph ─────────────────────────────────────────────────


def compile_ontology(intent_file: IntentFile) -> dict:
    """
    Compile an IntentFile to an ontology graph (nodes + edges).
    """
    nodes = []
    edges = []
    seen_nodes = set()

    def add_node(node_id: str, node_type: str, label: str, **props):
        if node_id not in seen_nodes:
            node = {"id": node_id, "type": node_type, "label": label}
            node.update(props)
            nodes.append(node)
            seen_nodes.add(node_id)

    def add_edge(from_id: str, to_id: str, relation: str):
        edges.append({"from": from_id, "to": to_id, "relation": relation})

    # Sources
    for source in intent_file.sources:
        source_id = f"source:{source.name}"
        add_node(source_id, "source", source.name, table=source.table)

        for measure in source.measures:
            measure_id = f"measure:{source.name}.{measure.name}"
            add_node(measure_id, "measure", measure.name, agg=measure.agg)
            add_edge(measure_id, source_id, "from_source")

        for dim in source.dimensions:
            dim_id = f"dimension:{source.name}.{dim.name}"
            add_node(dim_id, "dimension", dim.name, kind=dim.kind)
            add_edge(dim_id, source_id, "from_source")

    # Metrics
    for metric in intent_file.metrics:
        metric_id = f"metric:{metric.name}"
        add_node(
            metric_id, "metric", metric.name,
            description=metric.description,
            synonyms=metric.synonyms,
        )

        if metric.source_ref:
            measure_id = f"measure:{metric.source_ref}"
            add_edge(metric_id, measure_id, "computed_from")

        if metric.formula:
            import re
            all_metric_names = {m.name for m in intent_file.metrics}
            tokens = re.findall(r'[a-z_]\w*', metric.formula)
            for token in tokens:
                if token in all_metric_names and token != metric.name:
                    add_edge(metric_id, f"metric:{token}", "depends_on")

    # Intents
    for intent in intent_file.intents:
        import re
        intent_id = "intent:" + re.sub(r'[^a-z0-9]+', '_', intent.question.lower()).strip('_')
        add_node(
            intent_id, "intent", intent.question,
            also_asked=intent.also_asked,
            context=intent.context,
            is_default=intent.is_default,
        )

        for m in intent.metrics:
            add_edge(intent_id, f"metric:{m}", "uses")

        for dim in intent.dimensions:
            resolved = intent_file.resolve_alias(dim)
            dim_id = f"dimension:{resolved}"
            add_node(dim_id, "dimension", resolved)
            add_edge(intent_id, dim_id, "sliced_by")

        for ref_name, ref_path in intent.references:
            doc_id = f"document:{ref_path}"
            add_node(doc_id, "document", ref_name, path=ref_path)
            add_edge(intent_id, doc_id, "references")

    # Groups
    for group in intent_file.groups:
        import re
        group_id = "group:" + re.sub(r'[^a-z0-9]+', '_', group.name.lower()).strip('_')
        add_node(group_id, "group", group.name)
        for intent_name in group.intent_names:
            intent_id = "intent:" + re.sub(
                r'[^a-z0-9]+', '_', intent_name.lower(),
            ).strip('_')
            add_edge(group_id, intent_id, "contains")

    # Products
    compile_products_into_ontology(intent_file, nodes, edges, seen_nodes)

    return {"nodes": nodes, "edges": edges}


# ── Target 4: Data Product Manifests ─────────────────────────────────────────


def compile_product_manifest(product: Product, intent_file: IntentFile) -> dict:
    """
    Compile a Product into a self-describing manifest.

    The manifest is the single document that makes a data product discoverable,
    addressable, and trustworthy. It follows the Data Mesh principle that every
    data product must be self-describing.
    """
    import re

    slug = re.sub(r'[^a-z0-9]+', '_', product.name.lower()).strip('_')

    # Resolve the metrics this product exposes
    exposed_metrics = []
    for m_name in product.metric_names:
        metric = intent_file.get_metric(m_name)
        if metric:
            exposed_metrics.append({
                "name": metric.name,
                "description": metric.description,
                "type": metric.metric_type,
                "synonyms": metric.synonyms,
            })

    # Resolve the intents (business questions) this product answers
    answered_intents = []
    for intent_name in product.intent_names:
        for intent in intent_file.intents:
            if intent.question == intent_name:
                answered_intents.append({
                    "question": intent.question,
                    "also_asked": intent.also_asked,
                    "metrics": intent.metrics,
                    "dimensions": [
                        intent_file.resolve_alias(d)
                        for d in intent.dimensions
                    ],
                })
                break

    # Build contract
    contracts = []
    for rule in product.contracts:
        contracts.append({
            "check": rule.check,
            "operator": rule.operator,
            "threshold": rule.value,
        })

    # Build SLOs
    slos = []
    for slo in product.slos:
        slos.append({"name": slo.name, "target": slo.value})

    # Build output port
    output_port = {}
    if product.output.table:
        output_port["table"] = product.output.table
    if product.output.formats:
        output_port["formats"] = product.output.formats
    if product.output.schedule:
        output_port["schedule"] = product.output.schedule

    return {
        "product": {
            "id": slug,
            "name": product.name,
            "description": product.description,
            "owner": product.owner,
            "domain": product.domain,
            "tier": product.tier,
            "tags": product.tags,
            "depends_on": product.depends_on,
        },
        "schema": {
            "metrics": exposed_metrics,
            "intents": answered_intents,
        },
        "quality": {
            "contracts": contracts,
            "slos": slos,
        },
        "output": output_port,
    }


def compile_data_contract(product: Product, intent_file: IntentFile) -> dict:
    """
    Compile a Product into a data contract (YAML-compatible dict).

    Data contracts are the interface agreement between a data product and its
    consumers. They specify what the product promises: schema, quality, freshness,
    and availability. This follows the "data contracts" pattern from Andrew Jones.
    """
    import re

    slug = re.sub(r'[^a-z0-9]+', '_', product.name.lower()).strip('_')

    # Build the schema section — which columns/metrics are guaranteed
    schema_fields = []
    for m_name in product.metric_names:
        metric = intent_file.get_metric(m_name)
        if metric:
            schema_fields.append({
                "name": metric.name,
                "type": "metric",
                "metric_type": metric.metric_type,
                "description": metric.description,
            })

    # Collect all dimensions used by the product's intents
    dimensions_used = set()
    for intent_name in product.intent_names:
        for intent in intent_file.intents:
            if intent.question == intent_name:
                for d in intent.dimensions:
                    dimensions_used.add(intent_file.resolve_alias(d))
                break

    for dim in sorted(dimensions_used):
        schema_fields.append({
            "name": dim,
            "type": "dimension",
        })

    # Quality checks
    quality = []
    for rule in product.contracts:
        quality.append({
            "type": rule.check,
            "operator": rule.operator,
            "value": rule.value,
        })

    return {
        "dataContractSpecification": "0.9.3",
        "id": f"urn:dataproduct:{slug}",
        "info": {
            "title": product.name,
            "version": "1.0.0",
            "description": product.description,
            "owner": product.owner,
            "domain": product.domain,
        },
        "schema": schema_fields,
        "quality": quality,
        "slos": [{"name": s.name, "target": s.value} for s in product.slos],
        "tags": product.tags,
    }


def compile_product_catalog(intent_file: IntentFile) -> dict:
    """
    Compile all Products into a unified catalog for discovery.

    The catalog is the "marketplace" view — a single JSON that lists every
    data product with enough metadata for consumers to find, evaluate, and
    request access to the products they need.
    """
    catalog_entries = []

    for product in intent_file.products:
        entry = {
            "name": product.name,
            "description": product.description,
            "owner": product.owner,
            "domain": product.domain,
            "tier": product.tier,
            "tags": product.tags,
            "metrics_count": len(product.metric_names),
            "metrics": product.metric_names,
            "intents_count": len(product.intent_names),
            "intents": product.intent_names,
            "has_contract": len(product.contracts) > 0,
            "has_slos": len(product.slos) > 0,
            "output_table": product.output.table,
            "output_formats": product.output.formats,
            "schedule": product.output.schedule,
            "depends_on": product.depends_on,
        }
        catalog_entries.append(entry)

    # Build a dependency graph for the catalog
    dep_edges = []
    for product in intent_file.products:
        for dep in product.depends_on:
            dep_edges.append({"from": product.name, "to": dep})

    return {
        "products": catalog_entries,
        "dependency_graph": dep_edges,
        "summary": {
            "total_products": len(intent_file.products),
            "by_domain": _count_by(intent_file.products, lambda p: p.domain),
            "by_tier": _count_by(intent_file.products, lambda p: p.tier),
        },
    }


def _count_by(products: list[Product], key_fn) -> dict[str, int]:
    counts: dict[str, int] = {}
    for p in products:
        k = key_fn(p)
        counts[k] = counts.get(k, 0) + 1
    return counts


def compile_product_dbt_model(product: Product, intent_file: IntentFile) -> str:
    """
    Generate a dbt SQL model that materializes a data product as a table.

    This creates a governed, queryable output table for the product by joining
    the metrics the product exposes with the dimensions its intents use.
    """
    if not product.output.table:
        return ""

    # Find which sources the product's metrics come from
    source_tables = set()
    for m_name in product.metric_names:
        metric = intent_file.get_metric(m_name)
        if metric and metric.source_ref:
            source_name = metric.source_ref.split(".")[0]
            for s in intent_file.sources:
                if s.name == source_name:
                    source_tables.add((s.name, s.table))

    if not source_tables:
        return ""

    # Build the SQL as a simple select from the primary source
    primary_source_name, primary_table = next(iter(source_tables))

    # Collect all measure columns the product needs
    measures = []
    for m_name in product.metric_names:
        metric = intent_file.get_metric(m_name)
        if metric and metric.source_ref:
            source_name, measure_name = metric.source_ref.split(".")
            for s in intent_file.sources:
                if s.name == source_name:
                    for m in s.measures:
                        if m.name == measure_name:
                            measures.append(m)

    # Collect dimensions used by the product's intents
    dims_used = set()
    for intent_name in product.intent_names:
        for intent in intent_file.intents:
            if intent.question == intent_name:
                for d in intent.dimensions:
                    dims_used.add(d)
                break

    # Resolve the primary source for column references
    primary_source = None
    for s in intent_file.sources:
        if s.name == primary_source_name:
            primary_source = s
            break

    if not primary_source:
        return ""

    # Build column list
    columns = [primary_source.time_column]
    for dim_alias in dims_used:
        resolved = intent_file.resolve_alias(dim_alias)
        # Map MetricFlow dim paths back to column names
        if resolved.startswith("metric_time__"):
            continue  # time column already included
        # Extract the column name from the path (e.g., "order_id__customer_region" -> "customer_region")
        parts = resolved.split("__")
        col_name = parts[-1] if len(parts) > 1 else parts[0]
        if col_name not in columns:
            columns.append(col_name)

    # Add measure expressions
    measure_selects = []
    for m in measures:
        measure_selects.append(f"  {m.agg}({m.column}) as {m.name}")

    group_cols = ", ".join(columns)
    select_cols = "\n".join(f"  {c}," for c in columns)
    agg_cols = ",\n".join(measure_selects)

    # Extract the ref name from "ref('fct_orders')"
    import re
    ref_match = re.search(r"ref\('([^']+)'\)", primary_table)
    ref_name = ref_match.group(1) if ref_match else primary_source_name

    sql = f"""-- Data product: {product.name}
-- Owner: {product.owner} | Domain: {product.domain} | Tier: {product.tier}
-- Schedule: {product.output.schedule or 'on-demand'}

select
{select_cols}
{agg_cols}
from {{{{ ref('{ref_name}') }}}}
group by {group_cols}
order by {primary_source.time_column}
"""
    return sql


# ── Ontology: product nodes/edges ────────────────────────────────────────────


def compile_products_into_ontology(intent_file: IntentFile, nodes: list, edges: list, seen_nodes: set):
    """Add product nodes and edges to an existing ontology graph."""
    import re

    def add_node(node_id: str, node_type: str, label: str, **props):
        if node_id not in seen_nodes:
            node = {"id": node_id, "type": node_type, "label": label}
            node.update(props)
            nodes.append(node)
            seen_nodes.add(node_id)

    def add_edge(from_id: str, to_id: str, relation: str):
        edges.append({"from": from_id, "to": to_id, "relation": relation})

    for product in intent_file.products:
        product_id = "product:" + re.sub(
            r'[^a-z0-9]+', '_', product.name.lower(),
        ).strip('_')
        add_node(
            product_id, "product", product.name,
            owner=product.owner,
            domain=product.domain,
            tier=product.tier,
            tags=product.tags,
        )

        # Product -> metrics
        for m_name in product.metric_names:
            add_edge(product_id, f"metric:{m_name}", "exposes")

        # Product -> intents
        for intent_name in product.intent_names:
            intent_id = "intent:" + re.sub(
                r'[^a-z0-9]+', '_', intent_name.lower(),
            ).strip('_')
            add_edge(product_id, intent_id, "answers")

        # Product -> dependencies
        for dep_name in product.depends_on:
            dep_id = "product:" + re.sub(
                r'[^a-z0-9]+', '_', dep_name.lower(),
            ).strip('_')
            add_edge(product_id, dep_id, "depends_on")


# ── CLI ──────────────────────────────────────────────────────────────────────


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Compile .intent files")
    parser.add_argument("source", help="Path to .intent file or directory")
    parser.add_argument(
        "--target",
        choices=[
            "metricflow", "llm-context", "ontology",
            "products", "contracts", "catalog", "product-models",
            "all", "validate",
        ],
        default="all",
        help="Compilation target",
    )
    parser.add_argument("-o", "--output", help="Output directory", default=".")

    args = parser.parse_args()
    source_path = Path(args.source)

    from apps.intent_parser import parse_directory, parse_file

    if source_path.is_dir():
        intent_file = parse_directory(source_path)
    else:
        intent_file = parse_file(source_path)

    # Validate
    errors = intent_file.validate()
    if errors:
        print("Validation errors:")
        for err in errors:
            print(f"  - {err}")
        if args.target == "validate":
            return
        print()

    if args.target == "validate":
        if not errors:
            print("No errors found.")
        print(f"\nSummary:")
        print(f"  Sources:   {len(intent_file.sources)}")
        print(f"  Metrics:   {len(intent_file.metrics)}")
        print(f"  Intents:   {len(intent_file.intents)}")
        print(f"  Groups:    {len(intent_file.groups)}")
        print(f"  Products:  {len(intent_file.products)}")
        print(f"  Aliases:   {len(intent_file.aliases)}")
        return

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    if args.target in ("metricflow", "all"):
        mf_output = compile_metricflow(intent_file)
        for filename, content in mf_output.items():
            (out / filename).write_text(content)
            print(f"  wrote {out / filename}")

    if args.target in ("llm-context", "all"):
        ctx = compile_llm_context(intent_file)
        ctx_path = out / "llm_context.json"
        ctx_path.write_text(json.dumps(ctx, indent=2))
        print(f"  wrote {ctx_path}")

    if args.target in ("ontology", "all"):
        graph = compile_ontology(intent_file)
        graph_path = out / "ontology.json"
        graph_path.write_text(json.dumps(graph, indent=2))
        print(f"  wrote {graph_path}")

    if args.target in ("products", "all"):
        products_dir = out / "products"
        products_dir.mkdir(parents=True, exist_ok=True)
        for product in intent_file.products:
            manifest = compile_product_manifest(product, intent_file)
            import re
            slug = re.sub(r'[^a-z0-9]+', '_', product.name.lower()).strip('_')
            manifest_path = products_dir / f"{slug}.json"
            manifest_path.write_text(json.dumps(manifest, indent=2))
            print(f"  wrote {manifest_path}")

    if args.target in ("contracts", "all"):
        contracts_dir = out / "contracts"
        contracts_dir.mkdir(parents=True, exist_ok=True)
        for product in intent_file.products:
            contract = compile_data_contract(product, intent_file)
            import re
            slug = re.sub(r'[^a-z0-9]+', '_', product.name.lower()).strip('_')
            contract_path = contracts_dir / f"{slug}_contract.yml"
            contract_path.write_text(
                yaml.dump(contract, default_flow_style=False, sort_keys=False),
            )
            print(f"  wrote {contract_path}")

    if args.target in ("catalog", "all"):
        catalog = compile_product_catalog(intent_file)
        catalog_path = out / "product_catalog.json"
        catalog_path.write_text(json.dumps(catalog, indent=2))
        print(f"  wrote {catalog_path}")

    if args.target in ("product-models", "all"):
        models_dir = out / "product_models"
        models_dir.mkdir(parents=True, exist_ok=True)
        for product in intent_file.products:
            sql = compile_product_dbt_model(product, intent_file)
            if sql and product.output.table:
                model_path = models_dir / f"{product.output.table}.sql"
                model_path.write_text(sql)
                print(f"  wrote {model_path}")


if __name__ == "__main__":
    main()
