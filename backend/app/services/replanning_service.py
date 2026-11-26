"""Dynamic replanning service with rule-based safe preemption and road-closure rerouting."""

from __future__ import annotations

import math
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Assignment, Incident, RescueTeam
from app.schemas.entities import (
    AllocationAssignmentDetail,
    AssignmentRequest,
    PlanningRunRequest,
    ReassignmentDetail,
    ReplanningResponse,
    RouteRecalculationDetail,
    UnassignedIncidentDetail,
)
from app.services.allocation_service import (
    ACTIVE_ASSIGNMENT_STATUSES,
    ASSIGNMENT_WORKLOAD,
    AllocationRoutingError,
)
from app.services.planning_persistence import PlanningPersistenceService
from app.services.routing_service import RouteResult, RoutingService

# Preemption criteria:
# 1. Target assignment status MUST NOT be protected (must be 'assigned' or 'pending', NEVER 'in_progress' or 'completed').
# 2. Priority of arriving incident must exceed target incident by at least MIN_PREEMPTION_DELTA.
PROTECTED_PREEMPTION_STATUSES = ("in_progress", "completed", "cancelled", "failed", "reassigned")
MIN_PREEMPTION_DELTA = 10.0


@contextmanager
def _transaction(session: Session) -> Iterator[None]:
    """Join caller-owned transactions, or commit this operation when standalone."""
    if session.in_transaction():
        yield
    else:
        with session.begin():
            yield


def recalculate_active_routes(
    session: Session, routing_service: RoutingService
) -> list[RouteRecalculationDetail]:
    """
    Evaluate all currently active assignments against the working road network graph.

    Detects routes severed by newly applied road closures or modified network conditions.
    If an active route is severed (no alternative path exists), cancels the assignment
    and returns the incident to 'reported' status for reassignment.
    """
    with _transaction(session):
        details: list[RouteRecalculationDetail] = []

        active_assignments = list(
            session.scalars(
                select(Assignment)
                .where(Assignment.status.in_(("pending", "assigned", "in_progress")))
                .order_by(Assignment.id.asc())
            ).all()
        )

        for assign in active_assignments:
            incident = session.get(Incident, assign.incident_id)
            team = session.get(RescueTeam, assign.rescue_team_id)

            if not incident or not team or team.latitude is None or incident.latitude is None:
                continue

            try:
                route: RouteResult = routing_service.find_route(
                    start_lat=team.latitude,
                    start_lon=team.longitude,
                    dest_lat=incident.latitude,
                    dest_lon=incident.longitude,
                    weight="travel_time",
                    algorithm="dijkstra",
                )
            except Exception as exc:
                route = RouteResult(
                    status="no_route",
                    message=f"Routing check failed: {exc}",
                )

            if route.status == "success":
                details.append(
                    RouteRecalculationDetail(
                        assignment_id=assign.id,
                        incident_id=incident.id,
                        incident_title=incident.title,
                        rescue_team_id=team.id,
                        rescue_team_name=team.name,
                        new_distance_km=route.total_distance_km,
                        new_time_minutes=route.estimated_time_minutes,
                        status="rerouted" if assign.status == "in_progress" else "unchanged",
                        message="Route is clear and verified on current working graph.",
                    )
                )
            else:
                # Route is severed by road closures!
                # Cancel the severed assignment and revert incident so it can be picked up by another team
                assign.status = "cancelled"
                if incident.status == "assigned":
                    incident.status = "reported"

                details.append(
                    RouteRecalculationDetail(
                        assignment_id=assign.id,
                        incident_id=incident.id,
                        incident_title=incident.title,
                        rescue_team_id=team.id,
                        rescue_team_name=team.name,
                        new_distance_km=None,
                        new_time_minutes=None,
                        status="severed",
                        message=f"Road closure severed route between Team {team.name} and {incident.title}. Assignment cancelled.",
                    )
                )

        session.flush()
        return details


