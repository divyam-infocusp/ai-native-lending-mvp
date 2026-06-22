import { Routes, Route } from "react-router-dom";
import { RequireRole } from "./auth/AuthContext";
import { Layout } from "./components/Layout";
import { RoleLanding } from "./components/RoleLanding";
import { ApplicantJourney } from "./applicant/ApplicantJourney";
import { PipelineList } from "./lender/PipelineList";
import { ApplicationDetail } from "./lender/ApplicationDetail";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<RoleLanding />} />
      <Route
        path="/apply"
        element={
          <RequireRole role="applicant">
            <Layout>
              <ApplicantJourney />
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
    </Routes>
  );
}
