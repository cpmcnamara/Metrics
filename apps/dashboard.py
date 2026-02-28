"""
Streamlit dashboard powered by MetricFlow semantic layer.

Reads metrics directly from the DuckDB warehouse that MetricFlow manages,
using the same fact tables and definitions as `mf query`.

Run: streamlit run apps/dashboard.py
"""

import subprocess
import io
import re

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="MetricFlow Dashboard", layout="wide")

# ── Helpers ──────────────────────────────────────────────────────────────────


def run_mf_query(metrics: str, group_by: str, where: str | None = None) -> pd.DataFrame:
    """Execute an `mf query` command and return results as a DataFrame."""
    cmd = [
        "mf", "query",
        "--metrics", metrics,
        "--group-by", group_by,
    ]
    if where:
        cmd.extend(["--where", where])

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd="/home/user/Metrics",
    )

    output = result.stdout
    # Parse the table output from mf query (skip spinner lines, find header)
    lines = output.strip().split("\n")

    # Find the header line (contains column names separated by spaces)
    header_idx = None
    for i, line in enumerate(lines):
        if "metric_time" in line or "customer_region" in line or "product_category" in line:
            header_idx = i
            break

    if header_idx is None:
        return pd.DataFrame()

    # Header and separator
    header = lines[header_idx]
    cols = header.split()

    # Data lines start after the separator (dashes)
    data_lines = []
    for line in lines[header_idx + 1 :]:
        line = line.strip()
        if not line or line.startswith("-") or line.startswith("�") or line.startswith("✔"):
            continue
        data_lines.append(line.split())

    df = pd.DataFrame(data_lines, columns=cols)

    # Convert numeric columns
    for col in df.columns:
        if col.startswith("metric_time"):
            df[col] = pd.to_datetime(df[col])
        else:
            try:
                df[col] = pd.to_numeric(df[col])
            except (ValueError, TypeError):
                pass

    return df


# ── Sidebar ──────────────────────────────────────────────────────────────────

st.sidebar.title("MetricFlow Dashboard")
st.sidebar.markdown("Powered by **dbt + MetricFlow + DuckDB**")

page = st.sidebar.radio(
    "View",
    ["Revenue Overview", "Regional Breakdown", "Product Analysis", "Custom Query"],
)

# ── Pages ────────────────────────────────────────────────────────────────────

if page == "Revenue Overview":
    st.title("Revenue Overview")

    # KPI cards
    df_totals = run_mf_query("total_revenue,total_order_count,avg_order_value", "metric_time__month")

    if not df_totals.empty:
        total_rev = df_totals["total_revenue"].sum()
        total_orders = df_totals["total_order_count"].sum()
        avg_aov = df_totals["avg_order_value"].mean()

        col1, col2, col3 = st.columns(3)
        col1.metric("Total Revenue", f"${total_rev:,.0f}")
        col2.metric("Total Orders", f"{total_orders:,.0f}")
        col3.metric("Avg Order Value", f"${avg_aov:,.2f}")

        st.markdown("---")

        # Revenue trend
        fig = px.bar(
            df_totals,
            x="metric_time__month",
            y="total_revenue",
            title="Monthly Revenue",
            labels={"metric_time__month": "Month", "total_revenue": "Revenue ($)"},
        )
        fig.update_layout(xaxis_tickformat="%b %Y")
        st.plotly_chart(fig, use_container_width=True)

        # AOV trend
        fig2 = px.line(
            df_totals,
            x="metric_time__month",
            y="avg_order_value",
            title="Average Order Value Trend",
            markers=True,
            labels={"metric_time__month": "Month", "avg_order_value": "AOV ($)"},
        )
        fig2.update_layout(xaxis_tickformat="%b %Y")
        st.plotly_chart(fig2, use_container_width=True)

    # Return rate
    df_returns = run_mf_query("order_return_rate", "metric_time__month")
    if not df_returns.empty:
        fig3 = px.area(
            df_returns,
            x="metric_time__month",
            y="order_return_rate",
            title="Monthly Return Rate",
            labels={"metric_time__month": "Month", "order_return_rate": "Return Rate"},
        )
        fig3.update_layout(xaxis_tickformat="%b %Y", yaxis_tickformat=".0%")
        st.plotly_chart(fig3, use_container_width=True)

