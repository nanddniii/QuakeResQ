import { useCallback, useEffect, useState } from "react";

import DashboardHeader from "../components/DashboardHeader.jsx";
import IncidentDetailsModal from "../components/IncidentDetailsModal.jsx";
import IncidentFormModal from "../components/IncidentFormModal.jsx";
import MetricsOverview from "../components/MetricsOverview.jsx";
import ResponseMap from "../components/ResponseMap.jsx";
import RoadClosureModal from "../components/RoadClosureModal.jsx";
import {
  allocateResources,
  calculateRoute,
  checkApiHealth,
  getEarthquakes,
  getIncidentRankings,
  getIncidents,
  getRescueTeams,
  getRoadClosures,
  getRoadGraphStatus,
  reopenRoad,
  triggerReplanning,
} from "../services/api.js";

export default function Dashboard() {
  // Operational Data States
  const [incidents, setIncidents] = useState([]);
  const [rescueTeams, setRescueTeams] = useState([]);
  const [roadClosures, setRoadClosures] = useState([]);
  const [earthquakes, setEarthquakes] = useState([]);
  const [graphStatus, setGraphStatus] = useState(null);
  const [routes, setRoutes] = useState([]);

  // Real-time USGS Earthquake Feed States
  const [earthquakeFeed, setEarthquakeFeed] = useState("all_day"); // "all_day" | "all_week"
  const [earthquakeRegion, setEarthquakeRegion] = useState("worldwide"); // "worldwide" | "india"
  const [earthquakeMinMag, setEarthquakeMinMag] = useState(2.5); // 0.0 | 2.5 | 4.5 | 6.0
  const [lastEarthquakeRefresh, setLastEarthquakeRefresh] = useState(null);
  const [isEarthquakeRefreshing, setIsEarthquakeRefreshing] = useState(false);
  const [isEarthquakeCached, setIsEarthquakeCached] = useState(false);
  const [targetMapRegion, setTargetMapRegion] = useState("bengaluru"); // "bengaluru" | "india" | "worldwide"

  // System & Action States
  const [backendStatus, setBackendStatus] = useState("Checking...");
  const [isBackendHealthy, setIsBackendHealthy] = useState(false);
  const [planningSummary, setPlanningSummary] = useState("Ready for allocation");
  const [isLoading, setIsLoading] = useState(false);
  const [isAllocating, setIsAllocating] = useState(false);
  const [operationMessage, setOperationMessage] = useState(null);

  // Modals & Inspection States
  const [selectedIncident, setSelectedIncident] = useState(null);
  const [isIncidentFormOpen, setIsIncidentFormOpen] = useState(false);
  const [isClosureModalOpen, setIsClosureModalOpen] = useState(false);
  const [selectedLocation, setSelectedLocation] = useState(null);

  // Layer Visibility
  const [layerVisibility, setLayerVisibility] = useState({
    earthquakes: true,
    incidents: true,
    teams: true,
    closures: true,
    routes: true,
  });

  // Active view tab for operational data table
  const [activeTableTab, setActiveTableTab] = useState("incidents"); // "incidents" | "teams" | "closures"

  /**
   * Helper to recalculate route polylines for assigned pairs.
   */
  const computeAssignmentRoutes = useCallback(
    async (currentIncidents, currentTeams, allocationAssignments = null) => {
      const computedRoutes = [];

      // If we have explicit assignment pairings from planning run
      if (allocationAssignments && allocationAssignments.length > 0) {
        for (const assign of allocationAssignments) {
          const team = currentTeams.find((t) => t.id === assign.rescue_team_id);
          const inc = currentIncidents.find((i) => i.id === assign.incident_id);

          if (team && inc && team.latitude != null && inc.latitude != null) {
            try {
              const routeData = await calculateRoute({
                start_lat: team.latitude,
                start_lon: team.longitude,
                dest_lat: inc.latitude,
                dest_lon: inc.longitude,
                weight: "travel_time",
                algorithm: "dijkstra",
              });

              if (routeData.status === "success" && routeData.route_coordinates) {
                // Route coordinates from backend are GeoJSON [lon, lat]; convert to Leaflet [lat, lon]
                const leafletCoords = routeData.route_coordinates.map((pt) => [pt[1], pt[0]]);
                computedRoutes.push({
                  assignmentId: assign.assignment_id || `${team.id}-${inc.id}`,
                  teamName: assign.rescue_team_name || team.name,
                  incidentTitle: assign.incident_title || inc.title,
                  distanceKm: routeData.distance_km,
                  timeMinutes: routeData.estimated_time_minutes,
                  travelTimeSource: routeData.travel_time_source,
                  coordinates: leafletCoords,
                });
              }
            } catch {
              // Gracefully continue if routing calculation fails for an edge case
            }
          }
        }
      } else {
        // Fallback: match assigned incidents with assigned teams if coordinates exist
        const assignedIncidents = currentIncidents.filter((i) => i.status === "assigned");
        const assignedTeams = currentTeams.filter((t) => t.status === "assigned");

        // Pair up to the min count
        const pairCount = Math.min(assignedIncidents.length, assignedTeams.length);
        for (let idx = 0; idx < pairCount; idx++) {
          const team = assignedTeams[idx];
          const inc = assignedIncidents[idx];

          if (team.latitude != null && inc.latitude != null) {
            try {
              const routeData = await calculateRoute({
                start_lat: team.latitude,
                start_lon: team.longitude,
                dest_lat: inc.latitude,
                dest_lon: inc.longitude,
                weight: "travel_time",
                algorithm: "dijkstra",
              });

              if (routeData.status === "success" && routeData.route_coordinates) {
                const leafletCoords = routeData.route_coordinates.map((pt) => [pt[1], pt[0]]);
                computedRoutes.push({
                  assignmentId: `paired-${team.id}-${inc.id}`,
                  teamName: team.name,
                  incidentTitle: inc.title,
                  distanceKm: routeData.distance_km,
                  timeMinutes: routeData.estimated_time_minutes,
                  travelTimeSource: routeData.travel_time_source,
                  coordinates: leafletCoords,
                });
              }
            } catch {
              // Continue
            }
          }
        }
      }

      setRoutes(computedRoutes);
    },
    []
  );

  /**
   * Primary data-fetching method.
   */
  const loadAllData = useCallback(
    async (showStatus = false) => {
      setIsLoading(true);
      if (showStatus) setOperationMessage("Refreshing operational data...");

      try {
        // 1. Health check
        try {
          const health = await checkApiHealth();
          setBackendStatus(`Online (${health.status})`);
          setIsBackendHealthy(true);
        } catch {
          setBackendStatus("Offline / Unreachable");
          setIsBackendHealthy(false);
        }

        // 2. Parallel API calls for operational datasets
        const [incidentsRes, teamsRes, closuresRes, graphRes, earthquakesRes] =
          await Promise.allSettled([
            getIncidentRankings().catch(() => getIncidents()),
            getRescueTeams(),
            getRoadClosures("all"),
            getRoadGraphStatus(),
            getEarthquakes({
              feed: earthquakeFeed,
              region: earthquakeRegion,
              min_magnitude: earthquakeMinMag,
              limit: 150,
              sync_db: true,
            }),
          ]);

        const fetchedIncidents = incidentsRes.status === "fulfilled" ? incidentsRes.value : [];
        const fetchedTeams = teamsRes.status === "fulfilled" ? teamsRes.value : [];
        const fetchedClosures = closuresRes.status === "fulfilled" ? closuresRes.value : [];
        const fetchedGraph = graphRes.status === "fulfilled" ? graphRes.value : null;
        const fetchedEarthquakes =
          earthquakesRes.status === "fulfilled" && earthquakesRes.value?.features
            ? earthquakesRes.value.features
            : [];

        setIncidents(fetchedIncidents);
        setRescueTeams(fetchedTeams);
        setRoadClosures(fetchedClosures);
        setGraphStatus(fetchedGraph);
        setEarthquakes(fetchedEarthquakes);

        if (earthquakesRes.status === "fulfilled" && earthquakesRes.value) {
          setLastEarthquakeRefresh(
            new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
          );
          setIsEarthquakeCached(Boolean(earthquakesRes.value?.metadata?.cached_fallback));
        }

        // 3. Compute active route polylines
        await computeAssignmentRoutes(fetchedIncidents, fetchedTeams);

        if (showStatus) {
          setOperationMessage("Operational data refreshed successfully.");
          setTimeout(() => setOperationMessage(null), 3500);
        }
      } catch (err) {
        setOperationMessage(`Sync failed: ${err.message}`);
      } finally {
        setIsLoading(false);
      }
    },
    [computeAssignmentRoutes, earthquakeFeed, earthquakeRegion, earthquakeMinMag]
  );

  /**
   * Dedicated real-time USGS earthquake feed fetcher.
   */
  const fetchEarthquakesFeed = useCallback(
    async (
      region = earthquakeRegion,
      feed = earthquakeFeed,
      minMag = earthquakeMinMag,
      silent = false
    ) => {
      if (!silent) setIsEarthquakeRefreshing(true);
      try {
        const res = await getEarthquakes({
          feed,
          region,
          min_magnitude: minMag,
          limit: 150,
          sync_db: true,
        });
        if (res && res.features) {
          setEarthquakes(res.features);
          setLastEarthquakeRefresh(
            new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
          );
          setIsEarthquakeCached(Boolean(res.metadata?.cached_fallback));
          if (!silent) {
            setOperationMessage(
              `USGS Earthquake feed updated: ${res.features.length} events loaded${
                res.metadata?.cached_fallback ? " (using local cache)" : ""
              }.`
            );
            setTimeout(() => setOperationMessage(null), 3500);
          }
        }
      } catch (err) {
        if (!silent) {
          setOperationMessage(`USGS feed update warning: ${err.message}`);
        }
      } finally {
        if (!silent) setIsEarthquakeRefreshing(false);
      }
    },
    [earthquakeRegion, earthquakeFeed, earthquakeMinMag]
  );

  // Requirement 6: Automatic refresh every 5 minutes (300,000 ms)
  useEffect(() => {
    const timer = setInterval(() => {
      fetchEarthquakesFeed(earthquakeRegion, earthquakeFeed, earthquakeMinMag, true);
    }, 5 * 60 * 1000);
    return () => clearInterval(timer);
  }, [earthquakeRegion, earthquakeFeed, earthquakeMinMag, fetchEarthquakesFeed]);

  /**
   * Handle changing region, time window, or magnitude filters for earthquakes.
   */
  function handleFilterChange(newRegion, newFeed, newMinMag) {
    const r = newRegion !== undefined ? newRegion : earthquakeRegion;
    const f = newFeed !== undefined ? newFeed : earthquakeFeed;
    const m = newMinMag !== undefined ? newMinMag : earthquakeMinMag;

    if (newRegion !== undefined) {
      setEarthquakeRegion(newRegion);
      setTargetMapRegion(newRegion);
      setSelectedLocation(null);
    }
    if (newFeed !== undefined) setEarthquakeFeed(newFeed);
    if (newMinMag !== undefined) setEarthquakeMinMag(newMinMag);

    fetchEarthquakesFeed(r, f, m, false);
  }

  // Initial load
  useEffect(() => {
    loadAllData();
  }, [loadAllData]);

  /**
   * Action: Trigger Greedy Resource Allocation
   */
  async function handleTriggerAllocation() {
    setIsAllocating(true);
    setOperationMessage("Calculating Dijkstra shortest paths & assigning rescue units...");

    try {
      const allocation = await allocateResources();
      setPlanningSummary(allocation.summary || "Planning completed");

      // Reload state and pass new assignments directly to compute routes
      const [freshIncidents, freshTeams] = await Promise.all([
        getIncidentRankings(),
        getRescueTeams(),
      ]);

      setIncidents(freshIncidents);
      setRescueTeams(freshTeams);

      await computeAssignmentRoutes(freshIncidents, freshTeams, allocation.assignments);

      setOperationMessage(
        `Allocation finished! ${allocation.summary || "Incidents assigned to available teams."}`
      );
    } catch (err) {
      setOperationMessage(`Allocation failed: ${err.message}`);
    } finally {
      setIsAllocating(false);
    }
  }

  /**
   * Action: Trigger Dynamic Replanning with safe preemption & route verification
   */
  async function handleTriggerReplanning() {
    setIsAllocating(true);
    setOperationMessage("Evaluating road closures, active operations, and justified preemption...");

    try {
      const replanResult = await triggerReplanning(true);
      setPlanningSummary(replanResult.summary || "Replanning completed");

      const [freshIncidents, freshTeams] = await Promise.all([
        getIncidentRankings(),
        getRescueTeams(),
      ]);

      setIncidents(freshIncidents);
      setRescueTeams(freshTeams);

      await computeAssignmentRoutes(freshIncidents, freshTeams, replanResult.new_assignments);

      let msg = replanResult.summary;
      if (replanResult.reassignments && replanResult.reassignments.length > 0) {
        msg += ` (${replanResult.reassignments.length} emergency preemption(s) executed)`;
      }
      setOperationMessage(msg);
    } catch (err) {
      setOperationMessage(`Replanning failed: ${err.message}`);
    } finally {
      setIsAllocating(false);
    }
  }

  /**
   * Action: Quick Reopen Road
   */
  async function handleQuickReopenRoad(closureId) {
    try {
      setOperationMessage(`Reopening road closure #${closureId}...`);
      const result = await reopenRoad({
        closure_id: closureId,
        both_directions: true,
      });
      setOperationMessage(
        `${result.message || "Road reopened successfully."} Routes recalculated on restored graph.`
      );
      await loadAllData(false);
    } catch (err) {
      setOperationMessage(`Failed to reopen road: ${err.message}`);
    }
  }

  /**
   * Action: Focus Map on specific coordinates
   */
  function handleFocusMap(lat, lon) {
    if (lat != null && lon != null) {
      setSelectedLocation({ lat: Number(lat), lon: Number(lon) });
      window.scrollTo({ top: 320, behavior: "smooth" });
    }
  }

  // Calculated Metrics
  const totalIncidentsCount = incidents.length;
  const criticalIncidentsCount = incidents.filter(
    (i) => i.priority_level === "Critical" || (i.priority_score != null && i.priority_score >= 80)
  ).length;
  const availableTeamsCount = rescueTeams.filter((t) => t.status === "available").length;
  const totalTeamsCount = rescueTeams.length;
  const activeClosuresCount = roadClosures.filter((c) => c.status === "active").length;

  return (
    <>
      <DashboardHeader />

      <main className="page-shell">
        {/* Operations Welcome & Controls Bar */}
        <section className="welcome">
          <div>
            <div className="status-badge-row">
              <span className="eyebrow">QUAKERESQ OPERATIONS DISPATCH</span>
              <span
                className={`backend-pill ${
                  isBackendHealthy ? "backend-pill-online" : "backend-pill-offline"
                }`}
              >
                <span className="pill-dot" />
                {backendStatus}
              </span>
            </div>
            <h1>Earthquake Rescue Dashboard</h1>
            <p className="welcome-copy">
              Integrated real-time command for incident ranking, Dijkstra routing, and emergency
              allocation.
            </p>
          </div>

          <div className="controls-button-group">
            <button
              className="btn-secondary"
              type="button"
              onClick={() => loadAllData(true)}
              disabled={isLoading}
            >
              {isLoading ? "Syncing..." : "Refresh Data"}
            </button>

            <button
              className="btn-primary"
              type="button"
              onClick={handleTriggerAllocation}
              disabled={isAllocating}
            >
              {isAllocating ? "Allocating..." : "Run Resource Allocation"}
            </button>

            <button
              className="btn-primary"
              style={{ background: "#4338ca" }}
              type="button"
              onClick={handleTriggerReplanning}
              disabled={isAllocating}
            >
              {isAllocating ? "Replanning..." : "Dynamic Replanning"}
            </button>

            <button
              className="btn-accent"
              type="button"
              onClick={() => setIsIncidentFormOpen(true)}
            >
              + Report Incident
            </button>

            <button
              className="btn-outline"
              type="button"
              onClick={() => setIsClosureModalOpen(true)}
            >
              Road Closures {activeClosuresCount > 0 && `(${activeClosuresCount})`}
            </button>
          </div>
        </section>

        {/* Data Source Transparency Banner */}
        <div
          className="data-source-legend-bar"
          style={{
            display: "flex",
            gap: "18px",
            flexWrap: "wrap",
            fontSize: "12px",
            padding: "10px 18px",
            background: "#f8fafc",
            borderRadius: "10px",
            border: "1px solid #e2e8f0",
            margin: "0 0 16px",
            alignItems: "center",
          }}
        >
          <span style={{ fontWeight: 700, color: "#475569", textTransform: "uppercase", fontSize: "11px" }}>
            Data Transparency:
          </span>
          <span style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
            <span style={{ width: "8px", height: "8px", borderRadius: "50%", background: "#10b981" }} />
            <strong>USGS Earthquakes:</strong> Live Official Feed
          </span>
          <span style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
            <span style={{ width: "8px", height: "8px", borderRadius: "50%", background: "#6366f1" }} />
            <strong>Road Network:</strong> Real OSMnx Graph (Bengaluru Central)
          </span>
          <span style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
            <span style={{ width: "8px", height: "8px", borderRadius: "50%", background: "#f59e0b" }} />
            <strong>Road Closures:</strong> Operator-Simulated Hazards
          </span>
          <span style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
            <span style={{ width: "8px", height: "8px", borderRadius: "50%", background: "#3b82f6" }} />
            <strong>Rescue Units:</strong> App-Managed Demo Units (Simulated GPS)
          </span>
        </div>

        {/* Global Operation / Notification Message */}
        {operationMessage && (
          <div className="notification-toast" role="status">
            <span>{operationMessage}</span>
            <button
              type="button"
              className="toast-close"
              onClick={() => setOperationMessage(null)}
            >
              &times;
            </button>
          </div>
        )}

        {/* Metrics KPI Cards */}
        <MetricsOverview
          totalIncidents={totalIncidentsCount}
          criticalIncidents={criticalIncidentsCount}
          availableTeams={availableTeamsCount}
          totalTeams={totalTeamsCount}
          activeClosures={activeClosuresCount}
          planningStatus={planningSummary}
          graphStatus={graphStatus}
        />

        {/* Map Header with Layer Toggles */}
        <div className="map-section-header">
          <div>
            <span className="eyebrow">GEOSPATIAL SITUATION ROOM</span>
            <h2 className="map-heading">Bengaluru Metropolitan Road Network</h2>
          </div>

          <div className="layer-toggles">
            <label className="layer-toggle-chip">
              <input
                type="checkbox"
                checked={layerVisibility.incidents}
                onChange={(e) =>
                  setLayerVisibility((prev) => ({ ...prev, incidents: e.target.checked }))
                }
              />
              <span className="chip-indicator chip-incidents" />
              <span>Incidents ({totalIncidentsCount})</span>
            </label>

            <label className="layer-toggle-chip">
              <input
                type="checkbox"
                checked={layerVisibility.teams}
                onChange={(e) =>
                  setLayerVisibility((prev) => ({ ...prev, teams: e.target.checked }))
                }
              />
              <span className="chip-indicator chip-teams" />
              <span>Rescue Units ({totalTeamsCount})</span>
            </label>

            <label className="layer-toggle-chip">
              <input
                type="checkbox"
                checked={layerVisibility.closures}
                onChange={(e) =>
                  setLayerVisibility((prev) => ({ ...prev, closures: e.target.checked }))
                }
              />
              <span className="chip-indicator chip-closures" />
              <span>Closures ({activeClosuresCount})</span>
            </label>

            <label className="layer-toggle-chip">
              <input
                type="checkbox"
                checked={layerVisibility.routes}
                onChange={(e) =>
                  setLayerVisibility((prev) => ({ ...prev, routes: e.target.checked }))
                }
              />
              <span className="chip-indicator chip-routes" />
              <span>Active Routes ({routes.length})</span>
            </label>

            <label className="layer-toggle-chip">
              <input
                type="checkbox"
                checked={layerVisibility.earthquakes}
                onChange={(e) =>
                  setLayerVisibility((prev) => ({ ...prev, earthquakes: e.target.checked }))
                }
              />
              <span className="chip-indicator chip-earthquakes" />
              <span>Earthquakes ({earthquakes.length})</span>
            </label>
          </div>
        </div>

        {/* Real-Time USGS Earthquake Live Telemetry Bar */}
        <section className="earthquake-control-bar">
          <div className="eq-bar-top">
            <div className="eq-bar-title-group">
              <span className="eq-bar-title">
                <span style={{ fontSize: "16px" }}>⚡</span> Real-Time USGS Earthquake Feed
              </span>
              <span
                className={`eq-live-indicator ${
                  isEarthquakeCached ? "eq-live-cached" : "eq-live-active"
                }`}
              >
                <span className="eq-pulse-dot" />
                {isEarthquakeCached ? "Offline Cache" : "USGS Live"}
              </span>
            </div>

            <div className="eq-bar-meta">
              <span>
                <strong>Last updated:</strong> {lastEarthquakeRefresh || "Initial sync"}
              </span>
              <span style={{ color: "#94a3b8" }}>•</span>
              <span>
                <strong>Auto-refresh:</strong> Every 5 min
              </span>
              <span style={{ color: "#94a3b8" }}>•</span>
              <span>
                <strong>Events:</strong> {earthquakes.length} epicenters
              </span>
              <button
                type="button"
                className="eq-btn-refresh"
                onClick={() =>
                  fetchEarthquakesFeed(earthquakeRegion, earthquakeFeed, earthquakeMinMag, false)
                }
                disabled={isEarthquakeRefreshing}
              >
                {isEarthquakeRefreshing ? "Refreshing..." : "↻ Refresh Feed"}
              </button>
            </div>
          </div>

          <div className="eq-bar-filters">
            {/* Region Filter */}
            <div className="eq-filter-group">
              <span className="eq-filter-label">Region:</span>
              <button
                type="button"
                className={`eq-filter-btn ${earthquakeRegion === "worldwide" ? "active" : ""}`}
                onClick={() => handleFilterChange("worldwide", undefined, undefined)}
              >
                Worldwide
              </button>
              <button
                type="button"
                className={`eq-filter-btn ${earthquakeRegion === "india" ? "active" : ""}`}
                onClick={() => handleFilterChange("india", undefined, undefined)}
              >
                India & Regional
              </button>
            </div>

            {/* Time Window / Feed Filter */}
            <div className="eq-filter-group">
              <span className="eq-filter-label">Time Window:</span>
              <button
                type="button"
                className={`eq-filter-btn ${earthquakeFeed === "all_day" ? "active" : ""}`}
                onClick={() => handleFilterChange(undefined, "all_day", undefined)}
              >
                Past 24 Hours
              </button>
              <button
                type="button"
                className={`eq-filter-btn ${earthquakeFeed === "all_week" ? "active" : ""}`}
                onClick={() => handleFilterChange(undefined, "all_week", undefined)}
              >
                Past 7 Days
              </button>
            </div>

            {/* Minimum Magnitude Filter */}
            <div className="eq-filter-group">
              <span className="eq-filter-label">Min Magnitude:</span>
              {[
                { label: "All (M0+)", val: 0.0 },
                { label: "M2.5+", val: 2.5 },
                { label: "M4.5+", val: 4.5 },
                { label: "M6.0+", val: 6.0 },
              ].map((item) => (
                <button
                  key={item.val}
                  type="button"
                  className={`eq-filter-btn ${earthquakeMinMag === item.val ? "active" : ""}`}
                  onClick={() => handleFilterChange(undefined, undefined, item.val)}
                >
                  {item.label}
                </button>
              ))}
            </div>

            {/* Map Centering Shortcut */}
            <div className="eq-filter-group">
              <button
                type="button"
                className="eq-filter-btn"
                style={{ background: "#f1f5f9", fontWeight: 700 }}
                onClick={() => {
                  setTargetMapRegion("bengaluru");
                  setSelectedLocation({ lat: 12.9716, lon: 77.5946 });
                }}
              >
                Focus Bengaluru
              </button>
            </div>
          </div>
        </section>

        {/* Interactive Leaflet Map */}
        <ResponseMap
          earthquakes={earthquakes}
          incidents={incidents}
          rescueTeams={rescueTeams}
          roadClosures={roadClosures}
          routes={routes}
          selectedLocation={selectedLocation}
          targetRegion={targetMapRegion}
          onSelectIncident={(inc) => setSelectedIncident(inc)}
          onReopenRoad={handleQuickReopenRoad}
          layerVisibility={layerVisibility}
        />

        {/* Operational Records & Ranking Table */}
        <section className="data-table-section">
          <div className="table-tabs-header">
            <div className="table-tabs">
              <button
                type="button"
                className={`table-tab ${activeTableTab === "incidents" ? "active" : ""}`}
                onClick={() => setActiveTableTab("incidents")}
              >
                Incident Rankings ({incidents.length})
              </button>
              <button
                type="button"
                className={`table-tab ${activeTableTab === "teams" ? "active" : ""}`}
                onClick={() => setActiveTableTab("teams")}
              >
                Rescue Teams ({rescueTeams.length})
              </button>
              <button
                type="button"
                className={`table-tab ${activeTableTab === "closures" ? "active" : ""}`}
                onClick={() => setActiveTableTab("closures")}
              >
                Road Closures ({roadClosures.length})
              </button>
              <button
                type="button"
                className={`table-tab ${activeTableTab === "earthquakes" ? "active" : ""}`}
                onClick={() => setActiveTableTab("earthquakes")}
              >
                Earthquake Feed ({earthquakes.length})
              </button>
            </div>

            <span className="table-hint">
              {activeTableTab === "incidents" &&
                "Ranked by computed priority score descending (Severity 50%, Urgency 30%, Evidence 20%)."}
              {activeTableTab === "teams" &&
                "Team resources, deployment coordinates, and capacity limits."}
              {activeTableTab === "closures" &&
                "Closed road segments removed from Dijkstra graph computation."}
              {activeTableTab === "earthquakes" &&
                "Official USGS real-time seismic epicenters, magnitudes, and focal depths."}
            </span>
          </div>

          {/* TAB 1: Incidents Table */}
          {activeTableTab === "incidents" && (
            <div className="table-responsive">
              <table className="ops-table">
                <thead>
                  <tr>
                    <th>Rank / ID</th>
                    <th>Priority</th>
                    <th>Incident Title</th>
                    <th>Status</th>
                    <th>People Affected</th>
                    <th>Coordinates</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {incidents.length === 0 ? (
                    <tr>
                      <td colSpan={7} className="table-empty">
                        No incidents reported. Click &quot;+ Report Incident&quot; to add one.
                      </td>
                    </tr>
                  ) : (
                    incidents.map((inc, idx) => (
                      <tr key={inc.id} className={inc.priority_level === "Critical" ? "row-critical" : ""}>
                        <td className="font-bold">
                          #{inc.id}{" "}
                          <span className="rank-badge">R{idx + 1}</span>
                        </td>
                        <td>
                          <span
                            className={`priority-badge ${
                              inc.priority_level === "Critical"
                                ? "badge-critical"
                                : inc.priority_level === "High"
                                ? "badge-high"
                                : inc.priority_level === "Medium"
                                ? "badge-medium"
                                : "badge-low"
                            }`}
                          >
                            {inc.priority_level || "Scoring..."}{" "}
                            {inc.priority_score != null && `(${inc.priority_score.toFixed(1)})`}
                          </span>
                        </td>
                        <td className="font-semibold text-primary">{inc.title}</td>
                        <td>
                          <span className={`status-pill status-${inc.status}`}>{inc.status}</span>
                        </td>
                        <td>{inc.people_affected ?? 0} persons</td>
                        <td className="text-muted">
                          {Number(inc.latitude).toFixed(4)}, {Number(inc.longitude).toFixed(4)}
                        </td>
                        <td>
                          <div className="table-actions">
                            <button
                              type="button"
                              className="btn-table-action"
                              onClick={() => setSelectedIncident(inc)}
                            >
                              Inspect
                            </button>
                            <button
                              type="button"
                              className="btn-table-action"
                              onClick={() => handleFocusMap(inc.latitude, inc.longitude)}
                            >
                              Map
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          )}

          {/* TAB 2: Rescue Teams Table */}
          {activeTableTab === "teams" && (
            <div className="table-responsive">
              <table className="ops-table">
                <thead>
                  <tr>
                    <th>Team ID</th>
                    <th>Unit Name</th>
                    <th>Specialty</th>
                    <th>Deployment Status</th>
                    <th>Remaining Capacity</th>
                    <th>Active Missions</th>
                    <th>Personnel</th>
                    <th>Base Coordinates</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {rescueTeams.map((team) => {
                    const remaining = team.remaining_capacity ?? team.capacity;
                    const activeCount =
                      team.active_assignments_count ?? (team.status === "assigned" ? 1 : 0);
                    return (
                      <tr key={team.id}>
                        <td className="font-bold">#{team.id}</td>
                        <td className="font-semibold">
                          {team.name}
                          <span
                            style={{
                              fontSize: "10px",
                              background: "#f1f5f9",
                              padding: "2px 6px",
                              borderRadius: "4px",
                              color: "#64748b",
                              marginLeft: "8px",
                              fontWeight: 700,
                              textTransform: "uppercase",
                            }}
                          >
                            Demo GPS
                          </span>
                        </td>
                        <td>{team.team_type}</td>
                        <td>
                          <span className={`status-pill status-${team.status}`}>{team.status}</span>
                        </td>
                        <td>
                          <span
                            style={{
                              fontWeight: 700,
                              color: remaining > 0 ? "#059669" : "#dc2626",
                            }}
                          >
                            {remaining} / {team.capacity} units
                          </span>
                        </td>
                        <td>{activeCount} active mission(s)</td>
                        <td>{team.members_count ?? "N/A"} personnel</td>
                        <td className="text-muted font-mono" style={{ fontSize: "12px" }}>
                          {Number(team.latitude).toFixed(4)}, {Number(team.longitude).toFixed(4)}
                        </td>
                        <td>
                          <button
                            type="button"
                            className="btn-table-action"
                            onClick={() => handleFocusMap(team.latitude, team.longitude)}
                          >
                            Map
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {/* TAB 3: Road Closures Table */}
          {activeTableTab === "closures" && (
            <div className="table-responsive">
              <table className="ops-table">
                <thead>
                  <tr>
                    <th>Closure ID</th>
                    <th>Road Name</th>
                    <th>Start Node</th>
                    <th>End Node</th>
                    <th>Reason / Hazard</th>
                    <th>Status</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {roadClosures.length === 0 ? (
                    <tr>
                      <td colSpan={7} className="table-empty">
                        No road closures in database.
                      </td>
                    </tr>
                  ) : (
                    roadClosures.map((c) => (
                      <tr key={c.id}>
                        <td className="font-bold">#{c.id}</td>
                        <td className="font-semibold">{c.road_name}</td>
                        <td className="font-mono">{c.start_node_id}</td>
                        <td className="font-mono">{c.end_node_id}</td>
                        <td>{c.reason || "Obstruction"}</td>
                        <td>
                          <span
                            className={`status-pill ${
                              c.status === "active" ? "status-closed" : "status-available"
                            }`}
                          >
                            {c.status}
                          </span>
                        </td>
                        <td>
                          {c.status === "active" ? (
                            <button
                              type="button"
                              className="btn-table-action action-reopen"
                              onClick={() => handleQuickReopenRoad(c.id)}
                            >
                              Reopen
                            </button>
                          ) : (
                            <span className="text-muted">Restored</span>
                          )}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          )}

          {/* TAB 4: Earthquakes Table */}
          {activeTableTab === "earthquakes" && (
            <div className="table-responsive">
              <table className="ops-table">
                <thead>
                  <tr>
                    <th>Event ID</th>
                    <th>Magnitude</th>
                    <th>Epicenter Location</th>
                    <th>Depth</th>
                    <th>Occurred At</th>
                    <th>Coordinates</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {earthquakes.length === 0 ? (
                    <tr>
                      <td colSpan={7} className="table-empty">
                        No earthquakes found for current filter settings ({earthquakeRegion},{" "}
                        {earthquakeFeed}, M{earthquakeMinMag}+).
                      </td>
                    </tr>
                  ) : (
                    earthquakes.map((eq) => {
                      const coords = eq.geometry?.coordinates || [0, 0];
                      const lon = Number(coords[0]);
                      const lat = Number(coords[1]);
                      const depth = coords.length > 2 ? coords[2] : eq.properties?.depth_km;
                      const mag = Number(eq.properties?.magnitude ?? eq.properties?.mag ?? 0);
                      const place =
                        eq.properties?.location_name ?? eq.properties?.place ?? "Unknown location";
                      const timeRaw = eq.properties?.occurred_at ?? eq.properties?.time;
                      const timeStr = timeRaw ? new Date(timeRaw).toLocaleString() : "N/A";
                      const url = eq.properties?.url;

                      const magBadgeClass =
                        mag >= 6.0
                          ? "badge-mag-major"
                          : mag >= 4.5
                          ? "badge-mag-strong"
                          : mag >= 3.0
                          ? "badge-mag-moderate"
                          : "badge-mag-minor";

                      return (
                        <tr key={eq.id || `${lat}-${lon}`}>
                          <td className="font-bold text-muted font-mono">{eq.id || "N/A"}</td>
                          <td>
                            <span className={magBadgeClass}>M {mag.toFixed(1)}</span>
                          </td>
                          <td className="font-semibold">{place}</td>
                          <td>{depth != null ? `${depth} km` : "N/A"}</td>
                          <td className="text-muted" style={{ fontSize: "12px" }}>
                            {timeStr}
                          </td>
                          <td className="font-mono text-muted" style={{ fontSize: "12px" }}>
                            {lat.toFixed(4)}, {lon.toFixed(4)}
                          </td>
                          <td>
                            <div className="table-actions">
                              <button
                                type="button"
                                className="btn-table-action"
                                onClick={() => handleFocusMap(lat, lon)}
                              >
                                Map
                              </button>
                              {url && (
                                <a
                                  href={url}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="btn-table-action"
                                  style={{
                                    textDecoration: "none",
                                    display: "inline-flex",
                                    alignItems: "center",
                                  }}
                                >
                                  USGS ↗
                                </a>
                              )}
                            </div>
                          </td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {/* MODAL 1: Incident Form Modal */}
        <IncidentFormModal
          isOpen={isIncidentFormOpen}
          onClose={() => setIsIncidentFormOpen(false)}
          onSuccess={() => {
            setOperationMessage("New incident created and prioritized successfully.");
            loadAllData();
          }}
          earthquakes={earthquakes}
        />

        {/* MODAL 2: Incident Details Modal */}
        {selectedIncident && (
          <IncidentDetailsModal
            incident={selectedIncident}
            onClose={() => setSelectedIncident(null)}
            onFocusMap={(lat, lon) => handleFocusMap(lat, lon)}
            onUpdated={(updated) => {
              setSelectedIncident(updated);
              loadAllData();
            }}
            assignedTeam={
              selectedIncident.status === "assigned"
                ? rescueTeams.find((t) => t.status === "assigned")
                : null
            }
          />
        )}

        {/* MODAL 3: Road Closures Manager */}
        <RoadClosureModal
          isOpen={isClosureModalOpen}
          onClose={() => setIsClosureModalOpen(false)}
          closures={roadClosures}
          onUpdated={async () => {
            await loadAllData(false);
            setOperationMessage(
              "Road network updated: active routes automatically recalculated on working graph."
            );
            setTimeout(() => setOperationMessage(null), 4000);
          }}
        />
      </main>
    </>
  );
}
