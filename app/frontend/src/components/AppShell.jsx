import { NavLink } from "react-router-dom";

const navLinkClass = ({ isActive }) =>
  [
    "rounded-full border px-4 py-2 text-sm font-semibold transition",
    isActive
      ? "border-primary-300 bg-primary-50 text-primary-700"
      : "border-transparent text-slate-600 hover:border-primary-200 hover:text-primary-700"
  ].join(" ");

export default function AppShell({ children }) {
  return (
    <div className="app-shell">
      <header className="mb-8 flex flex-wrap items-center justify-between gap-4">
        <div>
          <p className="text-xs uppercase tracking-[0.3em] text-primary-600">
            Codex
          </p>
          <h1 className="text-3xl font-semibold text-slate-900">Codex Runner</h1>
          <p className="mt-1 text-sm text-slate-600">
            Agent runs, patches, and repo insight.
          </p>
        </div>
        <nav className="flex flex-wrap gap-2">
          <NavLink to="/" end className={navLinkClass}>
            Run Task
          </NavLink>
          <NavLink to="/tools" className={navLinkClass}>
            Repo Tools
          </NavLink>
        </nav>
      </header>
      <main className="grid gap-6">{children}</main>
    </div>
  );
}
