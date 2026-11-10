import { useState } from "react";

import DashboardHeader from "../components/DashboardHeader.jsx";
import { dashboardSections } from "../data/dashboard.js";
import { checkApiHealth } from "../services/api.js";

export default function Dashboard() {
  const [apiMessage, setApiMessage] = useState("Backend connection not checked");
  const [isChecking, setIsChecking] = useState(false);

  async function handleHealthCheck() {
    setIsChecking(true);
    try {
      const health = await checkApiHealth();
      setApiMessage(`Backend status: ${health.status}`);
    } catch (error) {
      setApiMessage(error.message);
    } finally {
      setIsChecking(false);
    }
  }

  return (
    <>
      <DashboardHeader />
      <main className="page-shell">
        <section className="welcome">
          <div>
            <p className="eyebrow">OPERATIONS OVERVIEW</p>
            <h1>Rescue planning dashboard</h1>
            <p className="welcome-copy">
              A shared starting point for earthquake response data and planning.
            </p>
          </div>
          <button
            className="health-button"
            type="button"
            onClick={handleHealthCheck}
            disabled={isChecking}
          >
            {isChecking ? "Checking..." : "Check backend"}
          </button>
        </section>

        <section className="summary-grid" aria-label="Project areas">
          {dashboardSections.map((section) => (
            <article className="summary-card" key={section.title}>
              <span className="card-indicator" aria-hidden="true" />
              <h2>{section.title}</h2>
              <p>{section.description}</p>
            </article>
          ))}
        </section>

        <section className="map-placeholder">
          <div>
            <p className="eyebrow">RESPONSE MAP</p>
            <h2>Map workspace</h2>
            <p>Map layers and operational data will be added in a later phase.</p>
          </div>
          <span className="map-grid" aria-hidden="true" />
        </section>

        <p className="api-message" role="status">{apiMessage}</p>
      </main>
    </>
  );
}
