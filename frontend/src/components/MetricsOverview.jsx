export default function MetricsOverview({
  totalIncidents = 0,
  criticalIncidents = 0,
  availableTeams = 0,
  totalTeams = 0,
  activeClosures = 0,
  planningStatus = "Ready",
  graphStatus = null,
}) {
  return (
    <section className="metrics-grid" aria-label="Key operational metrics">
      {/* 1. Total Incidents */}
      <article className="metric-card">
        <div className="metric-header">
          <span className="metric-indicator indicator-neutral" aria-hidden="true" />
          <span className="metric-title">TOTAL INCIDENTS</span>
        </div>
        <div className="metric-value">{totalIncidents}</div>
        <p className="metric-sub">Operator-reported triage queue</p>
      </article>

      {/* 2. Critical Incidents */}
      <article className="metric-card">
        <div className="metric-header">
          <span className="metric-indicator indicator-critical" aria-hidden="true" />
          <span className="metric-title">CRITICAL INCIDENTS</span>
        </div>
        <div className="metric-value text-critical">{criticalIncidents}</div>
        <p className="metric-sub">
          {criticalIncidents > 0 ? "Immediate rescue response needed" : "No critical priorities"}
        </p>
      </article>

      {/* 3. Available Rescue Teams */}
      <article className="metric-card">
        <div className="metric-header">
          <span className="metric-indicator indicator-available" aria-hidden="true" />
          <span className="metric-title">RESCUE TEAMS & CAPACITY</span>
        </div>
        <div className="metric-value">
          <span className="text-available">{availableTeams}</span>
          <span className="metric-total"> / {totalTeams} ready</span>
        </div>
        <p className="metric-sub">Demo response units & simulated GPS</p>
      </article>

      {/* 4. Active Road Closures */}
      <article className="metric-card">
        <div className="metric-header">
          <span className="metric-indicator indicator-warning" aria-hidden="true" />
          <span className="metric-title">ACTIVE ROAD CLOSURES</span>
        </div>
        <div className="metric-value text-warning">{activeClosures}</div>
        <p className="metric-sub">Simulated hazards (Working Graph)</p>
      </article>

      {/* 5. Current Planning Status */}
      <article className="metric-card">
        <div className="metric-header">
          <span className="metric-indicator indicator-info" aria-hidden="true" />
          <span className="metric-title">PLANNING & ROUTING</span>
        </div>
        <div className="metric-value text-info" style={{ fontSize: "16px", lineHeight: "1.3" }}>
          {planningStatus}
        </div>
        <p className="metric-sub">
          {graphStatus && graphStatus.status === "loaded"
            ? `OSMnx Graph ${graphStatus.graph_version} (${graphStatus.node_count.toLocaleString()} nodes)`
            : "Graph unloaded"}
        </p>
      </article>
    </section>
  );
}
