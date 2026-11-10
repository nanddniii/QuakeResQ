from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import PlanningRun
from app.schemas.entities import PlanningRunRequest


def test_planning_run_omitted_started_at_uses_model_default() -> None:
    request = PlanningRunRequest(summary="Initial run")

    assert "started_at" not in request.to_model_kwargs()
    assert request.to_model_kwargs() == {"summary": "Initial run"}

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        planning_run = PlanningRun(**request.to_model_kwargs())
        session.add(planning_run)
        session.flush()

        assert planning_run.started_at is not None
        assert planning_run.summary == "Initial run"


def test_planning_run_explicit_null_started_at_is_rejected() -> None:
    with pytest.raises(ValidationError, match="started_at cannot be null"):
        PlanningRunRequest(started_at=None)


def test_planning_run_preserves_explicit_started_at() -> None:
    started_at = datetime(2026, 11, 10, tzinfo=timezone.utc)
    request = PlanningRunRequest(started_at=started_at, summary="Initial run")

    assert request.to_model_kwargs() == {
        "started_at": started_at,
        "summary": "Initial run",
    }
