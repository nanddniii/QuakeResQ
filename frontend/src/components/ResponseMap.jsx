import { useEffect } from "react";
import L from "leaflet";
import {
  CircleMarker,
  MapContainer,
  Marker,
  Polyline,
  Popup,
  TileLayer,
  useMap,
} from "react-leaflet";

// Center on Bengaluru Central
const BENGALURU_CENTER = [12.9716, 77.5946];

function getEarthquakeMarkerStyle(mag) {
  if (mag >= 6.0) {
    return {
      radius: Math.min(26, 16 + (mag - 6.0) * 4),
      color: "#991b1b",
      fillColor: "#dc2626",
      fillOpacity: 0.6,
      weight: 2.5,
    };
  }
  if (mag >= 4.5) {
    return {
      radius: Math.min(18, 12 + (mag - 4.5) * 3),
      color: "#c2410c",
      fillColor: "#ea580c",
      fillOpacity: 0.5,
      weight: 2,
    };
  }
  if (mag >= 3.0) {
    return {
      radius: Math.min(12, 8 + (mag - 3.0) * 2),
      color: "#b45309",
      fillColor: "#f59e0b",
      fillOpacity: 0.45,
      weight: 1.8,
    };
  }
  return {
    radius: Math.max(5, Math.min(8, 5 + mag)),
    color: "#0369a1",
    fillColor: "#0ea5e9",
    fillOpacity: 0.4,
    weight: 1.5,
  };
}

/**
 * Helper component to pan/zoom map programmatically when target location or region changes.
 */
function MapController({ targetLocation, targetRegion }) {
  const map = useMap();
  useEffect(() => {
    if (targetLocation && targetLocation.lat && targetLocation.lon) {
      map.setView([targetLocation.lat, targetLocation.lon], 15, {
        animate: true,
      });
    } else if (targetRegion === "india") {
      map.setView([22.5, 79.5], 5, { animate: true });
    } else if (targetRegion === "worldwide") {
      map.setView([20.0, 10.0], 2, { animate: true });
    } else if (targetRegion === "bengaluru") {
      map.setView(BENGALURU_CENTER, 13, { animate: true });
    }
  }, [targetLocation, targetRegion, map]);
  return null;
}

/**
 * Generate custom HTML div icons for Leaflet markers.
 */
function createMarkerIcon({ color, label, iconText, size = 30 }) {
  return L.divIcon({
    className: "custom-leaflet-marker",
    html: `
      <div style="
        background: ${color};
        color: #ffffff;
        width: ${size}px;
        height: ${size}px;
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        font-family: 'DM Sans', sans-serif;
        font-weight: 700;
        font-size: ${size > 26 ? 12 : 10}px;
        border: 2.5px solid #ffffff;
        box-shadow: 0 3px 8px rgba(0,0,0,0.35);
        cursor: pointer;
        position: relative;
      ">
        <span>${iconText || label || ""}</span>
      </div>
    `,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
    popupAnchor: [0, -size / 2],
  });
}

function getPriorityColor(level, score) {
  if (level === "Critical" || (score !== null && score >= 80)) return "#e5503d";
  if (level === "High" || (score !== null && score >= 60)) return "#f39c12";
  if (level === "Medium" || (score !== null && score >= 40)) return "#e67e22";
  return "#27ae60";
}

function getTeamColor(status) {
  if (status === "available") return "#10b981"; // green
  if (status === "assigned" || status === "deployed") return "#2563eb"; // blue
  return "#6b7280"; // gray
}

