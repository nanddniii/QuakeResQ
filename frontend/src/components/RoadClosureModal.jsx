import { useState } from "react";
import { createRoadClosure, reopenRoad } from "../services/api.js";

const CLOSURE_PRESETS = [
  {
    name: "Richmond Road (Way 399001082)",
    start_node_id: "1637604213",
    end_node_id: "428831251",
    edge_id: "0",
    road_name: "Richmond Road",
    reason: "Severe road subsidence & earthquake fissure",
    latitude: 12.965,
    longitude: 77.602,
  },
  {
    name: "MG Road Segment",
    start_node_id: "4058224852",
    end_node_id: "1636623242",
    edge_id: "0",
    road_name: "Mahatma Gandhi Road",
    reason: "Fallen commercial signboard & debris obstruction",
    latitude: 12.974,
    longitude: 77.608,
  },
];

export default function RoadClosureModal({
  isOpen,
  onClose,
  closures = [],
  onUpdated,
}) {
  const [activeTab, setActiveTab] = useState("active"); // "active" or "new"
  const [startNodeId, setStartNodeId] = useState("");
  const [endNodeId, setEndNodeId] = useState("");
  const [edgeId, setEdgeId] = useState("0");
  const [roadName, setRoadName] = useState("");
  const [reason, setReason] = useState("");
  const [bothDirections, setBothDirections] = useState(false);
  const [latitude, setLatitude] = useState("");
  const [longitude, setLongitude] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [reopeningId, setReopeningId] = useState(null);
  const [bannerMessage, setBannerMessage] = useState(null);
  const [errorMessage, setErrorMessage] = useState(null);

  if (!isOpen) return null;

  function handleSelectPreset(preset) {
    setStartNodeId(preset.start_node_id);
    setEndNodeId(preset.end_node_id);
    setEdgeId(preset.edge_id);
    setRoadName(preset.road_name);
    setReason(preset.reason);
    setLatitude(preset.latitude ? String(preset.latitude) : "");
    setLongitude(preset.longitude ? String(preset.longitude) : "");
  }

  async function handleCloseRoad(e) {
    e.preventDefault();
    setIsSubmitting(true);
    setErrorMessage(null);
    setBannerMessage(null);

    try {
      if (!startNodeId.trim() || !endNodeId.trim()) {
        throw new Error("Start Node ID and End Node ID are required.");
      }
      if (!roadName.trim()) {
        throw new Error("Road name is required.");
      }

      const payload = {
        start_node_id: startNodeId.trim(),
        end_node_id: endNodeId.trim(),
        edge_id: edgeId.trim() || null,
        road_name: roadName.trim(),
        reason: reason.trim() || null,
        both_directions: Boolean(bothDirections),
        latitude: latitude ? Number(latitude) : null,
        longitude: longitude ? Number(longitude) : null,
      };

      const result = await createRoadClosure(payload);
      setBannerMessage(
        result.message || `Successfully closed road segment (Graph ${result.graph_version}).`
      );
      // Reset form
      setStartNodeId("");
      setEndNodeId("");
      setRoadName("");
      setReason("");
      setActiveTab("active");
      onUpdated?.();
    } catch (err) {
      setErrorMessage(err.message || "Failed to create road closure.");
    } finally {
      setIsSubmitting(false);
    }
  }

  async function handleReopen(closureId) {
    setReopeningId(closureId);
    setErrorMessage(null);
    setBannerMessage(null);

    try {
      const result = await reopenRoad({
        closure_id: closureId,
        both_directions: true,
      });
      setBannerMessage(
        result.message || `Successfully reopened road segment (Graph ${result.graph_version}).`
      );
      onUpdated?.();
    } catch (err) {
      setErrorMessage(err.message || "Failed to reopen road.");
    } finally {
      setReopeningId(null);
    }
  }

  const activeClosures = closures.filter((c) => c.status === "active");
  const clearedClosures = closures.filter((c) => c.status === "cleared");

  return (
    <div className="modal-backdrop" onClick={onClose} role="dialog" aria-modal="true">
      <div className="modal-dialog modal-dialog-lg" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div>
            <span className="eyebrow">ROAD NETWORK CONTROLS</span>
            <h2 className="modal-title">Manage Road Closures</h2>
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

        {/* Tab switcher */}
        <div className="modal-tabs">
          <button
            type="button"
            className={`tab-btn ${activeTab === "active" ? "active" : ""}`}
            onClick={() => setActiveTab("active")}
          >
            Active Closures ({activeClosures.length})
          </button>
          <button
            type="button"
            className={`tab-btn ${activeTab === "new" ? "active" : ""}`}
            onClick={() => setActiveTab("new")}
          >
            + Close Road Segment
          </button>
          <button
            type="button"
            className={`tab-btn ${activeTab === "history" ? "active" : ""}`}
            onClick={() => setActiveTab("history")}
          >
            History ({clearedClosures.length})
          </button>
        </div>

        <div className="modal-body">
          {bannerMessage && (
            <div className="success-banner" role="status">
              {bannerMessage}
            </div>
          )}
          {errorMessage && (
            <div className="form-error-banner" role="alert">
              {errorMessage}
            </div>
          )}

          {/* TAB 1: Active Closures */}
          {activeTab === "active" && (
            <div>
              {activeClosures.length === 0 ? (
                <div className="empty-state">
                  <p>All road segments are currently open and passable.</p>
                  <p className="empty-sub">
                    No active closures detected in the in-memory road graph.
                  </p>
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={() => setActiveTab("new")}
                    style={{ marginTop: "12px" }}
                  >
                    Simulate Road Closure
                  </button>
                </div>
              ) : (
                <div className="closure-list">
                  {activeClosures.map((c) => (
                    <article key={c.id} className="closure-card active-closure">
                      <div className="closure-info">
                        <div className="closure-header-row">
                          <span className="status-pill status-closed">Closed</span>
                          <h4 className="closure-road-name">{c.road_name}</h4>
                        </div>
                        <p className="closure-reason">
                          {c.reason || "Obstruction / structural damage."}
                        </p>
                        <div className="closure-meta">
                          <span>
                            <strong>Nodes:</strong> {c.start_node_id} &rarr; {c.end_node_id}
                          </span>
                          <span>
                            <strong>Edge:</strong> {c.edge_id}
                          </span>
                          <span>
                            <strong>Recorded:</strong>{" "}
                            {c.created_at ? new Date(c.created_at).toLocaleTimeString() : "N/A"}
                          </span>
                        </div>
                      </div>
                      <button
                        type="button"
                        className="btn-reopen"
                        onClick={() => handleReopen(c.id)}
                        disabled={reopeningId === c.id}
                      >
                        {reopeningId === c.id ? "Reopening..." : "Reopen Road"}
                      </button>
                    </article>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* TAB 2: New Road Closure Form */}
          {activeTab === "new" && (
            <form onSubmit={handleCloseRoad}>
              <div className="form-group">
                <label className="form-label">Demonstration Presets (Central Bengaluru)</label>
                <div className="preset-buttons">
                  {CLOSURE_PRESETS.map((p) => (
                    <button
                      key={p.name}
                      type="button"
                      className="preset-pill"
                      onClick={() => handleSelectPreset(p)}
                    >
                      {p.name}
                    </button>
                  ))}
                </div>
              </div>

              <div className="form-row form-row-2">
                <div className="form-group">
                  <label className="form-label" htmlFor="closure-start-node">
                    Start Node ID *
                  </label>
                  <input
                    id="closure-start-node"
                    className="form-input"
                    type="text"
                    required
                    placeholder="e.g. 1637604213"
                    value={startNodeId}
                    onChange={(e) => setStartNodeId(e.target.value)}
                  />
                </div>

                <div className="form-group">
                  <label className="form-label" htmlFor="closure-end-node">
                    End Node ID *
                  </label>
                  <input
                    id="closure-end-node"
                    className="form-input"
                    type="text"
                    required
                    placeholder="e.g. 428831251"
                    value={endNodeId}
                    onChange={(e) => setEndNodeId(e.target.value)}
                  />
                </div>
              </div>

              <div className="form-row form-row-2">
                <div className="form-group">
                  <label className="form-label" htmlFor="closure-road-name">
                    Road Name *
                  </label>
                  <input
                    id="closure-road-name"
                    className="form-input"
                    type="text"
                    required
                    placeholder="e.g., Richmond Road"
                    value={roadName}
                    onChange={(e) => setRoadName(e.target.value)}
                  />
                </div>

                <div className="form-group">
                  <label className="form-label" htmlFor="closure-edge-id">
                    Edge ID (Optional)
                  </label>
                  <input
                    id="closure-edge-id"
                    className="form-input"
                    type="text"
                    placeholder="e.g. 0"
                    value={edgeId}
                    onChange={(e) => setEdgeId(e.target.value)}
                  />
                </div>
              </div>

              <div className="form-group">
                <label className="form-label" htmlFor="closure-reason">
                  Closure Reason / Hazard
                </label>
                <input
                  id="closure-reason"
                  className="form-input"
                  type="text"
                  placeholder="e.g. Bridge collapse, power cable obstruction, deep fissure"
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                />
              </div>

              <div className="form-row form-row-2">
                <div className="form-group">
                  <label className="form-label" htmlFor="closure-lat">
                    Display Latitude (Optional)
                  </label>
                  <input
                    id="closure-lat"
                    className="form-input"
                    type="number"
                    step="0.0001"
                    placeholder="12.9650"
                    value={latitude}
                    onChange={(e) => setLatitude(e.target.value)}
                  />
                </div>

                <div className="form-group">
                  <label className="form-label" htmlFor="closure-lon">
                    Display Longitude (Optional)
                  </label>
                  <input
                    id="closure-lon"
                    className="form-input"
                    type="number"
                    step="0.0001"
                    placeholder="77.6020"
                    value={longitude}
                    onChange={(e) => setLongitude(e.target.value)}
                  />
                </div>
              </div>

              <div className="form-group">
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={bothDirections}
                    onChange={(e) => setBothDirections(e.target.checked)}
                  />
                  <span>Close both directions (bi-directional road)</span>
                </label>
              </div>

              <div className="modal-footer" style={{ padding: "16px 0 0 0" }}>
                <button
                  type="submit"
                  className="btn-danger"
                  disabled={isSubmitting}
                >
                  {isSubmitting ? "Applying Closure..." : "Inject Road Closure"}
                </button>
              </div>
            </form>
          )}

          {/* TAB 3: History */}
          {activeTab === "history" && (
            <div className="closure-list">
              {clearedClosures.length === 0 ? (
                <p className="empty-sub">No historical closures recorded.</p>
              ) : (
                clearedClosures.map((c) => (
                  <article key={c.id} className="closure-card cleared-closure">
                    <div className="closure-info">
                      <div className="closure-header-row">
                        <span className="status-pill status-available">Cleared</span>
                        <h4 className="closure-road-name">{c.road_name}</h4>
                      </div>
                      <p className="closure-reason">{c.reason || "Restored to service."}</p>
                      <div className="closure-meta">
                        <span>
                          Nodes: {c.start_node_id} &rarr; {c.end_node_id}
                        </span>
                      </div>
                    </div>
                  </article>
                ))
              )}
            </div>
          )}
        </div>

        <div className="modal-footer">
          <button type="button" className="btn-secondary" onClick={onClose}>
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
