"""
Data Product Catalog — Streamlit app for discovering and inspecting data products.

Launch with:
    streamlit run apps/data_product_server.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import streamlit as st

# ── Ensure the repo root is on sys.path so imports resolve ───────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apps.intent_compiler import (
    compile_data_contract,
    compile_product_catalog,
    compile_product_manifest,
)
from apps.intent_parser import parse_directory, parse_file

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Data Product Catalog",
    page_icon="📦",
    layout="wide",
)

TIER_COLORS = {"gold": "#FFD700", "silver": "#C0C0C0", "bronze": "#CD7F32"}
TIER_ICONS = {"gold": "🥇", "silver": "🥈", "bronze": "🥉"}


# ── Load intent file ────────────────────────────────────────────────────────
@st.cache_data
def load_intent_file():
    intents_dir = Path(__file__).resolve().parent.parent / "intents"
    if intents_dir.is_dir():
        return parse_directory(intents_dir)
    return parse_file(intents_dir / "orders.intent")


intent_file = load_intent_file()

if not intent_file.products:
    st.warning("No data products found in your .intent files.")
    st.stop()

catalog = compile_product_catalog(intent_file)

# ── Sidebar: filters ────────────────────────────────────────────────────────
st.sidebar.title("Filters")

all_domains = sorted({p.domain for p in intent_file.products})
selected_domains = st.sidebar.multiselect("Domain", all_domains, default=all_domains)

all_tiers = ["gold", "silver", "bronze"]
selected_tiers = st.sidebar.multiselect("Tier", all_tiers, default=all_tiers)

all_tags = sorted({t for p in intent_file.products for t in p.tags})
selected_tags = st.sidebar.multiselect("Tags", all_tags, default=[])

# Filter products
filtered_products = [
    p for p in intent_file.products
    if p.domain in selected_domains
    and p.tier in selected_tiers
    and (not selected_tags or any(t in selected_tags for t in p.tags))
]

# ── Header ──────────────────────────────────────────────────────────────────
st.title("Data Product Catalog")
st.markdown(
    f"**{len(filtered_products)}** products across "
    f"**{len(all_domains)}** domains"
)

# ── Summary cards ───────────────────────────────────────────────────────────
col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Products", len(intent_file.products))
col2.metric("Gold Tier", sum(1 for p in intent_file.products if p.tier == "gold"))
col3.metric("Silver Tier", sum(1 for p in intent_file.products if p.tier == "silver"))
col4.metric("Bronze Tier", sum(1 for p in intent_file.products if p.tier == "bronze"))

st.divider()

# ── Product cards ───────────────────────────────────────────────────────────
for product in filtered_products:
    icon = TIER_ICONS.get(product.tier, "📦")
    with st.expander(f"{icon} **{product.name}** — {product.description}", expanded=False):
        # Metadata row
        meta_cols = st.columns(4)
        meta_cols[0].markdown(f"**Owner:** {product.owner}")
        meta_cols[1].markdown(f"**Domain:** {product.domain}")
        meta_cols[2].markdown(f"**Tier:** {product.tier.title()}")
        meta_cols[3].markdown(f"**Tags:** {', '.join(product.tags)}")

        # Dependencies
        if product.depends_on:
            st.markdown(f"**Depends on:** {', '.join(product.depends_on)}")

        st.divider()

        # Two-column layout: metrics and intents
        left, right = st.columns(2)

        with left:
            st.markdown("##### Exposed Metrics")
            for m_name in product.metric_names:
                metric = intent_file.get_metric(m_name)
                if metric:
                    st.markdown(
                        f"- **{metric.name}** ({metric.metric_type}) — "
                        f"{metric.description}"
                    )

        with right:
            st.markdown("##### Business Questions Answered")
            for intent_name in product.intent_names:
                st.markdown(f'- "{intent_name}"')

        st.divider()

        # Contract and SLOs
        contract_col, slo_col, output_col = st.columns(3)

        with contract_col:
            st.markdown("##### Quality Contract")
            if product.contracts:
                for rule in product.contracts:
                    st.markdown(f"- **{rule.check}** {rule.operator} `{rule.value}`")
            else:
                st.caption("No contracts defined")

        with slo_col:
            st.markdown("##### SLOs")
            if product.slos:
                for slo in product.slos:
                    st.markdown(f"- **{slo.name}:** {slo.value}")
            else:
                st.caption("No SLOs defined")

        with output_col:
            st.markdown("##### Output Port")
            if product.output.table:
                st.markdown(f"- **Table:** `{product.output.table}`")
            if product.output.formats:
                st.markdown(f"- **Formats:** {', '.join(product.output.formats)}")
            if product.output.schedule:
                st.markdown(f"- **Schedule:** {product.output.schedule}")

        # Expandable raw manifest
        with st.popover("View Manifest JSON"):
            manifest = compile_product_manifest(product, intent_file)
            st.json(manifest)

        with st.popover("View Data Contract"):
            contract = compile_data_contract(product, intent_file)
            st.json(contract)


# ── Dependency graph (text-based) ───────────────────────────────────────────
st.divider()
st.subheader("Dependency Graph")

dep_edges = catalog.get("dependency_graph", [])
if dep_edges:
    for edge in dep_edges:
        st.markdown(f"**{edge['from']}** → {edge['to']}")
else:
    st.caption("No inter-product dependencies")

# ── Full catalog JSON ───────────────────────────────────────────────────────
st.divider()
with st.expander("Full Catalog JSON"):
    st.json(catalog)
