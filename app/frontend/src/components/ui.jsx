import { forwardRef } from "react";

const baseInput =
  "w-full rounded-sm border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm outline-none transition focus:border-primary-500 focus:ring-2 focus:ring-primary-500/30 disabled:bg-slate-50 disabled:text-slate-400";

const cx = (...classes) => classes.filter(Boolean).join(" ");

export function Card({ className = "", children }) {
  return (
    <div
      className={cx(
        "rounded-sm border border-slate-200 bg-white shadow-sm",
        className
      )}
    >
      {children}
    </div>
  );
}

export function PanelHeader({ title, subtitle, action, className = "" }) {
  return (
    <div
      className={cx(
        "flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-4 py-2",
        className
      )}
    >
      <div>
        <p className="text-sm font-semibold text-slate-900">{title}</p>
        {subtitle ? (
          <p className="text-[11px] text-slate-500">{subtitle}</p>
        ) : null}
      </div>
      {action ? <div className="flex items-center gap-2">{action}</div> : null}
    </div>
  );
}

export const ButtonPrimary = forwardRef(function ButtonPrimary(
  { className = "", ...props },
  ref
) {
  return (
    <button
      ref={ref}
      className={cx(
        "inline-flex items-center justify-center rounded-sm bg-primary-600 px-3 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-primary-700 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60",
        className
      )}
      {...props}
    />
  );
});

export const ButtonSecondary = forwardRef(function ButtonSecondary(
  { className = "", ...props },
  ref
) {
  return (
    <button
      ref={ref}
      className={cx(
        "inline-flex items-center justify-center rounded-sm border border-primary-200 bg-white px-3 py-2 text-sm font-semibold text-primary-700 shadow-sm transition hover:border-primary-300 hover:text-primary-800 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60",
        className
      )}
      {...props}
    />
  );
});

export const Input = forwardRef(function Input(
  { className = "", ...props },
  ref
) {
  return <input ref={ref} className={cx(baseInput, className)} {...props} />;
});

export const Textarea = forwardRef(function Textarea(
  { className = "", ...props },
  ref
) {
  return (
    <textarea
      ref={ref}
      className={cx(baseInput, "min-h-[96px] resize-none", className)}
      {...props}
    />
  );
});

export const Select = forwardRef(function Select(
  { className = "", ...props },
  ref
) {
  return (
    <select ref={ref} className={cx(baseInput, className)} {...props} />
  );
});

export function Badge({ className = "", children }) {
  return (
    <span
      className={cx(
        "inline-flex items-center rounded-sm border border-slate-200 bg-slate-50 px-2 py-0.5 text-[11px] font-semibold text-slate-600",
        className
      )}
    >
      {children}
    </span>
  );
}
