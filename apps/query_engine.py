"""
Hybrid intent + LLM query engine for MetricFlow.

Routes questions through two layers:
1. Intent classifier - fast, deterministic, scored by confidence
2. LLM fallback - flexible, handles anything the intents can't

High-confidence intent matches skip the LLM entirely (fast, free, accurate).
Low-confidence matches get routed to the LLM with the intent's best guess
as a hint, so the LLM has a starting point.
"""

import json
import os
import re
import subprocess
from dataclasses import dataclass, field

import pandas as pd


# ── Intent Definitions ───────────────────────────────────────────────────────
# Each intent maps a "concept" to a MetricFlow metric or dimension.
# Synonyms and example phrases drive the matching -- the more you add,
# the more accurate the intent layer becomes.


@dataclass
class MetricIntent:
    """A metric that can be detected from natural language."""
    metric_name: str
    display_name: str
    # Words/phrases that strongly indicate this metric
    keywords: list[str] = field(default_factory=list)
    # Phrases that should NOT match (to disambiguate similar metrics)
    anti_keywords: list[str] = field(default_factory=list)
    # Weight boost for exact phrase matches vs single-word matches
    phrase_boost: float = 2.0


@dataclass
class DimensionIntent:
    """A dimension/grouping that can be detected from natural language."""
    dimension_name: str
    display_name: str
    keywords: list[str] = field(default_factory=list)


@dataclass
class FilterIntent:
    """A filter condition that can be detected from natural language."""
    where_clause: str
    keywords: list[str] = field(default_factory=list)


# ── Metric intents ───────────────────────────────────────────────────────────

METRIC_INTENTS = [
    MetricIntent(
        metric_name="total_revenue",
        display_name="Total Revenue",
        keywords=[
            "revenue", "sales", "income", "earnings", "money",
            "how much did we make", "total sales", "gross revenue",
            "sales volume", "dollars", "spend",
        ],
        anti_keywords=["per item", "per unit", "cumulative", "running total", "trailing"],
    ),
    MetricIntent(
        metric_name="total_order_count",
        display_name="Total Orders",
        keywords=[
            "order count", "number of orders", "how many orders",
            "orders placed", "total orders", "order volume",
        ],
        anti_keywords=["return", "completed", "average order"],
    ),
    MetricIntent(
        metric_name="avg_order_value",
        display_name="Average Order Value",
        keywords=[
            "average order value", "aov", "avg order", "mean order value",
            "order average", "average basket", "basket size",
            "typical order", "average purchase",
        ],
    ),
    MetricIntent(
        metric_name="total_items_sold",
        display_name="Items Sold",
        keywords=[
            "items sold", "units sold", "quantity sold", "how many items",
            "products sold", "volume sold", "units", "pieces sold",
            "how much stuff", "what sold", "things sold",
        ],
    ),
    MetricIntent(
        metric_name="total_completed_orders",
        display_name="Completed Orders",
        keywords=[
            "completed orders", "successful orders", "fulfilled orders",
            "orders completed", "finished orders",
        ],
    ),
    MetricIntent(
        metric_name="total_returned_orders",
        display_name="Returned Orders",
        keywords=[
            "returned orders", "returns", "refunds", "sent back",
            "orders returned", "return count",
        ],
        anti_keywords=["rate", "percentage", "ratio"],
    ),
    MetricIntent(
        metric_name="order_return_rate",
        display_name="Return Rate",
        keywords=[
            "return rate", "refund rate", "return percentage",
            "what percentage returned", "return ratio",
            "how often returned", "return frequency",
        ],
    ),
    MetricIntent(
        metric_name="revenue_per_item",
        display_name="Revenue Per Item",
        keywords=[
            "revenue per item", "revenue per unit", "per item revenue",
            "average item revenue", "dollars per item",
            "revenue per product", "per unit revenue",
        ],
    ),
    MetricIntent(
        metric_name="cumulative_revenue",
        display_name="Cumulative Revenue",
        keywords=[
            "cumulative revenue", "running total", "total over time",
            "accumulated revenue", "revenue to date", "cumulative sales",
            "running revenue",
        ],
    ),
    MetricIntent(
        metric_name="trailing_7d_revenue",
        display_name="7-Day Trailing Revenue",
        keywords=[
            "trailing revenue", "7 day", "7-day", "rolling revenue",
            "weekly rolling", "last 7 days", "trailing week",
        ],
    ),
]