def replan_incidents(
    session: Session,
    routing_service: RoutingService,
    allow_preemption: bool = True,
) -> ReplanningResponse:
    """
    Dynamic replanning workflow:
    1. Evaluates active assignments against working graph and identifies severed routes.
    2. Queries unassigned incidents (ordered by priority descending).
    3. Allocates available team capacity using Dijkstra routing.
    4. Evaluates safe, justified preemption for any remaining high-priority incidents
       when all teams are busy with lower-priority tasks.
    5. Never preempts 'in_progress' or 'completed' operations.
    6. Updates database records atomically and records an audit summary in PlanningRun.
    """
    with _transaction(session):
        # Step 1: Check active routes against road closures
        recalculated_routes = recalculate_active_routes(session, routing_service)

        # Step 2: Query active assignments (after closure adjustments)
        active_assignments = list(
            session.scalars(
                select(Assignment)
                .where(Assignment.status.in_(ACTIVE_ASSIGNMENT_STATUSES))
                .order_by(Assignment.id.asc())
                .with_for_update()
            ).all()
        )
        active_incident_ids = {a.incident_id for a in active_assignments}

        # Step 3: Query candidate teams (status in available or assigned)
        candidate_teams = list(
            session.scalars(
                select(RescueTeam)
                .where(RescueTeam.status.in_(("available", "assigned")))
                .order_by(RescueTeam.id.asc())
                .with_for_update()
            ).all()
        )

        team_workload: dict[int, int] = {}
        for a in active_assignments:
            team_workload[a.rescue_team_id] = team_workload.get(a.rescue_team_id, 0) + ASSIGNMENT_WORKLOAD

        team_remaining_capacity: dict[int, int] = {
            team.id: team.capacity - team_workload.get(team.id, 0)
            for team in candidate_teams
        }

        eligible_teams = [
            team for team in candidate_teams if team_remaining_capacity[team.id] >= ASSIGNMENT_WORKLOAD
        ]

        # Step 4: Query all unassigned incidents needing response
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

        pending_incidents = [
            inc for inc in all_incidents if inc.id not in active_incident_ids
        ]
        already_assigned_incidents = [
            inc for inc in all_incidents if inc.id in active_incident_ids
        ]

        persistence = PlanningPersistenceService(session)
        planning_run = persistence.create_planning_run(
            PlanningRunRequest(status="running")
        )

        new_assignments: list[AllocationAssignmentDetail] = []
        reassignments: list[ReassignmentDetail] = []
        unassigned: list[UnassignedIncidentDetail] = []

        # Step 5: Capacity-aware allocation for pending incidents
        unassigned_after_capacity: list[Incident] = []
        for incident in pending_incidents:
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
                except Exception:
                    continue

                if route.status == "success" and math.isfinite(route.total_distance_km):
                    route_candidates.append((team, route))

            if route_candidates:
                best_team, best_route = min(
                    route_candidates,
                    key=lambda c: (c[1].estimated_time_minutes, c[1].total_distance_km, c[0].id),
                )
                new_assign = persistence.create_assignment(
                    AssignmentRequest(
                        planning_id=planning_run.id,
                        incident_id=incident.id,
                        rescue_team_id=best_team.id,
                        status="assigned",
                    )
                )
                incident.status = "assigned"
                team_remaining_capacity[best_team.id] -= ASSIGNMENT_WORKLOAD
                if team_remaining_capacity[best_team.id] < ASSIGNMENT_WORKLOAD:
                    eligible_teams.remove(best_team)

                new_assignments.append(
                    AllocationAssignmentDetail(
                        assignment_id=new_assign.id,
                        incident_id=incident.id,
                        incident_title=incident.title,
                        rescue_team_id=best_team.id,
                        rescue_team_name=best_team.name,
                        status=new_assign.status,
                        priority_score=incident.priority_score,
                        priority_level=incident.priority_level,
                        road_distance_km=best_route.total_distance_km,
                        estimated_time_minutes=best_route.estimated_time_minutes,
                        is_reassignment=False,
                    )
                )
            else:
                unassigned_after_capacity.append(incident)

        # Step 6: Justified Preemption for remaining high-priority incidents
        for incident in unassigned_after_capacity:
            preempted = False
            inc_score = incident.priority_score or 0.0

            if allow_preemption and inc_score >= 60.0:
                # Find current active assignments that are NOT in-progress and have lower priority
                preemption_candidates = []

                # Refresh active assignments list for current state
                current_active_assignments = list(
                    session.scalars(
                        select(Assignment)
                        .where(Assignment.status.in_(("assigned", "pending")))
                        .order_by(Assignment.id.asc())
                    ).all()
                )

                for active_assign in current_active_assignments:
                    # Protection 1: Never preempt an assignment created in this current planning run
                    if active_assign.planning_id == planning_run.id:
                        continue

                    # Protection 2: Never preempt in_progress or completed operations
                    if active_assign.status in PROTECTED_PREEMPTION_STATUSES:
                        continue

                    target_inc = session.get(Incident, active_assign.incident_id)
                    target_team = session.get(RescueTeam, active_assign.rescue_team_id)
                    if not target_inc or not target_team:
                        continue

                    target_score = target_inc.priority_score or 0.0
                    priority_delta = inc_score - target_score

                    # Protection 3: Justification rule: Priority delta must be >= MIN_PREEMPTION_DELTA
                    if priority_delta < MIN_PREEMPTION_DELTA:
                        continue

                    # Protection 4: Route feasibility: Target team must be able to reach new incident
                    try:
                        p_route = routing_service.find_route(
                            start_lat=target_team.latitude,
                            start_lon=target_team.longitude,
                            dest_lat=incident.latitude,
                            dest_lon=incident.longitude,
                            weight="travel_time",
                            algorithm="dijkstra",
                        )
                    except Exception:
                        continue

                    if p_route.status == "success" and math.isfinite(p_route.total_distance_km):
                        preemption_candidates.append(
                            (active_assign, target_inc, target_team, p_route, target_score)
                        )

                if preemption_candidates:
                    # Pick candidate with lowest target priority score and fastest travel time
                    best_candidate = min(
                        preemption_candidates,
                        key=lambda c: (c[4], c[3].estimated_time_minutes, c[2].id),
                    )
                    act_assign, old_inc, assigned_team, p_route, _ = best_candidate

                    # 1. Update old assignment status to 'reassigned'
                    act_assign.status = "reassigned"
                    old_inc.status = "reported"

                    # 2. Create new assignment for critical incident
                    new_assign = persistence.create_assignment(
                        AssignmentRequest(
                            planning_id=planning_run.id,
                            incident_id=incident.id,
                            rescue_team_id=assigned_team.id,
                            status="assigned",
                        )
                    )
                    incident.status = "assigned"

                    # 3. Record audit trail in reassignments and new assignments
                    justification_msg = (
                        f"Priority upgrade: Incident #{incident.id} ({inc_score:.1f}) preempted "
                        f"lower-priority Incident #{old_inc.id} ({old_inc.priority_score:.1f}). "
                        f"Estimated transit: {p_route.estimated_time_minutes:.1f} min."
                    )

                    reassignments.append(
                        ReassignmentDetail(
                            preempted_assignment_id=act_assign.id,
                            preempted_incident_id=old_inc.id,
                            preempted_incident_title=old_inc.title,
                            preempted_priority_score=old_inc.priority_score,
                            new_assignment_id=new_assign.id,
                            new_incident_id=incident.id,
                            new_incident_title=incident.title,
                            new_priority_score=incident.priority_score,
                            rescue_team_id=assigned_team.id,
                            rescue_team_name=assigned_team.name,
                            justification=justification_msg,
                        )
                    )

                    new_assignments.append(
                        AllocationAssignmentDetail(
                            assignment_id=new_assign.id,
                            incident_id=incident.id,
                            incident_title=incident.title,
                            rescue_team_id=assigned_team.id,
                            rescue_team_name=assigned_team.name,
                            status=new_assign.status,
                            priority_score=incident.priority_score,
                            priority_level=incident.priority_level,
                            road_distance_km=p_route.total_distance_km,
                            estimated_time_minutes=p_route.estimated_time_minutes,
                            is_reassignment=True,
                        )
                    )
                    preempted = True

            if not preempted:
                # Could not allocate or preempt
                reason = "No reachable team with capacity or justified preemption available."
                unassigned.append(
                    UnassignedIncidentDetail(
                        incident_id=incident.id,
                        title=incident.title,
                        reason=reason,
                    )
                )

        # Step 7: Update team statuses based on final workload
        for team in candidate_teams:
            # Count active assignments
            active_cnt = session.scalar(
                select(func.count(Assignment.id)).where(
                    Assignment.rescue_team_id == team.id,
                    Assignment.status.in_(ACTIVE_ASSIGNMENT_STATUSES),
                )
            ) or 0
            if active_cnt >= team.capacity:
                team.status = "assigned"
            else:
                team.status = "available"

        # Step 8: Build summary
        summary_parts = [
            f"Replanning completed: {len(new_assignments)} assignment(s) created",
            f"{len(reassignments)} reassignment(s) justified",
            f"{len(recalculated_routes)} route(s) evaluated",
            f"{len(unassigned)} incident(s) remain unassigned",
        ]
        summary = "; ".join(summary_parts) + "."

        persistence.update_planning_run(
            planning_run.id,
            PlanningRunRequest(
                status="completed",
                completed_at=datetime.now(timezone.utc),
                summary=summary,
            ),
        )
        session.flush()

        return ReplanningResponse(
            planning_run_id=planning_run.id,
            status="completed",
            new_assignments=new_assignments,
            reassignments=reassignments,
            recalculated_routes=recalculated_routes,
            unassigned_incidents=unassigned,
            summary=summary,
        )
