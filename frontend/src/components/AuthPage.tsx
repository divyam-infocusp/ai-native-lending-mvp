import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { Role } from "../api/client";
import { Brand, ErrorNote } from "./ui";

export function AuthPage({ mode }: { mode: "login" | "register" }) {
  const { login, register } = useAuth();
  const navigate = useNavigate();
  const isRegister = mode === "register";

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState<Role>("applicant");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const user = isRegister
        ? await register(email, password, name, role)
        : await login(email, password);
      navigate(user.role === "applicant" ? "/apply" : "/pipeline", { replace: true });
    } catch (err: any) {
      setError(err.message ?? "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-full grid lg:grid-cols-2">
      {/* Brand panel */}
      <div className="hidden lg:flex flex-col justify-between p-12 bg-gradient-to-br from-brand-600 via-brand-700 to-indigo-900 text-white">
        <Brand className="[&_span]:text-white" />
        <div>
          <h1 className="text-4xl font-semibold leading-tight">
            Lending, reimagined with AI.
          </h1>
          <p className="mt-4 text-brand-100 max-w-md">
            A loan from inquiry to offer in minutes — a conversational application, instant
            verification, and a transparent decision you can trust.
          </p>
          <ul className="mt-8 space-y-2 text-brand-100 text-sm">
            <li>• Conversational onboarding copilot</li>
            <li>• Grounded KYC &amp; underwriting</li>
            <li>• Explainable, auditable decisions</li>
          </ul>
        </div>
        <p className="text-brand-200/70 text-xs">Demo environment · mock adapters</p>
      </div>

      {/* Form panel */}
      <div className="flex items-center justify-center p-6">
        <div className="w-full max-w-sm">
          <div className="lg:hidden mb-8">
            <Brand />
          </div>
          <h2 className="text-2xl font-semibold text-slate-900">
            {isRegister ? "Create your account" : "Welcome back"}
          </h2>
          <p className="text-slate-500 mt-1 mb-6">
            {isRegister ? "Start your application in minutes." : "Sign in to continue."}
          </p>

          <form onSubmit={submit} className="space-y-3">
            {isRegister && (
              <div>
                <label className="text-sm font-medium text-slate-700">Full name</label>
                <input className="field mt-1" value={name} onChange={(e) => setName(e.target.value)} placeholder="Priya Sharma" />
              </div>
            )}
            <div>
              <label className="text-sm font-medium text-slate-700">Email</label>
              <input className="field mt-1" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" />
            </div>
            <div>
              <label className="text-sm font-medium text-slate-700">Password</label>
              <input className="field mt-1" type="password" required value={password} onChange={(e) => setPassword(e.target.value)} placeholder="••••••••" />
            </div>

            {isRegister && (
              <div>
                <label className="text-sm font-medium text-slate-700">I am an…</label>
                <div className="mt-1 grid grid-cols-2 gap-2">
                  {(["applicant", "underwriter"] as Role[]).map((r) => (
                    <button
                      type="button"
                      key={r}
                      onClick={() => setRole(r)}
                      className={`rounded-xl border px-3 py-2 text-sm font-medium capitalize transition ${
                        role === r
                          ? "border-brand bg-brand-50 text-brand-700"
                          : "border-slate-300 text-slate-600 hover:bg-slate-50"
                      }`}
                    >
                      {r}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {error && <ErrorNote>{error}</ErrorNote>}

            <button type="submit" disabled={busy} className="btn-primary w-full !mt-5">
              {busy ? "Please wait…" : isRegister ? "Create account" : "Sign in"}
            </button>
          </form>

          <p className="mt-6 text-sm text-slate-500 text-center">
            {isRegister ? (
              <>Already have an account? <a href="/login" className="text-brand font-medium hover:underline">Sign in</a></>
            ) : (
              <>New here? <a href="/register" className="text-brand font-medium hover:underline">Create an account</a></>
            )}
          </p>
        </div>
      </div>
    </div>
  );
}
