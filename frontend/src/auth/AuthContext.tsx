// Mock auth (#demo). Deliberately structured so real authentication can replace
// the provider internals WITHOUT touching consumers:
//   - components read identity via useAuth()
//   - route gating goes through <RequireRole>
// To add real auth later: make login() call an auth API + store a token, and
// hydrate identity from that token. The shape below (role + name) stays the same.

import { createContext, useContext, useEffect, useMemo, useState, ReactNode } from "react";
import { Navigate } from "react-router-dom";

export type Role = "applicant" | "underwriter";

export interface Identity {
  role: Role;
  name: string;
}

interface AuthState {
  identity: Identity | null;
  login: (identity: Identity) => void;
  logout: () => void;
}

const AuthContext = createContext<AuthState | undefined>(undefined);
const STORAGE_KEY = "lending.identity";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [identity, setIdentity] = useState<Identity | null>(null);

  // Hydrate from storage (a real impl would validate a token here instead).
  useEffect(() => {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      try {
        setIdentity(JSON.parse(raw));
      } catch {
        /* ignore */
      }
    }
  }, []);

  const value = useMemo<AuthState>(
    () => ({
      identity,
      login: (id) => {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(id));
        setIdentity(id);
      },
      logout: () => {
        localStorage.removeItem(STORAGE_KEY);
        setIdentity(null);
      },
    }),
    [identity],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}

// Route guard. Today it only checks the mock role; a real impl would also verify
// the session/token is valid and redirect to a login flow.
export function RequireRole({ role, children }: { role: Role; children: ReactNode }) {
  const { identity } = useAuth();
  if (!identity) return <Navigate to="/" replace />;
  if (identity.role !== role) return <Navigate to="/" replace />;
  return <>{children}</>;
}
