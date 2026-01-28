import { useState } from "react";

import { useWorkspace } from "../context/WorkspaceContext.jsx";
import { ButtonPrimary, ButtonSecondary, Card, Input } from "./ui.jsx";

export default function WorkspaceModal() {
  const {
    workspaces,
    openWorkspace,
    setIsModalOpen,
    activeWorkspaceId,
    setActiveWorkspaceId
  } = useWorkspace();
  const [path, setPath] = useState("");
  const [error, setError] = useState("");

  const submit = async (event) => {
    event.preventDefault();
    setError("");
    if (!path.trim()) {
      setError("Path is required.");
      return;
    }
    try {
      await openWorkspace(path.trim());
      setPath("");
    } catch (err) {
      setError(err.message || "Failed to open workspace");
    }
  };

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-slate-900/40 p-6">
      <Card className="w-full max-w-2xl p-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-base font-semibold text-slate-900">Open Folder</h2>
            <p className="mt-1 text-sm text-slate-600">
              Paste a local repo path or select a recent workspace.
            </p>
          </div>
          <ButtonSecondary type="button" onClick={() => setIsModalOpen(false)}>
            Close
          </ButtonSecondary>
        </div>

        <form className="mt-4 grid gap-2" onSubmit={submit}>
          <label className="label" htmlFor="workspace_path">
            Folder path
          </label>
          <Input
            id="workspace_path"
            value={path}
            onChange={(event) => setPath(event.target.value)}
            placeholder="C:\\Projects\\my-repo or /Users/me/projects/my-repo"
          />
          {error ? <p className="text-sm text-red-600">{error}</p> : null}
          <div className="flex flex-wrap gap-2">
            <ButtonPrimary type="submit">Open Folder</ButtonPrimary>
            <ButtonSecondary
              type="button"
              onClick={() => {
                setPath("");
                setError("");
              }}
            >
              Clear
            </ButtonSecondary>
          </div>
        </form>

        <div className="mt-4 border-t border-slate-200 pt-4">
          <h3 className="text-sm font-semibold text-slate-800">Recent</h3>
          <div className="mt-3 grid gap-2">
            {workspaces.length ? (
              workspaces.map((workspace) => (
                <button
                  key={workspace.workspace_id}
                  className={`flex w-full items-center justify-between rounded-md border px-3 py-2 text-left text-sm transition ${
                    workspace.workspace_id === activeWorkspaceId
                      ? "border-primary-300 bg-primary-50 text-primary-700"
                      : "border-slate-200 text-slate-700 hover:border-primary-200"
                  }`}
                  type="button"
                  onClick={() => {
                    setActiveWorkspaceId(workspace.workspace_id);
                    setIsModalOpen(false);
                  }}
                >
                  <div>
                    <p className="font-semibold">{workspace.name}</p>
                    <p className="text-xs text-slate-500">{workspace.path}</p>
                  </div>
                  <span className="text-xs uppercase text-slate-400">
                    {workspace.workspace_type}
                  </span>
                </button>
              ))
            ) : (
              <p className="text-sm text-slate-500">
                No recent workspaces yet.
              </p>
            )}
          </div>
        </div>

        <p className="mt-4 text-xs text-slate-500">
          Browser-based apps cannot open a native folder picker without a
          desktop wrapper. Paste the path above to open a local workspace.
        </p>
      </Card>
    </div>
  );
}
