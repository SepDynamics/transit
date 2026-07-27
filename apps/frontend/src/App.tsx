import { lazy, Suspense, useEffect, useState } from "react";
import StatusPage from "./pages/StatusPage/StatusPage";
import { OPS_CONSOLE_ENABLED } from "./utils/api";

const LiveConsole = lazy(() => import("./pages/LiveConsole/LiveConsole"));

type View = "ops" | "status";

function getViewFromHash(): View {
  if (!OPS_CONSOLE_ENABLED) return "status";
  const hash = typeof window !== "undefined" ? window.location.hash : "";
  return hash.startsWith("#status") ? "status" : "ops";
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
        {OPS_CONSOLE_ENABLED && (
          <a
            href="#ops"
            className={view === "ops" ? "app-nav__link app-nav__link--active" : "app-nav__link"}
            onClick={() => setView("ops")}
          >
            Operations
          </a>
        )}
        <a
          href="#status"
          className={view === "status" ? "app-nav__link app-nav__link--active" : "app-nav__link"}
          onClick={() => setView("status")}
        >
          Service Status
        </a>
      </nav>
      {view === "status" ? (
        <StatusPage />
      ) : (
        <Suspense
          fallback={
            <div className="app-loading" role="status" aria-live="polite">
              Loading Operations…
            </div>
          }
        >
          <LiveConsole />
        </Suspense>
      )}
    </>
  );
}
