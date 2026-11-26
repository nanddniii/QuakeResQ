/**
 * QuakeResQ Frontend API Service
 * Communicates with the FastAPI backend at /api/v1 endpoints.
 */

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

/**
 * Common request helper with robust error parsing and JSON handling.
 */
async function request(endpoint, options = {}) {
  const url = `${API_BASE_URL}${endpoint}`;
  const config = {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options.headers,
    },
  };

  let response;
  try {
    response = await fetch(url, config);
  } catch (networkError) {
    throw new Error(
      `Cannot connect to backend server at ${API_BASE_URL}. Ensure the backend is running. (${networkError.message})`
    );
  }

  if (response.status === 204) {
    return null;
  }

  let data;
  const contentType = response.headers.get("content-type");
  if (contentType && contentType.includes("application/json")) {
    try {
      data = await response.json();
    } catch {
      data = null;
    }
  } else {
    data = await response.text();
  }

  if (!response.ok) {
    let message = `API request failed with status ${response.status}`;
    if (data && typeof data === "object") {
      if (typeof data.detail === "string") {
        message = data.detail;
      } else if (Array.isArray(data.detail)) {
        message = data.detail.map((d) => d.msg || JSON.stringify(d)).join(", ");
      } else if (data.message) {
        message = data.message;
      }
    } else if (typeof data === "string" && data.trim()) {
      message = data;
    }
    const error = new Error(message);
    error.status = response.status;
    error.data = data;
    throw error;
  }

  return data;
}

// ---------------------------------------------------------------------------
// 1. Health Check
// ---------------------------------------------------------------------------

export async function checkApiHealth() {
  return request("/health");
}

// ---------------------------------------------------------------------------
// 2. Earthquake Events
// ---------------------------------------------------------------------------

export async function getEarthquakes({
  min_magnitude = 2.5,
  limit = 50,
  hours = 24.0,
  feed = null,
  region = null,
  sync_db = false,
} = {}) {
  const params = new URLSearchParams();
  if (min_magnitude !== null && min_magnitude !== undefined) {
    params.set("min_magnitude", String(min_magnitude));
  }
  if (limit !== null && limit !== undefined) {
    params.set("limit", String(limit));
  }
  if (hours !== null && hours !== undefined) {
    params.set("hours", String(hours));
  }
  if (feed) {
    params.set("feed", feed);
  }
  if (region) {
    params.set("region", region);
  }
  if (sync_db) {
    params.set("sync_db", String(sync_db));
  }
  return request(`/api/v1/earthquakes/events?${params.toString()}`);
}

// ---------------------------------------------------------------------------
// 3. Incident Management
// ---------------------------------------------------------------------------

export async function getIncidents() {
  return request("/api/v1/incidents");
}

export async function getIncidentRankings() {
  return request("/api/v1/incidents/rankings");
}

export async function getIncidentById(incidentId) {
  return request(`/api/v1/incidents/${incidentId}`);
}

export async function createIncident(payload) {
  return request("/api/v1/incidents", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function calculateIncidentPriority(incidentId) {
  return request(`/api/v1/incidents/${incidentId}/calculate-priority`, {
    method: "POST",
  });
}

// ---------------------------------------------------------------------------
// 4. Rescue Teams
// ---------------------------------------------------------------------------

export async function getRescueTeams() {
  return request("/api/v1/teams");
}

export async function createRescueTeam(payload) {
  return request("/api/v1/teams", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// ---------------------------------------------------------------------------
// 5. Planning and Resource Allocation
// ---------------------------------------------------------------------------

export async function allocateResources() {
  return request("/api/v1/planning/allocate", {
    method: "POST",
  });
}

export async function triggerReplanning(allowPreemption = true) {
  return request(`/api/v1/planning/replan?allow_preemption=${allowPreemption}`, {
    method: "POST",
  });
}

export async function evaluateClosureRoutes() {
  return request("/api/v1/planning/evaluate-closures", {
    method: "POST",
  });
}

// ---------------------------------------------------------------------------
// 6. Road Network Graph & Routing
// ---------------------------------------------------------------------------

export async function getRoadGraphStatus() {
  return request("/api/v1/roads/graph");
}

export async function calculateRoute({
  start_lat,
  start_lon,
  dest_lat,
  dest_lon,
  weight = "travel_time",
  algorithm = "dijkstra",
}) {
  return request("/api/v1/roads/route", {
    method: "POST",
    body: JSON.stringify({
      start_lat: Number(start_lat),
      start_lon: Number(start_lon),
      dest_lat: Number(dest_lat),
      dest_lon: Number(dest_lon),
      weight,
      algorithm,
    }),
  });
}

// ---------------------------------------------------------------------------
// 7. Road Closures & Reopening
// ---------------------------------------------------------------------------

export async function getRoadClosures(status = "active") {
  return request(`/api/v1/roads/closures?status=${encodeURIComponent(status)}`);
}

export async function createRoadClosure(payload) {
  return request("/api/v1/roads/closures", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function reopenRoad(payload) {
  return request("/api/v1/roads/reopen", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
