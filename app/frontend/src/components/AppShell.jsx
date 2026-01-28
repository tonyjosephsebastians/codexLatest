import { NavLink } from "react-router-dom";

import { useWorkspace } from "../context/WorkspaceContext.jsx";
import WorkspaceModal from "./WorkspaceModal.jsx";
import { ButtonSecondary, Input, Select } from "./ui.jsx";

const navClass = ({ isActive }) =>
  [
    "rounded-md border px-3 py-2 text-sm font-semibold transition",
    isActive
      ? "border-primary-300 bg-primary-50 text-primary-700"
      : "border-slate-200 text-slate-600 hover:border-primary-200 hover:text-primary-700"
  ].join(" ");

export default function AppShell({ children }) {
  const {
    activeWorkspace,
    activeWorkspaceId,
    workspaces,
    setActiveWorkspaceId,
    setIsModalOpen,
    isModalOpen
  } = useWorkspace();

  return (
    <div className="flex h-screen flex-col bg-slate-50 text-slate-900">
      <header className="z-30 h-14 border-b border-slate-200 bg-white">
        <div className="mx-auto flex h-14 w-full items-center gap-4 px-4">
          <div className="flex items-center gap-3">
            <div>
              <p className="text-[10px] uppercase tracking-[0.28em] text-primary-600">
                Codex
              </p>
              <h1 className="text-sm font-semibold text-slate-900">
                Codex Workspace
              </h1>
            </div>
            <ButtonSecondary type="button" onClick={() => setIsModalOpen(true)}>
              Open Folder
            </ButtonSecondary>
          </div>

          <div className="min-w-[240px]">
            <p className="text-[11px] uppercase tracking-wide text-slate-500">
              Current workspace
            </p>
            <Select
              className="mt-1 h-8"
              value={activeWorkspaceId}
              onChange={(event) => setActiveWorkspaceId(event.target.value)}
            >
              <option value="">Select workspace</option>
              {workspaces.map((workspace) => (
                <option key={workspace.workspace_id} value={workspace.workspace_id}>
                  {workspace.name}
                </option>
              ))}
            </Select>
            <p className="truncate text-xs text-slate-500">
              {activeWorkspace ? activeWorkspace.path : "Open a local repo"}
            </p>
          </div>

          <nav className="flex items-center gap-2">
            <NavLink to="/wiki" className={navClass}>
              Wiki
            </NavLink>
            <NavLink to="/editor" className={navClass}>
              Editor
            </NavLink>
          </nav>

          <div className="ml-auto hidden items-center gap-2 lg:flex">
            <Input className="h-8 w-56" placeholder="Search docs..." disabled />
          </div>
        </div>
      </header>

      <main className="flex-1 min-h-0 overflow-hidden">{children}</main>

      {isModalOpen ? <WorkspaceModal /> : null}
    </div>
  );
}
