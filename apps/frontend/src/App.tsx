import { useEffect, useState } from "react";
import LiveConsole from "./pages/LiveConsole/LiveConsole";
import StatusPage from "./pages/StatusPage/StatusPage";

type View = "ops" | "status";

function getViewFromHash(): View {
  const hash = typeof window !== "undefined" ? window.location.hash : "";
  return hash === "#status" ? "status" : "ops";
}

export default function App() {
  const [view, setView] = useState<View>(getViewFromHash);

  useEffect(() => {
    const handler = () => setView(getViewFromHash());
    window.addEventListener("hashchange", handler);
    return () => window.removeEventListener("hashchange", handler);
  }, []);

  return (
    <>
      <nav className="app-nav">
        <a
          href="#ops"
          className={view === "ops" ? "app-nav__link app-nav__link--active" : "app-nav__link"}
          onClick={() => setView("ops")}
        >
          Operations
        </a>
        <a
          href="#status"
          className={view === "status" ? "app-nav__link app-nav__link--active" : "app-nav__link"}
          onClick={() => setView("status")}
        >
          Service Status
        </a>
      </nav>
      {view === "status" ? <StatusPage /> : <LiveConsole />}
    </>
  );
}
