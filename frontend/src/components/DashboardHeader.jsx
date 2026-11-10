export default function DashboardHeader() {
  return (
    <header className="topbar">
      <a className="brand" href="/" aria-label="QuakeResQ home">
        <span className="brand-mark" aria-hidden="true">Q</span>
        <span>QuakeResQ</span>
      </a>
      <span className="topbar-label">Rescue planning workspace</span>
    </header>
  );
}
