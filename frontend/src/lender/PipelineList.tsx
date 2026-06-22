import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApplicationSummary } from "../api/client";

const STATE_STYLES: Record<string, string> = {
  OFFER_GENERATED: "bg-emerald-100 text-emerald-700",
  OFFER_ACCEPTED: "bg-emerald-100 text-emerald-700",
  DECLINED: "bg-rose-100 text-rose-700",
  LEAD_DECLINED: "bg-rose-100 text-rose-700",
  REFERRED: "bg-amber-100 text-amber-700",
  KYC_EXCEPTION: "bg-amber-100 text-amber-700",
  UW_EXCEPTION: "bg-amber-100 text-amber-700",
  LEAD_EXCEPTION: "bg-amber-100 text-amber-700",
};

export function PipelineList() {
  const [apps, setApps] = useState<ApplicationSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try {
      setApps(await api.listApplications());
      setError(null);
    } catch (e: any) {
      setError(e.message);
    }
  }

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 3000); // live-ish refresh
    return () => clearInterval(t);
  }, []);

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-semibold text-slate-900">Pipeline</h1>
        <button onClick={refresh} className="text-sm text-brand hover:text-brand-dark">
          Refresh
        </button>
      </div>

      {error && <p className="text-sm text-rose-600 mb-3">{error}</p>}

      <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-slate-500 text-left">
            <tr>
              <th className="px-4 py-2 font-medium">Applicant</th>
              <th className="px-4 py-2 font-medium">State</th>
              <th className="px-4 py-2 font-medium">Disposition</th>
              <th className="px-4 py-2 font-medium">Updated</th>
            </tr>
          </thead>
          <tbody>
            {apps.map((a) => (
              <tr key={a.application_id} className="border-t border-slate-100 hover:bg-slate-50">
                <td className="px-4 py-2">
                  <Link to={`/pipeline/${a.application_id}`} className="text-brand hover:underline">
                    {a.applicant_name}
                  </Link>
                  <div className="text-xs text-slate-400">{a.application_id.slice(0, 12)}…</div>
                </td>
                <td className="px-4 py-2">
                  <span
                    className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                      STATE_STYLES[a.workflow_state ?? ""] ?? "bg-slate-100 text-slate-600"
                    }`}
                  >
                    {a.workflow_state ?? "—"}
                  </span>
                </td>
                <td className="px-4 py-2 text-slate-600">{a.disposition ?? "—"}</td>
                <td className="px-4 py-2 text-slate-400 text-xs">
                  {new Date(a.updated_at).toLocaleString()}
                </td>
              </tr>
            ))}
            {apps.length === 0 && (
              <tr>
                <td colSpan={4} className="px-4 py-8 text-center text-slate-400">
                  No applications yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
