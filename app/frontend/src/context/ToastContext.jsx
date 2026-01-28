import { createContext, useCallback, useContext, useMemo, useState } from "react";

const ToastContext = createContext(null);

const buildToast = (toast) => ({
  id: crypto.randomUUID(),
  title: toast.title,
  message: toast.message,
  variant: toast.variant || "default"
});

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);

  const pushToast = useCallback((toast) => {
    const next = buildToast(toast);
    setToasts((prev) => [...prev, next]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((item) => item.id !== next.id));
    }, 4000);
  }, []);

  const value = useMemo(() => ({ pushToast }), [pushToast]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed right-6 top-6 z-50 space-y-3">
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className={`pointer-events-auto rounded-md border px-4 py-2 text-sm shadow-lg transition ${
              toast.variant === "success"
                ? "border-primary-200 bg-primary-50 text-primary-800"
                : toast.variant === "error"
                  ? "border-red-200 bg-red-50 text-red-700"
                  : "border-slate-200 bg-white text-slate-700"
            }`}
          >
            <p className="font-semibold">{toast.title}</p>
            {toast.message ? (
              <p className="mt-1 text-xs text-slate-600">{toast.message}</p>
            ) : null}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    throw new Error("ToastContext missing");
  }
  return ctx;
}
