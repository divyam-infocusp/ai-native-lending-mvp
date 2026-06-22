import { ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { Brand, Pill } from "./ui";

export function Layout({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  return (
    <div className="min-h-full">
      <header className="sticky top-0 z-10 bg-white/80 backdrop-blur border-b border-slate-200">
        <div className="max-w-6xl mx-auto px-6 py-3 flex items-center justify-between">
          <Brand />
          <div className="flex items-center gap-4">
            {user && (
              <div className="flex items-center gap-3">
                <Pill tone={user.role === "underwriter" ? "brand" : "green"}>{user.role}</Pill>
                <div className="text-right leading-tight hidden sm:block">
                  <div className="text-sm font-medium text-slate-700">{user.name}</div>
                  <div className="text-xs text-slate-400">{user.email}</div>
                </div>
              </div>
            )}
            <button
              onClick={() => {
                logout();
                navigate("/login");
              }}
              className="btn-ghost text-sm"
            >
              Sign out
            </button>
          </div>
        </div>
      </header>
      <main className="max-w-6xl mx-auto px-6 py-8">{children}</main>
    </div>
  );
}
