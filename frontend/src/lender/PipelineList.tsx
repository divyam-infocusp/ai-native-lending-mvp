import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApplicationSummary } from "../api/client";
import { Pill, Stat, Spinner, ErrorNote, stateTone } from "../components/ui";

export function PipelineList() {
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
    const t = setInterval(refresh, 3000);
    return () => clearInterval(t);
  }, []);

  const offers = apps.filter((a) => a.workflow_state === "OFFER_GENERATED").length;
  const review = apps.filter((a) => a.workflow_state?.endsWith("EXCEPTION") || a.workflow_state === "REFERRED").length;
  const declined = apps.filter((a) => (a.workflow_state ?? "").includes("DECLINED")).length;

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Underwriting pipeline</h1>
          <p className="text-slate-500 text-sm mt-0.5">Live view of every application and where it stands.</p>
        </div>
        <button onClick={refresh} className="btn-ghost text-sm">↻ Refresh</button>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <Stat label="Total" value={apps.length} />
        <Stat label="Offers" value={offers} accent="text-emerald-600" />
        <Stat label="In review" value={review} accent="text-amber-600" />
        <Stat label="Declined" value={declined} accent="text-rose-600" />
      </div>

      {error && <ErrorNote>{error}</ErrorNote>}

      <div className="card overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-slate-500 text-left">
            <tr>
              <th className="px-5 py-3 font-medium">Applicant</th>
              <th className="px-5 py-3 font-medium">State</th>
              <th className="px-5 py-3 font-medium">Disposition</th>
              <th className="px-5 py-3 font-medium">Updated</th>
            </tr>
          </thead>
          <tbody>
            {apps.map((a) => (
              <tr key={a.application_id} className="border-t border-slate-100 hover:bg-slate-50/70 transition">
                <td className="px-5 py-3">
                  <Link to={`/pipeline/${a.application_id}`} className="text-brand font-medium hover:underline">
                    {a.applicant_name}
                  </Link>
                  <div className="text-xs text-slate-400 font-mono">{a.application_id.slice(0, 12)}…</div>
                </td>
                <td className="px-5 py-3">
                  <Pill tone={stateTone(a.workflow_state)}>{a.workflow_state ?? "—"}</Pill>
                </td>
                <td className="px-5 py-3 text-slate-600 capitalize">{a.disposition ?? "—"}</td>
                <td className="px-5 py-3 text-slate-400 text-xs">{new Date(a.updated_at).toLocaleString()}</td>
              </tr>
            ))}
            {loaded && apps.length === 0 && (
              <tr><td colSpan={4} className="px-5 py-12 text-center text-slate-400">No applications yet.</td></tr>
            )}
            {!loaded && (
              <tr><td colSpan={4} className="px-5 py-12"><div className="grid place-items-center"><Spinner label="Loading…" /></div></td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
