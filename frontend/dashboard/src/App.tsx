import { Link, Route, Routes } from "react-router-dom";
import { ReviewInboxPage } from "./routes/ReviewInboxPage";
import { ReviewThreadPage } from "./routes/ReviewThreadPage";

export function App() {
  return (
    <div className="app-shell">
      <header className="topbar">
        <Link to="/" className="topbar-logo">
          <span className="topbar-logo-dot" />
          <span className="topbar-wordmark">HITL Control Plane</span>
        </Link>
        <div className="topbar-divider" />
        <span className="topbar-label">Layer 2 Runtime</span>
        <div className="topbar-spacer" />
        <span className="topbar-pill">
          <span className="topbar-pill-dot" />
          attivo
        </span>
      </header>
      <main className="page-content">
        <Routes>
          <Route path="/" element={<ReviewInboxPage />} />
          <Route path="/threads/:threadId" element={<ReviewThreadPage />} />
        </Routes>
      </main>
    </div>
  );
}
