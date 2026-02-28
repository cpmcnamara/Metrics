"""
Parser for .intent files.

Reads the Intent DSL and produces structured Python objects that can be
compiled to MetricFlow YAML, LLM context, or an ontology graph.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


# ── Parsed AST nodes ─────────────────────────────────────────────────────────


@dataclass
class SourceEntity:
    name: str
    kind: str  # "primary" or "foreign"


@dataclass
class SourceDimension:
    name: str
    kind: str  # "categorical" or "time"


@dataclass
class SourceMeasure:
    name: str
    agg: str  # "sum", "count", "average", "max", "min"
    column: str


@dataclass
class Source:
    name: str
    table: str
    time_column: str
    time_granularity: str
    entities: list[SourceEntity] = field(default_factory=list)
    dimensions: list[SourceDimension] = field(default_factory=list)
    measures: list[SourceMeasure] = field(default_factory=list)


@dataclass
class Metric:
    name: str
    description: str = ""
    synonyms: list[str] = field(default_factory=list)
    source_ref: str | None = None  # "source.measure" for simple/cumulative
    formula: str | None = None  # for derived
    metric_type: str = "simple"  # "simple", "derived", "cumulative"
    window: str | None = None  # "7 days" etc


@dataclass
class Intent:
    question: str
    also_asked: list[str] = field(default_factory=list)
    metrics: list[str] = field(default_factory=list)
    dimensions: list[str] = field(default_factory=list)
    where: str | None = None
    sorted_by: str | None = None
    sort_order: str | None = None
    limit: int | None = None
    references: list[tuple[str, str]] = field(default_factory=list)  # (name, path)
    context: list[str] = field(default_factory=list)
    is_default: bool = False


@dataclass
class Group:
    name: str
    intent_names: list[str] = field(default_factory=list)


@dataclass
class IntentFile:
    """The complete parsed result of one or more .intent files."""
    sources: list[Source] = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)
    metrics: list[Metric] = field(default_factory=list)
    intents: list[Intent] = field(default_factory=list)
    groups: list[Group] = field(default_factory=list)

    def resolve_alias(self, name: str) -> str:
        """Resolve a dimension shorthand to its full MetricFlow path."""
        return self.aliases.get(name, name)

    def get_metric(self, name: str) -> Metric | None:
        """Look up a metric by name."""
        for m in self.metrics:
            if m.name == name:
                return m
        return None

    def validate(self) -> list[str]:
        """Return a list of validation errors (empty = valid)."""
        errors = []

        metric_names = {m.name for m in self.metrics}
        source_measures = set()
        for s in self.sources:
            for m in s.measures:
                source_measures.add(f"{s.name}.{m.name}")

        # Check metric references
        for metric in self.metrics:
            if metric.source_ref and metric.source_ref not in source_measures:
                errors.append(
                    f"Metric '{metric.name}' references '{metric.source_ref}' "
                    f"which is not a defined source.measure"
                )
            if metric.formula:
                # Extract metric names from formula
                tokens = re.findall(r'[a-z_]+', metric.formula)
                for token in tokens:
                    if token not in metric_names and token not in (
                        "and", "or", "not", "if", "else",
                    ):
                        # Could be a number or operator, skip
                        pass

        # Check intent metric references
        for intent in self.intents:
            for m in intent.metrics:
                if m not in metric_names:
                    errors.append(
                        f"Intent '{intent.question}' references metric '{m}' "
                        f"which is not defined"
                    )

        # Check only one default
        defaults = [i for i in self.intents if i.is_default]
        if len(defaults) > 1:
            errors.append(
                f"Multiple default intents: "
                f"{', '.join(i.question for i in defaults)}"
            )

        # Check group references
        intent_questions = {i.question for i in self.intents}
        for group in self.groups:
            for name in group.intent_names:
                if name not in intent_questions:
                    errors.append(
                        f"Group '{group.name}' references intent '{name}' "
                        f"which is not defined"
                    )

        return errors


# ── Parser ───────────────────────────────────────────────────────────────────


def _parse_quoted_list(text: str) -> list[str]:
    """Extract all quoted strings from a line."""
    return re.findall(r'"([^"]*)"', text)


def _strip_comment(line: str) -> str:
    """Remove trailing # comments from a line (but not inside quotes)."""
    in_quote = False
    for i, ch in enumerate(line):
        if ch == '"':
            in_quote = not in_quote
        elif ch == '#' and not in_quote:
            return line[:i].rstrip()
    return line