# ── Dimension intents ────────────────────────────────────────────────────────

DIMENSION_INTENTS = [
    DimensionIntent(
        dimension_name="metric_time__month",
        display_name="Monthly",
        keywords=["month", "monthly", "by month", "over time", "trend", "over months"],
    ),
    DimensionIntent(
        dimension_name="metric_time__quarter",
        display_name="Quarterly",
        keywords=["quarter", "quarterly", "by quarter", "q1", "q2", "q3", "q4"],
    ),
    DimensionIntent(
        dimension_name="metric_time__week",
        display_name="Weekly",
        keywords=["week", "weekly", "by week"],
    ),
    DimensionIntent(
        dimension_name="metric_time__day",
        display_name="Daily",
        keywords=["day", "daily", "by day", "each day"],
    ),
    DimensionIntent(
        dimension_name="metric_time__year",
        display_name="Yearly",
        keywords=["year", "yearly", "annual", "by year"],
    ),
    DimensionIntent(
        dimension_name="order_id__customer_region",
        display_name="Region",
        keywords=[
            "region", "geography", "by region", "geographic",
            "location", "area", "territory", "where",
        ],
    ),
    DimensionIntent(
        dimension_name="order_item_id__product_category",
        display_name="Product Category",
        keywords=[
            "product", "category", "by product", "by category",
            "product type", "product category", "what product",
        ],
    ),
    DimensionIntent(
        dimension_name="order_id__customer_name",
        display_name="Customer",
        keywords=[
            "customer", "by customer", "who", "buyer", "client",
            "top customers", "best customers",
        ],
    ),
    DimensionIntent(
        dimension_name="order_id__order_status",
        display_name="Order Status",
        keywords=["status", "by status", "order status"],
    ),
]

# ── Filter intents ───────────────────────────────────────────────────────────

FILTER_INTENTS = [
    FilterIntent(
        where_clause="{{ Dimension('order_id__customer_region') }} = 'us_west'",
        keywords=["us west", "west coast", "western us", "us_west"],
    ),
    FilterIntent(
        where_clause="{{ Dimension('order_id__customer_region') }} = 'us_east'",
        keywords=["us east", "east coast", "eastern us", "us_east"],
    ),
    FilterIntent(
        where_clause="{{ Dimension('order_id__customer_region') }} = 'eu_west'",
        keywords=["europe", "eu", "european", "eu west", "eu_west"],
    ),
    FilterIntent(
        where_clause="{{ Dimension('order_id__order_status') }} = 'completed'",
        keywords=["completed", "successful", "fulfilled"],
    ),
    FilterIntent(
        where_clause="{{ Dimension('order_id__order_status') }} = 'returned'",
        keywords=["returned", "refunded"],
    ),
]


# ── Confidence Scoring ───────────────────────────────────────────────────────


def _score_intent(query: str, keywords: list[str], anti_keywords: list[str] | None = None) -> float:
    """
    Score how well a query matches an intent's keywords.

    Returns 0.0 - 1.0:
      - Multi-word phrase match in query = high score
      - Single word match = medium score
      - Anti-keyword present = score penalty
      - No match = 0.0
    """
    q = query.lower()
    score = 0.0
    max_possible = 0.0

    for keyword in keywords:
        keyword_lower = keyword.lower()
        weight = len(keyword_lower.split())  # multi-word phrases worth more

        max_possible += weight

        if keyword_lower in q:
            score += weight
            # Bonus for exact phrase (not just substring of a longer word)
            if re.search(r'\b' + re.escape(keyword_lower) + r'\b', q):
                score += weight * 0.5

    # Penalty for anti-keywords
    if anti_keywords:
        for anti in anti_keywords:
            if anti.lower() in q:
                score *= 0.3  # heavy penalty

    if max_possible == 0:
        return 0.0

    # Normalize to 0-1, but cap contribution
    raw = score / max_possible
    # Boost: if any multi-word phrase matched, that's a strong signal
    if any(kw.lower() in q for kw in keywords if len(kw.split()) > 1):
        raw = min(1.0, raw + 0.3)

    return min(1.0, raw)


