"""Rich terminal summaries for SignalHarness outputs."""

from __future__ import annotations

from typing import Iterable

from rich.console import Console
from rich.table import Table

from signal_harness.signal.schemas import SignalAssessment, SignalEvent


def render_assessment_table(
    signals: Iterable[SignalEvent],
    assessments: Iterable[SignalAssessment],
    *,
    console: Console | None = None,
) -> None:
    """Render the compact scan/report table."""

    target = console or Console()
    events = {event.event_id: event for event in signals}
    table = Table(title="SignalHarness Radar")
    table.add_column("Signal", overflow="fold")
    table.add_column("Score", justify="right")
    table.add_column("Category")
    table.add_column("Action")
    for assessment in sorted(assessments, key=lambda item: item.impact_score, reverse=True):
        event = events.get(assessment.event_id)
        table.add_row(
            event.title if event else assessment.event_id,
            f"{assessment.impact_score:.1f}",
            assessment.category.value,
            assessment.decision.value,
        )
    target.print(table)
