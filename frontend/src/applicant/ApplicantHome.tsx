import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApplicationSummary } from "../api/client";
import { Pill, Spinner, ErrorNote, stateTone } from "../components/ui";

function label(a: ApplicationSummary): { tone: string; text: string } {
  if (!a.workflow_state) return { tone: "slate", text: "Draft — not submitted" };
  return { tone: stateTone(a.workflow_state), text: a.workflow_state };
}

export function ApplicantHome() {
  const [apps, setApps] = useState<ApplicationSummary[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try {
      setApps(await api.listApplications());
      setError(null);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoaded(true);
    }
  }

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 4000);   // reflect live progress of in-process apps
    return () => clearInterval(t);
  }, []);

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Your applications</h1>
          <p className="text-slate-500 text-sm mt-0.5">Track progress, view offers, or start a new one.</p>
        </div>
        <Link to="/apply/new" className="btn-primary">+ New application</Link>
      </div>

      {error && <ErrorNote>{error}</ErrorNote>}

      {!loaded ? (
        <div className="card p-8 grid place-items-center"><Spinner label="Loading…" /></div>
      ) : apps.length === 0 ? (
        <div className="card p-10 text-center">
          <p className="text-slate-500">You haven't applied yet.</p>
          <Link to="/apply/new" className="btn-primary mt-4">Start your first application →</Link>
        </div>
      ) : (
        <div className="space-y-3">
          {apps.map((a) => {
            const l = label(a);
            return (
              <Link
                key={a.application_id}
                to={`/apply/${a.application_id}`}
                className="card px-5 py-4 flex items-center justify-between hover:shadow-glow transition"
              >
                <div>
                  <div className="font-medium text-slate-800">Personal loan application</div>
                  <div className="text-xs text-slate-400 font-mono">{a.application_id.slice(0, 14)}…</div>
                  <div className="text-xs text-slate-400 mt-0.5">Updated {new Date(a.updated_at).toLocaleString()}</div>
                </div>
                <Pill tone={l.tone}>{l.text}</Pill>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
