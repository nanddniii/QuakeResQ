"""Priority-ordered rescue-team allocation using road-network routes."""

from __future__ import annotations

import math
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Assignment, Incident, RescueTeam
from app.schemas.entities import (
    AllocationAssignmentDetail,
    AllocationResponse,
    AssignmentRequest,
    PlanningRunRequest,
    UnassignedIncidentDetail,
)
from app.services.planning_persistence import PlanningPersistenceService
from app.services.routing_service import RouteResult, RoutingService

ACTIVE_ASSIGNMENT_STATUSES = ("pending", "assigned", "in_progress")
ASSIGNMENT_WORKLOAD = 1


class AllocationRoutingError(RuntimeError):
    """Raised when RoutingService cannot calculate a usable road route."""


@contextmanager
def _transaction(session: Session) -> Iterator[None]:
    """Join caller-owned transactions, or commit this operation when standalone."""
    if session.in_transaction():
        yield
    else:
        with session.begin():
            yield


def allocate_incidents(
    session: Session, routing_service: RoutingService
) -> AllocationResponse:
    with _transaction(session):
        # 1. Query all non-terminal incidents ordered by priority score descending
        all_incidents = list(
            session.scalars(
                select(Incident)
                .where(Incident.status.notin_(("resolved", "closed")))
                .order_by(
                    Incident.priority_score.is_(None).asc(),
                    Incident.priority_score.desc(),
                    Incident.id.asc(),
                )
                .with_for_update()
            ).all()
        )

        # 2. Query all existing active assignments from database
        active_assignments = session.execute(
            select(Assignment.incident_id, Assignment.rescue_team_id).where(
                Assignment.status.in_(ACTIVE_ASSIGNMENT_STATUSES)
            )
        ).all()
        active_incident_ids = {incident_id for incident_id, _ in active_assignments}

        # 3. Separate incidents into pending (need assignment) vs already assigned
        pending_incidents = [
            inc for inc in all_incidents if inc.id not in active_incident_ids
        ]
        already_assigned_incidents = [
            inc for inc in all_incidents if inc.id in active_incident_ids
        ]

        # 4. Query active rescue teams (both 'available' and 'assigned' can have remaining capacity)
        candidate_teams = list(
            session.scalars(
                select(RescueTeam)
                .where(RescueTeam.status.in_(("available", "assigned")))
                .order_by(RescueTeam.id.asc())
                .with_for_update()
            ).all()
        )

        # 5. Compute dynamic remaining capacity per team based on active assignments
        team_workload: dict[int, int] = {}
        for _, tid in active_assignments:
            team_workload[tid] = team_workload.get(tid, 0) + ASSIGNMENT_WORKLOAD

        team_remaining_capacity: dict[int, int] = {
            team.id: team.capacity - team_workload.get(team.id, 0)
            for team in candidate_teams
        }

        # Eligible teams are those with remaining capacity >= ASSIGNMENT_WORKLOAD
        eligible_teams = [
            team
            for team in candidate_teams
            if team_remaining_capacity[team.id] >= ASSIGNMENT_WORKLOAD
        ]

        planned_assignments: list[tuple[Incident, RescueTeam, RouteResult]] = []
        unassigned: list[UnassignedIncidentDetail] = []

        # 6. Allocation loop over pending incidents only
        for incident in pending_incidents:
            if not candidate_teams:
                reason = "No rescue teams are currently available."
            elif not eligible_teams:
                reason = "All eligible teams have reached maximum capacity."
            else:
                route_candidates: list[tuple[RescueTeam, RouteResult]] = []
                for team in eligible_teams:
                    try:
                        route = routing_service.find_route(
                            start_lat=team.latitude,
                            start_lon=team.longitude,
                            dest_lat=incident.latitude,
                            dest_lon=incident.longitude,
                            weight="travel_time",
                            algorithm="dijkstra",
                        )
                    except (RuntimeError, ValueError) as exc:
                        raise AllocationRoutingError(str(exc)) from exc

                    if route.status == "no_route":
                        continue
                    if route.status != "success":
                        raise AllocationRoutingError(
                            f"RoutingService returned unexpected status {route.status!r}."
                        )
                    if (
                        not math.isfinite(route.total_distance_km)
                        or route.total_distance_km < 0
                        or not math.isfinite(route.estimated_time_minutes)
                        or route.estimated_time_minutes < 0
                    ):
                        raise AllocationRoutingError(
                            "RoutingService returned invalid route metrics."
                        )
                    route_candidates.append((team, route))

                if not route_candidates:
                    reason = "No remaining team has a reachable road route."
                else:
                    team, route = min(
                        route_candidates,
                        key=lambda candidate: (
                            candidate[1].estimated_time_minutes,
                            candidate[1].total_distance_km,
                            candidate[0].id,
                        ),
                    )
                    planned_assignments.append((incident, team, route))
                    team_remaining_capacity[team.id] -= ASSIGNMENT_WORKLOAD
                    if team_remaining_capacity[team.id] < ASSIGNMENT_WORKLOAD:
                        eligible_teams.remove(team)
                    continue

            unassigned.append(
                UnassignedIncidentDetail(
                    incident_id=incident.id,
                    title=incident.title,
                    reason=reason,
                )
            )

        # 7. Persist PlanningRun and new Assignments
        persistence = PlanningPersistenceService(session)
        planning_run = persistence.create_planning_run(
            PlanningRunRequest(status="running")
        )
        assignment_details: list[AllocationAssignmentDetail] = []
        for incident, team, route in planned_assignments:
            assignment = persistence.create_assignment(
                AssignmentRequest(
                    planning_id=planning_run.id,
                    incident_id=incident.id,
                    rescue_team_id=team.id,
                    status="assigned",
                )
            )
            incident.status = "assigned"
            assignment_details.append(
                AllocationAssignmentDetail(
                    assignment_id=assignment.id,
                    incident_id=incident.id,
                    incident_title=incident.title,
                    rescue_team_id=team.id,
                    rescue_team_name=team.name,
                    status=assignment.status,
                    priority_score=incident.priority_score,
                    priority_level=incident.priority_level,
                    road_distance_km=route.total_distance_km,
                    estimated_time_minutes=route.estimated_time_minutes,
                )
            )

        # 8. Update team statuses consistently based on remaining capacity
        for team in candidate_teams:
            if team_remaining_capacity.get(team.id, 0) <= 0:
                team.status = "assigned"
            else:
                team.status = "available"

        # 9. Format Summary distinguishing newly allocated, unassigned, and already assigned
        if not all_incidents:
            summary = "No eligible incidents were found."
        else:
            parts = [f"Allocated {len(assignment_details)} incident(s)"]
            if unassigned:
                parts.append(f"{len(unassigned)} incident(s) remain unassigned")
            else:
                parts.append("0 incident(s) remain unassigned")
            if already_assigned_incidents:
                parts.append(f"{len(already_assigned_incidents)} incident(s) already assigned")
            summary = "; ".join(parts) + "."

        persistence.update_planning_run(
            planning_run.id,
            PlanningRunRequest(
                status="completed",
                completed_at=datetime.now(timezone.utc),
                summary=summary,
            ),
        )
        session.flush()

        return AllocationResponse(
            planning_run_id=planning_run.id,
            status="completed",
            assignments=assignment_details,
            unassigned_incidents=unassigned,
            summary=summary,
        )