@dataclass
class IntentResult:
    """The result of intent classification."""
    metrics: list[str]
    metric_confidence: float
    group_by: list[str]
    dimension_confidence: float
    where: str | None
    filter_confidence: float
    explanation: str

    @property
    def overall_confidence(self) -> float:
        """Weighted average -- metric choice matters most."""
        return (
            self.metric_confidence * 0.6
            + self.dimension_confidence * 0.3
            + self.filter_confidence * 0.1
        )


def classify_intent(question: str) -> IntentResult:
    """
    Classify a natural language question into MetricFlow parameters.
    Returns scored results so the router can decide intent-vs-LLM.
    """
    q = question.lower()

    # ── Score all metric intents ─────────────────────────────────────────
    metric_scores = []
    for intent in METRIC_INTENTS:
        score = _score_intent(q, intent.keywords, intent.anti_keywords)
        metric_scores.append((intent, score))

    metric_scores.sort(key=lambda x: x[1], reverse=True)
    best_metric = metric_scores[0]

    # Check if user wants multiple metrics (e.g., "revenue and orders")
    metrics = [best_metric[0].metric_name]
    metric_conf = best_metric[1]

    # Detect multi-metric intent
    if " and " in q or " vs " in q or " versus " in q or " compared to " in q:
        second_best = metric_scores[1]
        if second_best[1] > 0.2:
            metrics.append(second_best[0].metric_name)
            metric_conf = (best_metric[1] + second_best[1]) / 2

    # ── Score all dimension intents ──────────────────────────────────────
    dim_scores = []
    for intent in DIMENSION_INTENTS:
        score = _score_intent(q, intent.keywords)
        dim_scores.append((intent, score))

    dim_scores.sort(key=lambda x: x[1], reverse=True)
    best_dim = dim_scores[0]

    group_by = [best_dim[0].dimension_name] if best_dim[1] > 0 else ["metric_time__month"]
    dim_conf = best_dim[1] if best_dim[1] > 0 else 0.5  # default is a reasonable guess

    # ── Score all filter intents ─────────────────────────────────────────
    filter_scores = []
    for intent in FILTER_INTENTS:
        score = _score_intent(q, intent.keywords)
        filter_scores.append((intent, score))

    filter_scores.sort(key=lambda x: x[1], reverse=True)
    best_filter = filter_scores[0]

    where = best_filter[0].where_clause if best_filter[1] > 0.3 else None
    filter_conf = best_filter[1] if where else 1.0  # no filter needed = confident

    # ── Build explanation ────────────────────────────────────────────────
    metric_names = [m.replace("_", " ").title() for m in metrics]
    dim_name = best_dim[0].display_name if best_dim[1] > 0 else "Monthly"
    explanation = f"Showing {', '.join(metric_names)} by {dim_name}"
    if where:
        explanation += f" (filtered)"

    return IntentResult(
        metrics=metrics,
        metric_confidence=metric_conf,
        group_by=group_by,
        dimension_confidence=dim_conf,
        where=where,
        filter_confidence=filter_conf,
        explanation=explanation,
    )


# ── LLM Layer ────────────────────────────────────────────────────────────────

SCHEMA = """
Available metrics:
- total_revenue: Total revenue across all orders
- total_order_count: Total number of orders placed
- avg_order_value: Average value per order
- total_items_sold: Total quantity of items sold
- total_completed_orders: Number of completed orders
- total_returned_orders: Number of returned orders
- order_return_rate: Percentage of orders returned (derived)
- revenue_per_item: Revenue per item sold (derived)
- cumulative_revenue: Running total of revenue over all time
- trailing_7d_revenue: Rolling 7-day revenue window

Available dimensions:
- metric_time__day, metric_time__week, metric_time__month, metric_time__quarter, metric_time__year
- order_id__customer_region (values: us_west, us_east, eu_west)
- order_id__order_status (values: completed, returned, cancelled)
- order_id__customer_name
- order_item_id__product_category (values: widgets, gadgets, gizmos)
- order_item_id__product_name

Where filter syntax:
- {{ Dimension('order_id__customer_region') }} = 'us_west'
- {{ Dimension('order_id__order_status') }} = 'completed'
- {{ TimeDimension('metric_time', 'month') }} >= '2024-06-01'
"""


