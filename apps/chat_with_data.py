"""
Chat with your data using natural language, powered by MetricFlow + LLM.

This app translates natural language questions into MetricFlow `mf query`
commands, executes them, and presents the results conversationally.

Works with any LLM API (Claude, OpenAI, local models). Falls back to a
rule-based parser when no API key is configured.

Run: streamlit run apps/chat_with_data.py

To use with Claude API:
  export ANTHROPIC_API_KEY=sk-ant-...
  streamlit run apps/chat_with_data.py
"""

import json
import os
import subprocess
import re

import pandas as pd
import plotly.express as px
import streamlit as st

st.set_page_config(page_title="Chat with Your Metrics", layout="wide")
st.title("Chat with Your Metrics")

# ── Semantic Layer Schema (what the LLM knows about) ────────────────────────

SCHEMA = """
Available metrics:
- total_revenue: Total revenue across all orders (Simple)
- total_order_count: Total number of orders placed (Simple)
- avg_order_value: Average value per order (Simple)
- total_items_sold: Total quantity of items sold (Simple)
- total_completed_orders: Number of completed orders (Simple)
- total_returned_orders: Number of returned orders (Simple)
- order_return_rate: Percentage of orders returned (Derived = returned/total)
- revenue_per_item: Revenue per item sold (Derived = revenue/items)
- cumulative_revenue: Running total of revenue over all time (Cumulative)
- trailing_7d_revenue: Rolling 7-day revenue window (Cumulative)

Available dimensions for grouping:
- metric_time__day, metric_time__week, metric_time__month, metric_time__quarter, metric_time__year
- order_id__customer_region (values: us_west, us_east, eu_west)
- order_id__order_status (values: completed, returned, cancelled)
- order_id__customer_name
- order_item_id__product_category (values: widgets, gadgets, gizmos)
- order_item_id__product_name

Available where filter syntax:
- {{ Dimension('order_id__customer_region') }} = 'us_west'
- {{ Dimension('order_id__order_status') }} = 'completed'
- {{ TimeDimension('metric_time', 'month') }} >= '2024-06-01'
"""

SYSTEM_PROMPT = f"""You are a data analyst assistant. You translate natural language questions into MetricFlow query parameters.

{SCHEMA}

Given a user question, respond with a JSON object containing:
- "metrics": comma-separated metric names
- "group_by": comma-separated dimension names
- "where": optional where filter string (or null)
- "explanation": a brief explanation of what this query will show

Important rules:
- Use metric_time__month as the default time granularity unless the user asks for daily/weekly/quarterly
- When users ask about "products" or "categories", use the order_item_id__ prefixed dimensions
- When users ask about "regions" or "customers", use the order_id__ prefixed dimensions
- Only use metrics and dimensions from the schema above
- Respond with ONLY the JSON object, no other text
"""


def run_mf_query(metrics: str, group_by: str, where: str | None = None) -> pd.DataFrame:
    """Execute an mf query and return a DataFrame."""
    cmd = ["mf", "query", "--metrics", metrics, "--group-by", group_by]
    if where:
        cmd.extend(["--where", where])

    result = subprocess.run(cmd, capture_output=True, text=True, cwd="/home/user/Metrics")
    output = result.stdout
    lines = output.strip().split("\n")

    header_idx = None
    for i, line in enumerate(lines):
        # Look for lines with multiple word-like tokens (the header)
        tokens = line.split()
        if len(tokens) >= 2 and any(
            t.startswith("metric_time") or t.endswith("_region") or t.endswith("_category")
            or t.startswith("total_") or t.startswith("avg_") or t.startswith("order_")
            or t.startswith("revenue") or t.startswith("cumulative") or t.startswith("trailing")
            for t in tokens
        ):
            header_idx = i
            break

    if header_idx is None:
        return pd.DataFrame()

    cols = lines[header_idx].split()
    data_lines = []
    for line in lines[header_idx + 1 :]:
        line = line.strip()
        if not line or line.startswith("-") or "✔" in line or "⠋" in line:
            continue
        data_lines.append(line.split())

    df = pd.DataFrame(data_lines, columns=cols)
    for col in df.columns:
        if "metric_time" in col:
            df[col] = pd.to_datetime(df[col])
        else:
            try:
                df[col] = pd.to_numeric(df[col])
            except (ValueError, TypeError):
                pass
    return df


