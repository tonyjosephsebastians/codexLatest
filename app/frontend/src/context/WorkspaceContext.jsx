import { createContext, useContext, useEffect, useMemo, useState } from "react";

import { apiDelete, apiGet, apiPost } from "../lib/api.js";
import { useToast } from "./ToastContext.jsx";

const WorkspaceContext = createContext(null);

export function WorkspaceProvider({ children }) {
  const [workspaces, setWorkspaces] = useState([]);
  const [activeWorkspaceId, setActiveWorkspaceId] = useState(
    localStorage.getItem("workspaceId") || ""
  );
  const [activeWorkspace, setActiveWorkspace] = useState(null);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const { pushToast } = useToast();

  const refreshWorkspaces = async () => {
    const data = await apiGet("/api/workspaces");
    setWorkspaces(data.workspaces || []);
  };

  useEffect(() => {
    refreshWorkspaces().catch(() => {});
  }, []);

  useEffect(() => {
    const selected = workspaces.find(
      (item) => item.workspace_id === activeWorkspaceId
    );
    setActiveWorkspace(selected || null);
    if (activeWorkspaceId) {
      localStorage.setItem("workspaceId", activeWorkspaceId);
    }
  }, [activeWorkspaceId, workspaces]);

  const openWorkspace = async (path) => {
    const data = await apiPost("/api/workspaces/open", { path });
    await refreshWorkspaces();
    setActiveWorkspaceId(data.workspace_id);
    setIsModalOpen(false);
    pushToast({
      title: "Workspace opened",
      message: data.name || path,
      variant: "success"
    });
  };

  const clearWorkspaceMemory = async (workspaceId) => {
    await apiDelete(`/api/workspaces/${workspaceId}/memory`);
    pushToast({
      title: "Workspace memory cleared",
      message: "Repo memory and cached wiki/context were reset",
      variant: "success"
    });
  };

  const value = useMemo(
    () => ({
      workspaces,
      activeWorkspace,
      activeWorkspaceId,
      setActiveWorkspaceId,
      isModalOpen,
      setIsModalOpen,
      openWorkspace,
      clearWorkspaceMemory,
      refreshWorkspaces
    }),
    [
      workspaces,
      activeWorkspace,
      activeWorkspaceId,
      isModalOpen
    ]
  );

  return (
    <WorkspaceContext.Provider value={value}>
      {children}
    </WorkspaceContext.Provider>
  );
}

export function useWorkspace() {
  const ctx = useContext(WorkspaceContext);
  if (!ctx) {
    throw new Error("WorkspaceContext missing");
  }
  return ctx;
}