def query_with_llm(question: str, intent_hint: IntentResult | None = None) -> dict | None:
    """
    Use Claude to translate a question into MetricFlow parameters.
    Optionally receives an intent hint (the intent layer's best guess)
    so the LLM can refine rather than start from scratch.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    hint_text = ""
    if intent_hint:
        hint_text = f"""
My best guess from keyword matching (confidence: {intent_hint.overall_confidence:.0%}):
- metrics: {','.join(intent_hint.metrics)}
- group_by: {','.join(intent_hint.group_by)}
- where: {intent_hint.where or 'none'}

Refine this if wrong, or confirm if correct.
"""

    system_prompt = f"""You translate natural language into MetricFlow query parameters.

{SCHEMA}

Respond with ONLY a JSON object:
{{"metrics": "comma,separated", "group_by": "comma,separated", "where": "filter or null", "explanation": "brief description"}}

Rules:
- Default time grain is metric_time__month unless user specifies otherwise
- Use order_item_id__ prefix for product dimensions, order_id__ for customer/order dimensions
- Only use metrics and dimensions from the schema
"""

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        user_msg = question
        if hint_text:
            user_msg += f"\n\n---\n{hint_text}"

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            system=system_prompt,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = response.content[0].text.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)
    except Exception:
        return None


# ── Hybrid Router ────────────────────────────────────────────────────────────

CONFIDENCE_THRESHOLD = 0.7  # Above this, trust the intent layer


@dataclass
class QueryResult:
    """Final routed query result."""
    metrics: str
    group_by: str
    where: str | None
    explanation: str
    routed_via: str  # "intent" or "llm" or "llm_with_hint"
    confidence: float


def route_question(question: str) -> QueryResult:
    """
    Main entry point. Routes through intent classifier first,
    then to LLM if confidence is too low.
    """
    # Step 1: Always run intent classification (it's instant)
    intent = classify_intent(question)

    # Step 2: If confidence is high, use intent directly
    if intent.overall_confidence >= CONFIDENCE_THRESHOLD:
        return QueryResult(
            metrics=",".join(intent.metrics),
            group_by=",".join(intent.group_by),
            where=intent.where,
            explanation=intent.explanation,
            routed_via="intent",
            confidence=intent.overall_confidence,
        )

    # Step 3: Try LLM with the intent as a hint
    llm_result = query_with_llm(question, intent_hint=intent)

    if llm_result:
        return QueryResult(
            metrics=llm_result["metrics"],
            group_by=llm_result["group_by"],
            where=llm_result.get("where"),
            explanation=llm_result.get("explanation", ""),
            routed_via="llm_with_hint",
            confidence=0.9,  # LLM + hint = high confidence
        )

    # Step 4: No LLM available, use intent result anyway (best effort)
    return QueryResult(
        metrics=",".join(intent.metrics),
        group_by=",".join(intent.group_by),
        where=intent.where,
        explanation=intent.explanation + " (low confidence)",
        routed_via="intent",
        confidence=intent.overall_confidence,
    )


# ── Query Execution ──────────────────────────────────────────────────────────


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
        return pd.DataFrame()

    cols = lines[header_idx].split()
    data_lines = []
    for line in lines[header_idx + 1:]:
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


def ask(question: str) -> tuple[pd.DataFrame, QueryResult]:
    """
    Complete pipeline: question -> route -> query -> results.
    Returns (dataframe, query_metadata).
    """
    result = route_question(question)
    df = run_mf_query(result.metrics, result.group_by, result.where)
    return df, result
