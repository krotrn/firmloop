"""Compare a base spec with a follow-up spec by item id."""

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from firmloop.spec.models import Rule, Scenario, Spec


class IdDiff(BaseModel):
    """Ids of one kind of item (rules or scenarios) grouped by what happened to them."""

    model_config = ConfigDict(extra="forbid")

    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    modified: list[str] = Field(default_factory=list)
    unchanged: list[str] = Field(default_factory=list)


class SpecDiff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary_changed: bool
    summary_old: str | None = None
    summary_new: str | None = None
    rules: IdDiff
    scenarios: IdDiff


def _diff_items(base: Sequence[Rule | Scenario], new: Sequence[Rule | Scenario]) -> IdDiff:
    """Added, modified and unchanged ids follow ``new`` order; removed ids follow ``base`` order."""
    base_by_id = {item.id: item for item in base}
    new_ids = {item.id for item in new}
    out = IdDiff()
    for item in new:
        before = base_by_id.get(item.id)
        if before is None:
            out.added.append(item.id)
        elif before == item:
            out.unchanged.append(item.id)
        else:
            out.modified.append(item.id)
    out.removed = [item.id for item in base if item.id not in new_ids]
    return out


def diff_specs(base: Spec, new: Spec) -> SpecDiff:
    """Compare two specs; any field difference makes an item ``modified``."""
    changed = base.summary != new.summary
    return SpecDiff(
        summary_changed=changed,
        summary_old=base.summary if changed else None,
        summary_new=new.summary if changed else None,
        rules=_diff_items(base.rules, new.rules),
        scenarios=_diff_items(base.scenarios, new.scenarios),
    )
