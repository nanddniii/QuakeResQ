import { useState } from "react";
import { calculateIncidentPriority } from "../services/api.js";

function getPriorityBadgeClass(level) {
  if (level === "Critical") return "badge-critical";
  if (level === "High") return "badge-high";
  if (level === "Medium") return "badge-medium";
  return "badge-low";
}

export default function IncidentDetailsModal({
  incident,
  onClose,
  onFocusMap,
  onUpdated,
  assignedTeam = null,
  routeDetail = null,
}) {
  const [isRecalculating, setIsRecalculating] = useState(false);
  const [actionMessage, setActionMessage] = useState(null);

  if (!incident) return null;

  async function handleRecalculate() {
    setIsRecalculating(true);
    setActionMessage(null);
    try {
      const updated = await calculateIncidentPriority(incident.id);
      setActionMessage(`Priority updated to ${updated.priority_score} (${updated.priority_level})`);
      onUpdated?.(updated);
    } catch (err) {
      setActionMessage(`Error: ${err.message}`);
    } finally {
      setIsRecalculating(false);
    }
  }

  const priorityScore = incident.priority_score != null ? incident.priority_score.toFixed(1) : "Not scored";
  const priorityLevel = incident.priority_level || "Pending";

  return (
    <div className="modal-backdrop" onClick={onClose} role="dialog" aria-modal="true">
      <div className="modal-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div>
            <span className="eyebrow">INCIDENT #{incident.id}</span>
            <h2 className="modal-title">{incident.title}</h2>
          </div>
          <button
            type="button"
            className="modal-close"
            onClick={onClose}
            aria-label="Close dialog"
          >
            &times;
          </button>
        </div>

        <div className="modal-body">
          {actionMessage && (
            <div className="info-banner" role="status">
              {actionMessage}
            </div>
          )}

          {/* Priority Score Summary Box */}
          <div className="priority-summary-box">
            <div className="priority-header-row">
              <div>
                <span className="priority-label">Priority Assessment</span>
                <div className="priority-value-row">
                  <span className="priority-large-score">{priorityScore}</span>
                  <span className={`priority-badge ${getPriorityBadgeClass(priorityLevel)}`}>
                    {priorityLevel}
                  </span>
                </div>
              </div>
              <button
                type="button"
                className="btn-secondary"
                onClick={handleRecalculate}
                disabled={isRecalculating}
              >
                {isRecalculating ? "Calculating..." : "Recalculate Priority"}
              </button>
            </div>

            <div className="priority-factors-grid">
              <div className="factor-item">
                <span className="factor-name">Severity</span>
                <span className="factor-val">{incident.severity ?? "N/A"}/5</span>
                <span className="factor-weight">(50% weight)</span>
              </div>
              <div className="factor-item">
                <span className="factor-name">Urgency</span>
                <span className="factor-val">{incident.urgency ?? "N/A"}/5</span>
                <span className="factor-weight">(30% weight)</span>
              </div>
              <div className="factor-item">
                <span className="factor-name">Evidence</span>
                <span className="factor-val">{incident.evidence_strength ?? "N/A"}/5</span>
                <span className="factor-weight">(20% weight)</span>
              </div>
            </div>
          </div>

          {/* Operational Details */}
          <div className="details-section">
            <h3 className="section-title">Operational Status</h3>
            <div className="details-grid">
              <div className="detail-row">
                <span className="detail-key">Current Status:</span>
                <span className={`status-pill status-${incident.status}`}>
                  {incident.status}
                </span>
              </div>
              <div className="detail-row">
                <span className="detail-key">People Affected:</span>
                <span className="detail-val font-semibold">{incident.people_affected ?? 0}</span>
              </div>
              <div className="detail-row">
                <span className="detail-key">Coordinates:</span>
                <span className="detail-val">
                  {Number(incident.latitude).toFixed(4)}, {Number(incident.longitude).toFixed(4)}
                </span>
              </div>
              <div className="detail-row">
                <span className="detail-key">Reported At:</span>
                <span className="detail-val">
                  {incident.created_at ? new Date(incident.created_at).toLocaleString() : "N/A"}
                </span>
              </div>
            </div>
          </div>

          {/* Incident Description */}
          {incident.description && (
            <div className="details-section">
              <h3 className="section-title">Description & Notes</h3>
              <p className="description-text">{incident.description}</p>
            </div>
          )}

          {/* Assigned Rescue Team Details */}
          {assignedTeam && (
            <div className="details-section team-assigned-section">
              <h3 className="section-title">Assigned Rescue Unit</h3>
              <div className="assigned-team-card">
                <div className="assigned-team-info">
                  <h4 className="assigned-team-name">{assignedTeam.name}</h4>
                  <p className="assigned-team-type">{assignedTeam.team_type}</p>
                  <div className="assigned-team-meta">
                    <span>Capacity: {assignedTeam.capacity}</span>
                    <span>Members: {assignedTeam.members_count ?? "N/A"}</span>
                  </div>
                </div>
                {routeDetail && (
                  <div className="assigned-route-meta">
                    <span className="route-stat">
                      <strong>Distance:</strong> {routeDetail.distanceKm.toFixed(2)} km
                    </span>
                    <span className="route-stat">
                      <strong>ETA:</strong> {routeDetail.timeMinutes.toFixed(1)} min
                    </span>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>

        <div className="modal-footer">
          <button
            type="button"
            className="btn-secondary"
            onClick={() => {
              onFocusMap?.(incident.latitude, incident.longitude);
              onClose();
            }}
          >
            Locate on Map
          </button>
          <button type="button" className="btn-primary" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
