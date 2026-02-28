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

from apps.intent_parser import IntentFile, Metric, Source


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

    return {"nodes": nodes, "edges": edges}


# ── CLI ──────────────────────────────────────────────────────────────────────


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Compile .intent files")
    parser.add_argument("source", help="Path to .intent file or directory")
    parser.add_argument(
        "--target",
        choices=["metricflow", "llm-context", "ontology", "all", "validate"],
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
        print(f"  Sources:  {len(intent_file.sources)}")
        print(f"  Metrics:  {len(intent_file.metrics)}")
        print(f"  Intents:  {len(intent_file.intents)}")
        print(f"  Groups:   {len(intent_file.groups)}")
        print(f"  Aliases:  {len(intent_file.aliases)}")
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


if __name__ == "__main__":
    main()