def parse(text: str) -> IntentFile:
    """Parse intent DSL text into an IntentFile."""
    result = IntentFile()
    lines = text.split('\n')
    i = 0

    while i < len(lines):
        line = _strip_comment(lines[i]).rstrip()

        # Skip blank lines and pure comment lines
        if not line or line.lstrip().startswith('#'):
            i += 1
            continue

        stripped = line.lstrip()

        # ── source block ─────────────────────────────────────────────
        if stripped.startswith('source '):
            source_name = stripped.split()[1]
            source = Source(name=source_name, table="", time_column="", time_granularity="day")
            i += 1

            while i < len(lines):
                child = _strip_comment(lines[i]).rstrip()
                if not child or child.lstrip().startswith('#'):
                    i += 1
                    continue
                indent = len(child) - len(child.lstrip())
                if indent == 0:
                    break

                parts = child.strip().split()
                keyword = parts[0]

                if keyword == 'table':
                    source.table = ' '.join(parts[1:])
                elif keyword == 'time':
                    source.time_column = parts[1]
                    source.time_granularity = parts[2] if len(parts) > 2 else "day"
                elif keyword == 'entity':
                    source.entities.append(SourceEntity(name=parts[1], kind=parts[2]))
                elif keyword == 'dimension':
                    source.dimensions.append(SourceDimension(name=parts[1], kind=parts[2]))
                elif keyword == 'measure':
                    # measure name agg(column)
                    measure_name = parts[1]
                    agg_expr = ' '.join(parts[2:])
                    match = re.match(r'(\w+)\((\w+)\)', agg_expr)
                    if match:
                        source.measures.append(SourceMeasure(
                            name=measure_name, agg=match.group(1), column=match.group(2),
                        ))
                    i += 1
                    continue

                i += 1

            result.sources.append(source)
            continue

        # ── alias ────────────────────────────────────────────────────
        if stripped.startswith('alias '):
            match = re.match(r'alias\s+(\w+)\s*=\s*(\S+)', stripped)
            if match:
                result.aliases[match.group(1)] = match.group(2)
            i += 1
            continue

        # ── metric block ─────────────────────────────────────────────
        if stripped.startswith('metric '):
            metric_name = stripped.split()[1]
            metric = Metric(name=metric_name)
            i += 1

            while i < len(lines):
                child = _strip_comment(lines[i]).rstrip()
                if not child or child.lstrip().startswith('#'):
                    i += 1
                    continue
                indent = len(child) - len(child.lstrip())
                if indent == 0:
                    break

                child_stripped = child.strip()

                if child_stripped.startswith('description '):
                    quoted = _parse_quoted_list(child_stripped)
                    metric.description = quoted[0] if quoted else child_stripped.split(None, 1)[1]
                elif child_stripped.startswith('also called '):
                    metric.synonyms = _parse_quoted_list(child_stripped)
                elif child_stripped.startswith('from '):
                    metric.source_ref = child_stripped.split()[1]
                elif child_stripped.startswith('formula '):
                    metric.formula = child_stripped.split(None, 1)[1]
                elif child_stripped.startswith('type '):
                    metric.metric_type = child_stripped.split()[1]
                elif child_stripped.startswith('window '):
                    metric.window = child_stripped.split(None, 1)[1]

                i += 1

            result.metrics.append(metric)
            continue

        # ── intent block ─────────────────────────────────────────────
        if stripped.startswith('intent '):
            quoted = _parse_quoted_list(stripped)
            question = quoted[0] if quoted else stripped.split(None, 1)[1]
            intent = Intent(question=question)
            i += 1

            while i < len(lines):
                child = _strip_comment(lines[i]).rstrip()
                if not child or child.lstrip().startswith('#'):
                    i += 1
                    continue
                indent = len(child) - len(child.lstrip())
                if indent == 0:
                    break

                child_stripped = child.strip()

                if child_stripped.startswith('also asked '):
                    intent.also_asked = _parse_quoted_list(child_stripped)
                elif child_stripped.startswith('show '):
                    metrics_str = child_stripped.split(None, 1)[1]
                    intent.metrics = [m.strip() for m in metrics_str.split(',')]
                elif child_stripped.startswith('by '):
                    dims_str = child_stripped.split(None, 1)[1]
                    intent.dimensions = [d.strip() for d in dims_str.split(',')]
                elif child_stripped.startswith('where '):
                    intent.where = child_stripped.split(None, 1)[1]
                elif child_stripped.startswith('sorted by '):
                    sort_parts = child_stripped.split()
                    # "sorted by metric_name desc"
                    intent.sorted_by = sort_parts[2]
                    intent.sort_order = sort_parts[3] if len(sort_parts) > 3 else "desc"
                elif child_stripped.startswith('limit '):
                    intent.limit = int(child_stripped.split()[1])
                elif child_stripped.startswith('reference '):
                    # reference "Name" from path
                    ref_name = _parse_quoted_list(child_stripped)
                    ref_path_match = re.search(r'from\s+(\S+)', child_stripped)
                    if ref_name and ref_path_match:
                        intent.references.append((ref_name[0], ref_path_match.group(1)))
                elif child_stripped.startswith('context '):
                    ctx = _parse_quoted_list(child_stripped)
                    if ctx:
                        intent.context.extend(ctx)
                elif child_stripped == 'default':
                    intent.is_default = True

                i += 1

            result.intents.append(intent)
            continue

        # ── group block ──────────────────────────────────────────────
        if stripped.startswith('group '):
            quoted = _parse_quoted_list(stripped)
            group_name = quoted[0] if quoted else stripped.split(None, 1)[1]
            group = Group(name=group_name)
            i += 1

            while i < len(lines):
                child = _strip_comment(lines[i]).rstrip()
                if not child or child.lstrip().startswith('#'):
                    i += 1
                    continue
                indent = len(child) - len(child.lstrip())
                if indent == 0:
                    break

                child_stripped = child.strip()

                if child_stripped == 'includes':
                    i += 1
                    continue

                # Lines inside includes are quoted intent names
                quoted_names = _parse_quoted_list(child_stripped)
                if quoted_names:
                    group.intent_names.extend(quoted_names)
                i += 1

            result.groups.append(group)
            continue

        i += 1

    return result


def parse_file(path: str | Path) -> IntentFile:
    """Parse a single .intent file."""
    return parse(Path(path).read_text())


def parse_directory(path: str | Path) -> IntentFile:
    """Parse all .intent files in a directory and merge them."""
    merged = IntentFile()
    for intent_file in sorted(Path(path).glob('*.intent')):
        parsed = parse_file(intent_file)
        merged.sources.extend(parsed.sources)
        merged.aliases.update(parsed.aliases)
        merged.metrics.extend(parsed.metrics)
        merged.intents.extend(parsed.intents)
        merged.groups.extend(parsed.groups)
    return merged
