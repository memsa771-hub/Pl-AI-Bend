"""Planner intelligence — next actions. Does not persist or execute them."""

from __future__ import annotations

from pai.kernel.contracts.schemas import TaskProposal


def plan_next_actions(state: dict) -> list[TaskProposal]:
    # ponytail: counselor-proposed tasks until a dedicated planning LLM lives here.
    return list(state.get("task_proposals") or [])