elif page == "Regional Breakdown":
    st.title("Regional Breakdown")

    df_region = run_mf_query(
        "total_revenue,total_order_count", "order_id__customer_region"
    )

    if not df_region.empty:
        col1, col2 = st.columns(2)

        with col1:
            fig = px.pie(
                df_region,
                values="total_revenue",
                names="order_id__customer_region",
                title="Revenue by Region",
            )
            st.plotly_chart(fig, use_container_width=True)

        with col2:
            fig2 = px.bar(
                df_region,
                x="order_id__customer_region",
                y="total_order_count",
                title="Orders by Region",
                color="order_id__customer_region",
                labels={
                    "order_id__customer_region": "Region",
                    "total_order_count": "Orders",
                },
            )
            st.plotly_chart(fig2, use_container_width=True)

    # Revenue by region over time
    df_region_time = run_mf_query(
        "total_revenue",
        "order_id__customer_region,metric_time__month",
    )
    if not df_region_time.empty:
        fig3 = px.line(
            df_region_time,
            x="metric_time__month",
            y="total_revenue",
            color="order_id__customer_region",
            title="Revenue by Region Over Time",
            markers=True,
            labels={
                "metric_time__month": "Month",
                "total_revenue": "Revenue ($)",
                "order_id__customer_region": "Region",
            },
        )
        fig3.update_layout(xaxis_tickformat="%b %Y")
        st.plotly_chart(fig3, use_container_width=True)

elif page == "Product Analysis":
    st.title("Product Analysis")

    df_products = run_mf_query(
        "total_items_sold",
        "order_item_id__product_category",
    )

    if not df_products.empty:
        fig = px.bar(
            df_products,
            x="order_item_id__product_category",
            y="total_items_sold",
            title="Items Sold by Category",
            color="order_item_id__product_category",
            labels={
                "order_item_id__product_category": "Category",
                "total_items_sold": "Items Sold",
            },
        )
        st.plotly_chart(fig, use_container_width=True)

    df_product_time = run_mf_query(
        "total_items_sold",
        "order_item_id__product_category,metric_time__month",
    )
    if not df_product_time.empty:
        fig2 = px.area(
            df_product_time,
            x="metric_time__month",
            y="total_items_sold",
            color="order_item_id__product_category",
            title="Items Sold by Category Over Time",
            labels={
                "metric_time__month": "Month",
                "total_items_sold": "Items Sold",
                "order_item_id__product_category": "Category",
            },
        )
        fig2.update_layout(xaxis_tickformat="%b %Y")
        st.plotly_chart(fig2, use_container_width=True)

elif page == "Custom Query":
    st.title("Custom MetricFlow Query")
    st.markdown(
        "Build your own query using the MetricFlow semantic layer. "
        "All metrics use the same governed definitions."
    )

    available_metrics = [
        "total_revenue",
        "total_order_count",
        "avg_order_value",
        "total_items_sold",
        "total_completed_orders",
        "total_returned_orders",
        "order_return_rate",
        "revenue_per_item",
        "cumulative_revenue",
        "trailing_7d_revenue",
    ]

    available_dimensions = [
        "metric_time__day",
        "metric_time__week",
        "metric_time__month",
        "metric_time__quarter",
        "order_id__customer_region",
        "order_id__order_status",
        "order_id__customer_name",
    ]

    selected_metrics = st.multiselect("Select metrics", available_metrics, default=["total_revenue"])
    selected_dims = st.multiselect(
        "Group by dimensions", available_dimensions, default=["metric_time__month"]
    )

    if st.button("Run Query") and selected_metrics and selected_dims:
        with st.spinner("Querying MetricFlow..."):
            df = run_mf_query(",".join(selected_metrics), ",".join(selected_dims))

        if not df.empty:
            st.dataframe(df, use_container_width=True)

            # Auto-chart if there's a time dimension
            time_cols = [c for c in df.columns if "metric_time" in c]
            numeric_cols = df.select_dtypes(include="number").columns.tolist()

            if time_cols and numeric_cols:
                fig = px.line(
                    df,
                    x=time_cols[0],
                    y=numeric_cols,
                    title="Query Results",
                    markers=True,
                )
                st.plotly_chart(fig, use_container_width=True)
            elif numeric_cols:
                cat_cols = [c for c in df.columns if c not in numeric_cols]
                if cat_cols:
                    fig = px.bar(df, x=cat_cols[0], y=numeric_cols[0], title="Query Results")
                    st.plotly_chart(fig, use_container_width=True)
        else:
            st.warning("No results returned.")

st.sidebar.markdown("---")
st.sidebar.markdown(
    "All metrics are governed by the **MetricFlow semantic layer**. "
    "Everyone sees the same numbers."
)
