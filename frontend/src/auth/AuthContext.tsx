// Real authentication (#38). Identity comes from the backend: login/register
// return a bearer token (stored client-side); on load we hydrate from /auth/me.
// Route gating goes through <RequireRole>.

import { createContext, useContext, useEffect, useMemo, useState, ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { api, AuthUser, Role, tokenStore } from "../api/client";

interface AuthState {
  user: AuthUser | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<AuthUser>;
  register: (email: string, password: string, name: string, role: Role) => Promise<AuthUser>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);

  // Hydrate from a stored token on first load.
  useEffect(() => {
    if (!tokenStore.get()) {
      setLoading(false);
      return;
    }
    api
      .me()
      .then(setUser)
      .catch(() => tokenStore.clear())
      .finally(() => setLoading(false));
  }, []);

  // Re-sync identity with the (shared, cross-tab) token whenever the tab regains
  // focus. The token lives in localStorage — shared across tabs — while `user` is
  // per-tab in-memory state. Without this, a tab can show one user while its token
  // silently belongs to another (e.g. a second tab logged in as a different role),
  // letting an applicant's action run under an underwriter's token. Re-validating
  // on focus makes the UI reflect the real session so the route guard can react.
  useEffect(() => {
    function resync() {
      const token = tokenStore.get();
      if (!token) {
        setUser(null);
        return;
      }
      api.me().then(setUser).catch(() => {
        tokenStore.clear();
        setUser(null);
      });
    }
    function onVisible() {
      if (!document.hidden) resync();
    }
    window.addEventListener("focus", resync);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("focus", resync);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  const value = useMemo<AuthState>(
    () => ({
      user,
      loading,
      login: async (email, password) => {
        const { token, user } = await api.login(email, password);
        tokenStore.set(token);
        setUser(user);
        return user;
      },
      register: async (email, password, name, role) => {
        const { token, user } = await api.register(email, password, name, role);
        tokenStore.set(token);
        setUser(user);
        return user;
      },
      logout: () => {
        tokenStore.clear();
        setUser(null);
      },
    }),
    [user, loading],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}

export function RequireRole({ role, children }: { role: Role; children: ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return <div className="min-h-full grid place-items-center text-slate-400">Loading…</div>;
  if (!user) return <Navigate to="/login" replace state={{ from: location }} />;
  if (user.role !== role) {
    // Signed in as the other role — send them to their own home.
    return <Navigate to={user.role === "applicant" ? "/apply" : "/pipeline"} replace />;
  }
  return <>{children}</>;
}
