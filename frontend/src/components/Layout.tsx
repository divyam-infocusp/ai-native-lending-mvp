import { ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

export function Layout({ children }: { children: ReactNode }) {
  const { identity, logout } = useAuth();
  const navigate = useNavigate();

  return (
    <div className="min-h-full">
      <header className="bg-white border-b border-slate-200">
        <div className="max-w-6xl mx-auto px-6 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="font-semibold text-slate-900">AI-Native Lending</span>
            {identity && (
              <span className="text-xs uppercase tracking-wide rounded-full bg-slate-100 px-2 py-0.5 text-slate-500">
                {identity.role}
              </span>
            )}
          </div>
          <div className="flex items-center gap-4 text-sm">
            {identity && <span className="text-slate-500">{identity.name}</span>}
            <button
              onClick={() => {
                logout();
                navigate("/");
              }}
              className="text-slate-500 hover:text-slate-800"
            >
              Switch role
            </button>
          </div>
        </div>
      </header>
      <main className="max-w-6xl mx-auto px-6 py-6">{children}</main>
    </div>
  );
}
