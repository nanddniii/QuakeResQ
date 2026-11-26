import { useState } from "react";
import { calculateIncidentPriority, createIncident } from "../services/api.js";

const BENGALURU_PRESETS = [
  { name: "Central (MG Road)", lat: 12.975, lon: 77.605 },
  { name: "Cubbon Park / Vidhana Soudha", lat: 12.978, lon: 77.591 },
  { name: "Richmond Town", lat: 12.965, lon: 77.602 },
  { name: "Koramangala", lat: 12.935, lon: 77.624 },
  { name: "Shivajinagar", lat: 12.986, lon: 77.602 },
];

export default function IncidentFormModal({
  isOpen,
  onClose,
  onSuccess,
  earthquakes = [],
}) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [severity, setSeverity] = useState(4);
  const [urgency, setUrgency] = useState(4);
  const [evidenceStrength, setEvidenceStrength] = useState(3);
  const [peopleAffected, setPeopleAffected] = useState(15);
  const [latitude, setLatitude] = useState(12.975);
  const [longitude, setLongitude] = useState(77.605);
  const [earthquakeId, setEarthquakeId] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [formError, setFormError] = useState(null);

  if (!isOpen) return null;

  function handlePresetSelect(preset) {
    setLatitude(preset.lat);
    setLongitude(preset.lon);
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setFormError(null);
    setIsSubmitting(true);

    try {
      if (!title.trim()) {
        throw new Error("Incident title is required.");
      }

      const payload = {
        title: title.trim(),
        description: description.trim() || null,
        severity: Number(severity),
        urgency: Number(urgency),
        evidence_strength: Number(evidenceStrength),
        people_affected: Number(peopleAffected),
        latitude: Number(latitude),
        longitude: Number(longitude),
        status: "reported",
        earthquake_id: earthquakeId ? Number(earthquakeId) : null,
      };

      // 1. Create Incident
      const created = await createIncident(payload);

      // 2. Automatically compute priority score and level
      if (created && created.id) {
        try {
          await calculateIncidentPriority(created.id);
        } catch {
          // Priority calculation can also be done later
        }
      }

      // Reset form
      setTitle("");
      setDescription("");
      onSuccess?.();
      onClose();
    } catch (err) {
      setFormError(err.message || "Failed to create incident.");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose} role="dialog" aria-modal="true">
      <div className="modal-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div>
            <span className="eyebrow">NEW EMERGENCY REPORT</span>
            <h2 className="modal-title">Report Rescue Incident</h2>
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

        <form onSubmit={handleSubmit} className="modal-body">
          {formError && (
            <div className="form-error-banner" role="alert">
              {formError}
            </div>
          )}

          <div className="form-group">
            <label className="form-label" htmlFor="incident-title">
              Incident Title *
            </label>
            <input
              id="incident-title"
              className="form-input"
              type="text"
              required
              placeholder="e.g., Collapsed Residential Complex at Brigade Road"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          </div>

          <div className="form-group">
            <label className="form-label" htmlFor="incident-desc">
              Description
            </label>
            <textarea
              id="incident-desc"
              className="form-textarea"
              rows={2}
              placeholder="Provide situational details, structural failure description, trapped persons..."
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>

          <div className="form-row form-row-3">
            <div className="form-group">
              <label className="form-label" htmlFor="incident-severity">
                Severity (1-5) *
              </label>
              <select
                id="incident-severity"
                className="form-select"
                value={severity}
                onChange={(e) => setSeverity(Number(e.target.value))}
              >
                <option value={5}>5 - Catastrophic</option>
                <option value={4}>4 - Severe Damage</option>
                <option value={3}>3 - Moderate Damage</option>
                <option value={2}>2 - Minor Damage</option>
                <option value={1}>1 - Superficial</option>
              </select>
            </div>

            <div className="form-group">
              <label className="form-label" htmlFor="incident-urgency">
                Urgency (1-5) *
              </label>
              <select
                id="incident-urgency"
                className="form-select"
                value={urgency}
                onChange={(e) => setUrgency(Number(e.target.value))}
              >
                <option value={5}>5 - Immediate (Trapped)</option>
                <option value={4}>4 - High Urgency</option>
                <option value={3}>3 - Moderate Urgency</option>
                <option value={2}>2 - Low Urgency</option>
                <option value={1}>1 - Delayed</option>
              </select>
            </div>

            <div className="form-group">
              <label className="form-label" htmlFor="incident-evidence">
                Evidence (1-5) *
              </label>
              <select
                id="incident-evidence"
                className="form-select"
                value={evidenceStrength}
                onChange={(e) => setEvidenceStrength(Number(e.target.value))}
              >
                <option value={5}>5 - Confirmed by Responders</option>
                <option value={4}>4 - Multiple Sensor/Video</option>
                <option value={3}>3 - Eyewitness Reports</option>
                <option value={2}>2 - Single Citizen Call</option>
                <option value={1}>1 - Unverified Rumor</option>
              </select>
            </div>
          </div>

          <div className="form-row form-row-2">
            <div className="form-group">
              <label className="form-label" htmlFor="incident-people">
                People Affected / Trapped *
              </label>
              <input
                id="incident-people"
                className="form-input"
                type="number"
                min={0}
                required
                value={peopleAffected}
                onChange={(e) => setPeopleAffected(Number(e.target.value))}
              />
            </div>

            <div className="form-group">
              <label className="form-label" htmlFor="incident-earthquake">
                Linked Earthquake (Optional)
              </label>
              <select
                id="incident-earthquake"
                className="form-select"
                value={earthquakeId}
                onChange={(e) => setEarthquakeId(e.target.value)}
              >
                <option value="">None / Unlinked</option>
                {earthquakes.slice(0, 10).map((eq) => (
                  <option key={eq.id} value={eq.id}>
                    M {eq.properties?.mag?.toFixed(1) ?? "Event"} - {eq.properties?.place?.slice(0, 30) ?? `ID ${eq.id}`}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="form-group">
            <label className="form-label">Location Presets (Bengaluru)</label>
            <div className="preset-buttons">
              {BENGALURU_PRESETS.map((p) => (
                <button
                  key={p.name}
                  type="button"
                  className="preset-pill"
                  onClick={() => handlePresetSelect(p)}
                >
                  {p.name}
                </button>
              ))}
            </div>
          </div>

          <div className="form-row form-row-2">
            <div className="form-group">
              <label className="form-label" htmlFor="incident-lat">
                Latitude *
              </label>
              <input
                id="incident-lat"
                className="form-input"
                type="number"
                step="0.0001"
                required
                value={latitude}
                onChange={(e) => setLatitude(Number(e.target.value))}
              />
            </div>

            <div className="form-group">
              <label className="form-label" htmlFor="incident-lon">
                Longitude *
              </label>
              <input
                id="incident-lon"
                className="form-input"
                type="number"
                step="0.0001"
                required
                value={longitude}
                onChange={(e) => setLongitude(Number(e.target.value))}
              />
            </div>
          </div>

          <div className="modal-footer">
            <button
              type="button"
              className="btn-secondary"
              onClick={onClose}
              disabled={isSubmitting}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="btn-primary"
              disabled={isSubmitting}
            >
              {isSubmitting ? "Submitting & Scoring..." : "Submit Incident"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
