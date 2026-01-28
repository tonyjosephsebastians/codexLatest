import { Navigate, Route, Routes } from "react-router-dom";

import AppShell from "./components/AppShell.jsx";
import { ToastProvider } from "./context/ToastContext.jsx";
import { WorkspaceProvider } from "./context/WorkspaceContext.jsx";
import Editor from "./pages/Editor.jsx";
import TaskDetail from "./pages/TaskDetail.jsx";
import Wiki from "./pages/Wiki.jsx";

export default function App() {
  return (
    <ToastProvider>
      <WorkspaceProvider>
        <AppShell>
          <Routes>
            <Route path="/" element={<Navigate to="/wiki" replace />} />
            <Route path="/wiki" element={<Wiki />} />
            <Route path="/editor" element={<Editor />} />
            <Route path="/tasks/:taskId" element={<TaskDetail />} />
          </Routes>
        </AppShell>
      </WorkspaceProvider>
    </ToastProvider>
  );
}
