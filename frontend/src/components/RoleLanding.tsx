// Mock role landing (#demo) — pick a role + display name, no credentials.
// This is the single place real authentication would replace: swap the form for
// a login flow and call auth.login() with the authenticated identity.
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth, Role } from "../auth/AuthContext";

export function RoleLanding() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [name, setName] = useState("");

  function enter(role: Role) {
    const display = name.trim() || (role === "applicant" ? "Applicant" : "Underwriter");
    login({ role, name: display });
    navigate(role === "applicant" ? "/apply" : "/pipeline");
  }

  return (
    <div className="min-h-full flex items-center justify-center p-6">
      <div className="w-full max-w-lg bg-white rounded-2xl shadow-sm border border-slate-200 p-8">
        <h1 className="text-2xl font-semibold text-slate-900">AI-Native Lending</h1>
        <p className="mt-1 text-slate-500">Demo — choose how you want to sign in.</p>

        <label className="block mt-6 text-sm font-medium text-slate-700">Display name</label>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Priya Sharma"
          className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand"
        />

        <div className="mt-6 grid grid-cols-2 gap-4">
          <button
            onClick={() => enter("applicant")}
            className="rounded-xl bg-brand hover:bg-brand-dark text-white py-3 font-medium"
          >
            I'm an Applicant
          </button>
          <button
            onClick={() => enter("underwriter")}
            className="rounded-xl bg-slate-800 hover:bg-slate-900 text-white py-3 font-medium"
          >
            I'm an Underwriter
          </button>
        </div>

        <p className="mt-4 text-xs text-slate-400">
          No password — this is a demo role switch. Real authentication slots in here later.
        </p>
      </div>
    </div>
  );
}
