import { ToastProvider } from "./context/ToastContext.jsx";
import { WorkspaceProvider } from "./context/WorkspaceContext.jsx";
import CodexDesktop from "./pages/CodexDesktop.jsx";
import { ConfigProvider } from "antd";

export default function App() {
  return (
    <ConfigProvider
      theme={{
        token: {
          colorPrimary: "#54B949",
          colorBgLayout: "#0d2a20",
          colorBgContainer: "#ffffff",
          colorBorder: "#d7e0db",
          borderRadius: 10,
          colorText: "#0f172a",
          colorTextSecondary: "#4b5563",
          fontFamily: "Manrope, system-ui, sans-serif",
        },
      }}
    >
      <ToastProvider>
        <WorkspaceProvider>
          <CodexDesktop />
        </WorkspaceProvider>
      </ToastProvider>
    </ConfigProvider>
  );
}
