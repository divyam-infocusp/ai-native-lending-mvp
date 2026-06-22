import { Routes, Route, Navigate } from "react-router-dom";
import { RequireRole, useAuth } from "./auth/AuthContext";
import { AuthPage } from "./components/AuthPage";
import { Layout } from "./components/Layout";
import { ApplicantJourney } from "./applicant/ApplicantJourney";
import { ApplicantHome } from "./applicant/ApplicantHome";
import { ApplicationStatus } from "./applicant/ApplicationStatus";
import { PipelineList } from "./lender/PipelineList";
import { ApplicationDetail } from "./lender/ApplicationDetail";

function Home() {
  const { user, loading } = useAuth();
  if (loading) return <div className="min-h-full grid place-items-center text-slate-400">Loading…</div>;
  if (!user) return <Navigate to="/login" replace />;
  return <Navigate to={user.role === "applicant" ? "/apply" : "/pipeline"} replace />;
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/login" element={<AuthPage mode="login" />} />
      <Route path="/register" element={<AuthPage mode="register" />} />
      <Route
        path="/apply"
        element={
          <RequireRole role="applicant">
            <Layout>
              <ApplicantHome />
            </Layout>
          </RequireRole>
        }
      />
      <Route
        path="/apply/new"
        element={
          <RequireRole role="applicant">
            <Layout>
              <ApplicantJourney />
            </Layout>
          </RequireRole>
        }
      />
      <Route
        path="/apply/:id"
        element={
          <RequireRole role="applicant">
            <Layout>
              <ApplicationStatus />
            </Layout>
          </RequireRole>
        }
      />
      <Route
        path="/pipeline"
        element={
          <RequireRole role="underwriter">
            <Layout>
              <PipelineList />
            </Layout>
          </RequireRole>
        }
      />
      <Route
        path="/pipeline/:id"
        element={
          <RequireRole role="underwriter">
            <Layout>
              <ApplicationDetail />
            </Layout>
          </RequireRole>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
