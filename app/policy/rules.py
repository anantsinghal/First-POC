from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from ..domain.values import clean, parse_date, parse_float


@dataclass(frozen=True)
class Condition:
    """A single condition in a rule's when or expect."""
    field: str
    op: str
    value: Any = None


@dataclass(frozen=True)
class Rule:
    """Declarative rule definition loaded from JSON catalog."""
    rule_id: str
    object_name: str
    finding_type: str
    title: str
    when: tuple[Condition, ...]
    expect: Condition | None
    explanation: str
    field_name: str | None = None

    def referenced_fields(self) -> set[str]:
        fields: set[str] = {c.field for c in self.when}
        if self.expect:
            fields.add(self.expect.field)
        if self.field_name:
            fields.add(self.field_name)
        return fields


def load_rule_catalog(directory: Path | None = None) -> list[Rule]:
    """Load all *.json rule files from the given directory (defaults to app/policy/rules)."""
    if directory is None:
        directory = Path(__file__).resolve().parent / "rules"
    if not directory.exists() or not directory.is_dir():
        return []
    rules: list[Rule] = []
    for json_file in sorted(directory.glob("*.json")):
        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        for item in data:
            when = tuple(Condition(**c) for c in item.get("when", []))
            expect = Condition(**item["expect"]) if item.get("expect") else None
            rule = Rule(
                rule_id=item["rule_id"],
                object_name=item["object_name"],
                finding_type=item["finding_type"],
                title=item["title"],
                when=when,
                expect=expect,
                explanation=item["explanation"],
                field_name=item.get("field_name"),
            )
            rules.append(rule)
    return rules


def evaluate_condition(record: dict[str, str], cond: Condition, today: date | None = None) -> bool:
    """Evaluate a single condition against a record. Supports the listed operators."""
    val = clean(record.get(cond.field))
    op = cond.op
    expected = cond.value

    if op == "blank":
        return not val
    if op == "not_blank":
        return bool(val)

    if op in ("equals", "not_equals", "in", "not_in"):
        # string compare for most, but handle lists
        if op in ("in", "not_in") and not isinstance(expected, (list, tuple)):
            expected = [expected]
        if op in ("equals", "not_equals"):
            match = (val == str(expected) if expected is not None else val == "")
            return match if op == "equals" else not match
        else:
            exp_list = [str(e) for e in expected]
            match = val in exp_list
            return match if op == "in" else not match

    if op in ("lt", "lte", "gt", "gte"):
        num_val = parse_float(val)
        num_exp = parse_float(expected) if expected is not None else None
        if num_val is None or num_exp is None:
            return False
        if op == "lt":
            return num_val < num_exp
        if op == "lte":
            return num_val <= num_exp
        if op == "gt":
            return num_val > num_exp
        if op == "gte":
            return num_val >= num_exp

    if op in ("before", "after"):
        date_val = parse_date(val)
        if date_val is None:
            return False
        if isinstance(expected, str) and expected.lower() == "today":
            exp_date = today or date.today()
        else:
            exp_date = parse_date(expected) if expected is not None else None
        if exp_date is None:
            return False
        if op == "before":
            return date_val < exp_date
        if op == "after":
            return date_val > exp_date

    return False
