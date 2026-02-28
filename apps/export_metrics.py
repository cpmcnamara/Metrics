#!/usr/bin/env python3
"""
Export MetricFlow query results to CSV or JSON.

Useful for feeding metrics into downstream systems, notebooks, or APIs.

Usage:
    python apps/export_metrics.py --metrics total_revenue --group-by metric_time__month --format csv
    python apps/export_metrics.py --metrics total_revenue,total_order_count --group-by metric_time__month --format json
    python apps/export_metrics.py --metrics total_revenue --group-by order_id__customer_region --format csv --output revenue_by_region.csv
"""

import argparse
import subprocess
import sys
import json

import pandas as pd


def run_mf_query(metrics: str, group_by: str, where: str | None = None) -> pd.DataFrame:
    """Execute mf query and parse output into a DataFrame."""
    cmd = ["mf", "query", "--metrics", metrics, "--group-by", group_by]
    if where:
        cmd.extend(["--where", where])

    result = subprocess.run(cmd, capture_output=True, text=True, cwd="/home/user/Metrics")

    if result.returncode != 0:
        print(f"Error: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    lines = result.stdout.strip().split("\n")

    header_idx = None
    for i, line in enumerate(lines):
        tokens = line.split()
        if len(tokens) >= 2 and any(
            t.startswith("metric_time") or "_region" in t or "_category" in t
            or t.startswith("total_") or t.startswith("avg_") or t.startswith("order_")
            or t.startswith("revenue") or t.startswith("cumulative") or t.startswith("trailing")
            for t in tokens
        ):
            header_idx = i
            break

    if header_idx is None:
        print("Error: Could not parse query output", file=sys.stderr)
        sys.exit(1)

    cols = lines[header_idx].split()
    data_lines = []
    for line in lines[header_idx + 1:]:
        line = line.strip()
        if not line or line.startswith("-") or "✔" in line:
            continue
        data_lines.append(line.split())

    df = pd.DataFrame(data_lines, columns=cols)
    for col in df.columns:
        try:
            df[col] = pd.to_numeric(df[col])
        except (ValueError, TypeError):
            pass

    return df


def main():
    parser = argparse.ArgumentParser(description="Export MetricFlow query results")
    parser.add_argument("--metrics", required=True, help="Comma-separated metric names")
    parser.add_argument("--group-by", required=True, help="Comma-separated dimensions")
    parser.add_argument("--where", default=None, help="Optional where filter")
    parser.add_argument("--format", choices=["csv", "json", "parquet"], default="csv")
    parser.add_argument("--output", "-o", default=None, help="Output file path (default: stdout)")
    args = parser.parse_args()

    df = run_mf_query(args.metrics, getattr(args, "group_by"), args.where)

    if args.format == "csv":
        if args.output:
            df.to_csv(args.output, index=False)
            print(f"Exported {len(df)} rows to {args.output}")
        else:
            print(df.to_csv(index=False))

    elif args.format == "json":
        data = df.to_dict(orient="records")
        output = json.dumps(data, indent=2, default=str)
        if args.output:
            with open(args.output, "w") as f:
                f.write(output)
            print(f"Exported {len(df)} rows to {args.output}")
        else:
            print(output)

    elif args.format == "parquet":
        if not args.output:
            args.output = "output.parquet"
        df.to_parquet(args.output, index=False)
        print(f"Exported {len(df)} rows to {args.output}")


if __name__ == "__main__":
    main()