def query_with_llm(question: str) -> dict:
    """Use Claude API to translate question to MetricFlow params."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": question}],
        )
        text = response.content[0].text.strip()
        # Extract JSON from response
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)
    except Exception as e:
        st.error(f"LLM error: {e}")
        return None


def query_with_rules(question: str) -> dict:
    """Rule-based fallback for translating questions to MetricFlow params."""
    q = question.lower()

    metrics = "total_revenue"
    group_by = "metric_time__month"
    where = None
    explanation = ""

    # Detect metrics
    if "return rate" in q or "return" in q and "rate" in q:
        metrics = "order_return_rate"
    elif "returned" in q:
        metrics = "total_returned_orders"
    elif "average order" in q or "aov" in q:
        metrics = "avg_order_value"
    elif "order" in q and "count" in q or "how many orders" in q:
        metrics = "total_order_count"
    elif "items sold" in q or "quantity" in q:
        metrics = "total_items_sold"
    elif "revenue per item" in q:
        metrics = "revenue_per_item"
    elif "cumulative" in q or "running total" in q:
        metrics = "cumulative_revenue"
    elif "revenue" in q or "sales" in q or "money" in q:
        metrics = "total_revenue"

    # Multi-metric
    if "and" in q:
        if "revenue" in q and "order" in q:
            metrics = "total_revenue,total_order_count"

    # Detect grouping
    if "region" in q or "geography" in q:
        group_by = "order_id__customer_region"
    elif "product" in q or "category" in q:
        group_by = "order_item_id__product_category"
    elif "customer" in q:
        group_by = "order_id__customer_name"
    elif "status" in q:
        group_by = "order_id__order_status"
    elif "quarter" in q:
        group_by = "metric_time__quarter"
    elif "week" in q:
        group_by = "metric_time__week"
    elif "daily" in q or "day" in q:
        group_by = "metric_time__day"
    else:
        group_by = "metric_time__month"

    # Detect filters
    if "us west" in q or "west coast" in q:
        where = "{{ Dimension('order_id__customer_region') }} = 'us_west'"
    elif "us east" in q or "east coast" in q:
        where = "{{ Dimension('order_id__customer_region') }} = 'us_east'"
    elif "europe" in q or "eu" in q:
        where = "{{ Dimension('order_id__customer_region') }} = 'eu_west'"
    elif "completed" in q and "order" in q:
        where = "{{ Dimension('order_id__order_status') }} = 'completed'"

    explanation = f"Querying {metrics} grouped by {group_by}"
    if where:
        explanation += f" with filter"

    return {
        "metrics": metrics,
        "group_by": group_by,
        "where": where,
        "explanation": explanation,
    }


# ── Chat Interface ───────────────────────────────────────────────────────────

has_api_key = bool(os.environ.get("ANTHROPIC_API_KEY"))

if has_api_key:
    st.info("Claude API connected. Using AI-powered natural language understanding.")
else:
    st.info(
        "No ANTHROPIC_API_KEY set. Using rule-based query parser. "
        "Set the env var for full natural language support."
    )

st.markdown("**Ask questions about your business metrics in plain English.**")

# Example questions
with st.expander("Example questions you can ask"):
    st.markdown("""
- "What was our monthly revenue?"
- "Show me revenue by region"
- "What's the return rate over time?"
- "How many items sold by product category?"
- "Revenue and order count by quarter"
- "Show me US West revenue by month"
- "What's the average order value trend?"
- "Who are our top customers by revenue?"
- "Show me cumulative revenue over time"
    """)

# Chat history
if "messages" not in st.session_state:
    st.session_state.messages = []

# Display chat history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if "dataframe" in msg:
            st.dataframe(msg["dataframe"], use_container_width=True)
        if "chart" in msg:
            st.plotly_chart(msg["chart"], use_container_width=True)

# Chat input
if prompt := st.chat_input("Ask about your metrics..."):
    # Show user message
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Translate to MetricFlow query
    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            if has_api_key:
                params = query_with_llm(prompt)
                if not params:
                    params = query_with_rules(prompt)
            else:
                params = query_with_rules(prompt)

        explanation = params.get("explanation", "")
        metrics = params["metrics"]
        group_by = params["group_by"]
        where = params.get("where")

        st.markdown(f"*{explanation}*")
        st.code(
            f"mf query --metrics {metrics} --group-by {group_by}"
            + (f' --where "{where}"' if where else ""),
            language="bash",
        )

        with st.spinner("Querying MetricFlow..."):
            df = run_mf_query(metrics, group_by, where)

        msg = {"role": "assistant", "content": f"*{explanation}*"}

        if not df.empty:
            st.dataframe(df, use_container_width=True)
            msg["dataframe"] = df

            # Auto-visualize
            time_cols = [c for c in df.columns if "metric_time" in c]
            numeric_cols = df.select_dtypes(include="number").columns.tolist()
            cat_cols = [c for c in df.columns if c not in numeric_cols and "metric_time" not in c]

            if time_cols and numeric_cols:
                if cat_cols:
                    fig = px.line(
                        df, x=time_cols[0], y=numeric_cols[0],
                        color=cat_cols[0], markers=True,
                        title=explanation,
                    )
                else:
                    fig = px.bar(
                        df, x=time_cols[0], y=numeric_cols,
                        title=explanation,
                    )
                fig.update_layout(xaxis_tickformat="%b %Y")
                st.plotly_chart(fig, use_container_width=True)
                msg["chart"] = fig
            elif cat_cols and numeric_cols:
                fig = px.bar(
                    df, x=cat_cols[0], y=numeric_cols[0],
                    color=cat_cols[0] if len(cat_cols) > 0 else None,
                    title=explanation,
                )
                st.plotly_chart(fig, use_container_width=True)
                msg["chart"] = fig

            # Summary stats
            if numeric_cols:
                summary_parts = []
                for col in numeric_cols:
                    total = df[col].sum()
                    avg = df[col].mean()
                    summary_parts.append(f"**{col}**: total={total:,.2f}, avg={avg:,.2f}")
                st.markdown("**Summary:** " + " | ".join(summary_parts))
        else:
            st.warning("No data returned for this query.")

        st.session_state.messages.append(msg)
