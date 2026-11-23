"""Priority-ordered rescue-team allocation using road-network routes."""

from __future__ import annotations

import math
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


def allocate_incidents(
    session: Session, routing_service: RoutingService
) -> AllocationResponse:
    with session.begin():
        incidents = list(
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
        available_teams = list(
            session.scalars(
                select(RescueTeam)
                .where(RescueTeam.status == "available")
                .order_by(RescueTeam.id.asc())
                .with_for_update()
            ).all()
        )
        active_assignments = session.execute(
            select(Assignment.incident_id, Assignment.rescue_team_id).where(
                Assignment.status.in_(ACTIVE_ASSIGNMENT_STATUSES)
            )
        ).all()
        active_incident_ids = {incident_id for incident_id, _ in active_assignments}
        active_team_ids = {team_id for _, team_id in active_assignments}

        capacity_teams = [
            team
            for team in available_teams
            if team.capacity >= ASSIGNMENT_WORKLOAD
        ]
        remaining_teams = [
            team for team in capacity_teams if team.id not in active_team_ids
        ]
        planned_assignments: list[tuple[Incident, RescueTeam, RouteResult]] = []
        unassigned: list[UnassignedIncidentDetail] = []

        for incident in incidents:
            if incident.id in active_incident_ids:
                reason = "Incident already has an active assignment."
            elif not available_teams:
                reason = "No rescue teams are currently available."
            elif not capacity_teams:
                reason = "No available team has capacity for one incident."
            elif not remaining_teams:
                reason = "All eligible teams already have an active or new assignment."
            else:
                route_candidates: list[tuple[RescueTeam, RouteResult]] = []
                for team in remaining_teams:
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
                    remaining_teams.remove(team)
                    continue

            unassigned.append(
                UnassignedIncidentDetail(
                    incident_id=incident.id,
                    title=incident.title,
                    reason=reason,
                )
            )

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
            team.status = "assigned"
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

        if not incidents:
            summary = "No eligible incidents were found."
        else:
            summary = (
                f"Allocated {len(assignment_details)} incident(s); "
                f"{len(unassigned)} incident(s) remain unassigned."
            )
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