export default function ResponseMap({
  earthquakes = [],
  incidents = [],
  rescueTeams = [],
  roadClosures = [],
  routes = [],
  selectedLocation = null,
  targetRegion = null,
  onSelectIncident,
  onReopenRoad,
  layerVisibility = {
    earthquakes: true,
    incidents: true,
    teams: true,
    closures: true,
    routes: true,
  },
}) {
  return (
    <div className="map-wrapper" style={{ height: "560px", width: "100%", position: "relative" }}>
      <MapContainer
        center={BENGALURU_CENTER}
        zoom={13}
        scrollWheelZoom={true}
        style={{ height: "100%", width: "100%", borderRadius: "12px" }}
      >
        <MapController targetLocation={selectedLocation} targetRegion={targetRegion} />

        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          maxZoom={19}
        />

        {/* 1. Earthquakes Layer */}
        {layerVisibility.earthquakes &&
          earthquakes.map((eq) => {
            const coords = eq.geometry?.coordinates;
            if (!coords || coords.length < 2) return null;
            // GeoJSON coordinates are [longitude, latitude]
            const lon = Number(coords[0]);
            const lat = Number(coords[1]);
            const mag = Number(eq.properties?.magnitude ?? eq.properties?.mag ?? 0);
            const place = eq.properties?.location_name ?? eq.properties?.place ?? "Unknown location";
            const depthKm = eq.properties?.depth_km ?? (coords.length > 2 ? coords[2] : null);
            const url = eq.properties?.url;
            const timeRaw = eq.properties?.occurred_at ?? eq.properties?.time;
            const timeFormatted = timeRaw
              ? new Date(timeRaw).toLocaleString()
              : "N/A";

            const style = getEarthquakeMarkerStyle(mag);

            return (
              <CircleMarker
                key={`eq-${eq.id || `${lat}-${lon}`}`}
                center={[lat, lon]}
                radius={style.radius}
                pathOptions={{
                  color: style.color,
                  fillColor: style.fillColor,
                  fillOpacity: style.fillOpacity,
                  weight: style.weight,
                }}
              >
                <Popup>
                  <div className="popup-card">
                    <span
                      className="popup-tag earthquake-tag"
                      style={{
                        background: style.fillColor,
                        color: "#ffffff",
                        padding: "3px 8px",
                        borderRadius: "4px",
                        fontSize: "0.75rem",
                        fontWeight: 700,
                        textTransform: "uppercase",
                      }}
                    >
                      USGS Seismic Event
                    </span>
                    <h3 className="popup-title" style={{ margin: "6px 0 2px", fontSize: "1.15rem" }}>
                      Magnitude {mag.toFixed(1)}
                    </h3>
                    <p className="popup-desc" style={{ color: "#374151", margin: "4px 0" }}>
                      {place}
                    </p>
                    <div className="popup-meta" style={{ display: "grid", gap: "3px", fontSize: "0.85rem", marginTop: "8px" }}>
                      <div>
                        <strong>Depth:</strong> {depthKm != null ? `${depthKm} km` : "N/A"}
                      </div>
                      <div>
                        <strong>Occurred:</strong> {timeFormatted}
                      </div>
                      <div>
                        <strong>Coordinates:</strong> {lat.toFixed(4)}°N, {lon.toFixed(4)}°E
                      </div>
                      {url && (
                        <div style={{ marginTop: "6px", paddingTop: "6px", borderTop: "1px solid #e5e7eb" }}>
                          <a
                            href={url}
                            target="_blank"
                            rel="noopener noreferrer"
                            style={{
                              color: "#2563eb",
                              textDecoration: "underline",
                              fontWeight: 600,
                              fontSize: "0.85rem",
                            }}
                          >
                            Official USGS Event Page &rarr;
                          </a>
                        </div>
                      )}
                    </div>
                  </div>
                </Popup>
              </CircleMarker>
            );
          })}

        {/* 2. Incidents Layer */}
        {layerVisibility.incidents &&
          incidents.map((incident) => {
            if (incident.latitude == null || incident.longitude == null) return null;
            const lat = Number(incident.latitude);
            const lon = Number(incident.longitude);
            const color = getPriorityColor(incident.priority_level, incident.priority_score);
            const scoreLabel = incident.priority_score != null ? Math.round(incident.priority_score) : "!";
            const icon = createMarkerIcon({
              color,
              iconText: scoreLabel,
              size: 32,
            });

            return (
              <Marker
                key={`inc-${incident.id}`}
                position={[lat, lon]}
                icon={icon}
              >
                <Popup>
                  <div className="popup-card">
                    <span
                      className="popup-tag"
                      style={{ backgroundColor: `${color}20`, color: color }}
                    >
                      {incident.priority_level || "Reported"} Priority ({incident.priority_score ?? "N/A"})
                    </span>
                    <h3 className="popup-title">{incident.title}</h3>
                    {incident.description && (
                      <p className="popup-desc">{incident.description}</p>
                    )}
                    <div className="popup-meta">
                      <div>
                        <strong>Status:</strong>{" "}
                        <span className={`status-pill status-${incident.status}`}>
                          {incident.status}
                        </span>
                      </div>
                      <div>
                        <strong>Severity:</strong> {incident.severity ?? "N/A"}/5
                      </div>
                      <div>
                        <strong>Urgency:</strong> {incident.urgency ?? "N/A"}/5
                      </div>
                      <div>
                        <strong>People Affected:</strong> {incident.people_affected ?? 0}
                      </div>
                    </div>
                    {onSelectIncident && (
                      <button
                        type="button"
                        className="popup-btn"
                        onClick={() => onSelectIncident(incident)}
                      >
                        Inspect Details
                      </button>
                    )}
                  </div>
                </Popup>
              </Marker>
            );
          })}

        {/* 3. Rescue Teams Layer */}
        {layerVisibility.teams &&
          rescueTeams.map((team) => {
            if (team.latitude == null || team.longitude == null) return null;
            const lat = Number(team.latitude);
            const lon = Number(team.longitude);
            const color = getTeamColor(team.status);
            const icon = createMarkerIcon({
              color,
              iconText: "TEAM",
              size: 28,
            });

            return (
              <Marker
                key={`team-${team.id}`}
                position={[lat, lon]}
                icon={icon}
              >
                <Popup>
                  <div className="popup-card">
                    <div style={{ display: "flex", gap: "6px", alignItems: "center", marginBottom: "4px" }}>
                      <span
                        className="popup-tag"
                        style={{ backgroundColor: `${color}20`, color: color }}
                      >
                        {team.status.toUpperCase()}
                      </span>
                      <span
                        style={{
                          fontSize: "10px",
                          fontWeight: 700,
                          background: "#f1f5f9",
                          color: "#64748b",
                          padding: "2px 6px",
                          borderRadius: "4px",
                          border: "1px solid #e2e8f0",
                          textTransform: "uppercase",
                        }}
                      >
                        Demo Unit (Simulated GPS)
                      </span>
                    </div>
                    <h3 className="popup-title">{team.name}</h3>
                    <p className="popup-desc">{team.team_type}</p>
                    <div className="popup-meta">
                      <div>
                        <strong>Remaining Capacity:</strong>{" "}
                        <span
                          style={{
                            fontWeight: 700,
                            color:
                              (team.remaining_capacity ?? team.capacity) > 0 ? "#10b981" : "#ef4444",
                          }}
                        >
                          {team.remaining_capacity ?? team.capacity} / {team.capacity} missions
                        </span>
                      </div>
                      <div>
                        <strong>Active Assignments:</strong>{" "}
                        {team.active_assignments_count ?? (team.status === "assigned" ? 1 : 0)} active mission(s)
                      </div>
                      <div>
                        <strong>Personnel:</strong> {team.members_count ?? "N/A"} members
                      </div>
                      <div>
                        <strong>Base Coordinates:</strong> {lat.toFixed(4)}°N, {lon.toFixed(4)}°E
                      </div>
                    </div>
                  </div>
                </Popup>
              </Marker>
            );
          })}

        {/* 4. Active Road Closures Layer */}
        {layerVisibility.closures &&
          roadClosures
            .filter((c) => c.status === "active")
            .map((closure) => {
              // coordinates in RoadClosureItemResponse: [longitude, latitude]
              let lat = closure.coordinates ? closure.coordinates[1] : null;
              let lon = closure.coordinates ? closure.coordinates[0] : null;

              // Fallback to center if coordinates not recorded
              if (lat == null || lon == null) {
                lat = 12.9716;
                lon = 77.5946;
              }

              const icon = createMarkerIcon({
                color: "#b91c1c",
                iconText: "X",
                size: 26,
              });

              return (
                <Marker
                  key={`closure-${closure.id}`}
                  position={[lat, lon]}
                  icon={icon}
                >
                  <Popup>
                    <div className="popup-card">
                      <div style={{ display: "flex", gap: "6px", alignItems: "center", marginBottom: "4px" }}>
                        <span className="popup-tag closure-tag" style={{ background: "#fee2e2", color: "#991b1b" }}>
                          Active Road Closure
                        </span>
                        <span
                          style={{
                            fontSize: "10px",
                            fontWeight: 700,
                            background: "#fff7ed",
                            color: "#c2410c",
                            padding: "2px 6px",
                            borderRadius: "4px",
                            border: "1px solid #ffedd5",
                            textTransform: "uppercase",
                          }}
                        >
                          Operator-Simulated Hazard
                        </span>
                      </div>
                      <h3 className="popup-title">{closure.road_name}</h3>
                      <p className="popup-desc">
                        {closure.reason || "Reported road obstacle / collapse debris."}
                      </p>
                      <div className="popup-meta">
                        <div>
                          <strong>Nodes:</strong> {closure.start_node_id} &rarr; {closure.end_node_id}
                        </div>
                        <div>
                          <strong>Edge ID:</strong> {closure.edge_id}
                        </div>
                        <div>
                          <strong>Closure ID:</strong> #{closure.id}
                        </div>
                      </div>
                      {onReopenRoad && (
                        <button
                          type="button"
                          className="popup-btn reopen-btn"
                          onClick={() => onReopenRoad(closure.id)}
                        >
                          Reopen This Road
                        </button>
                      )}
                    </div>
                  </Popup>
                </Marker>
              );
            })}

        {/* 5. Assignment Route Polylines */}
        {layerVisibility.routes &&
          routes.map((route, idx) => {
            if (!route.coordinates || route.coordinates.length < 2) return null;
            return (
              <Polyline
                key={`route-${route.assignmentId || idx}`}
                positions={route.coordinates}
                pathOptions={{
                  color: "#2563eb",
                  weight: 5,
                  opacity: 0.85,
                  lineCap: "round",
                  lineJoin: "round",
                }}
              >
                <Popup>
                  <div className="popup-card">
                    <span className="popup-tag route-tag">Dijkstra Rescue Route</span>
                    <h3 className="popup-title">
                      {route.teamName} &rarr; {route.incidentTitle}
                    </h3>
                    <div className="popup-meta">
                      <div>
                        <strong>Distance:</strong> {route.distanceKm.toFixed(2)} km
                      </div>
                      <div>
                        <strong>Estimated Time:</strong> {route.timeMinutes.toFixed(1)} min
                      </div>
                      {route.travelTimeSource && (
                        <div>
                          <strong>Speed Source:</strong> {route.travelTimeSource}
                        </div>
                      )}
                    </div>
                  </div>
                </Popup>
              </Polyline>
            );
          })}
      </MapContainer>
    </div>
  );
}
