import { Route, Routes } from "react-router-dom";

import AppShell from "./components/AppShell.jsx";
import Home from "./pages/Home.jsx";
import TaskDetail from "./pages/TaskDetail.jsx";
import Tools from "./pages/Tools.jsx";

export default function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/tasks/:taskId" element={<TaskDetail />} />
        <Route path="/tools" element={<Tools />} />
      </Routes>
    </AppShell>
  );
}
