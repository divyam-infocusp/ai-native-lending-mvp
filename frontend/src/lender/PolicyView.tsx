import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, PolicyView as Policy } from "../api/client";
import { Card, Pill, Spinner, ErrorNote } from "../components/ui";

const inr = (n: number) => `₹${Number(n).toLocaleString("en-IN")}`;
const pct = (n: number) => `${+(n * 100).toFixed(2)}%`;

function threshold(t: number | null, unit: string): string {
  if (t === null || t === undefined) return "—";
  if (unit === "₹") return inr(t);
  if (unit === "ratio") return pct(t);
  return unit ? `${t} ${unit}` : String(t);
}

export function PolicyView() {
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getPolicy().then(setPolicy).catch((e) => setError(e.message));
  }, []);

  if (error) return <div className="max-w-3xl mx-auto"><ErrorNote>{error}</ErrorNote></div>;
  if (!policy) return <div className="card p-8 grid place-items-center"><Spinner label="Loading policy…" /></div>;

  return (
    <div className="space-y-5 max-w-4xl">
      <div>
        <Link to="/pipeline" className="text-sm text-brand hover:underline">← Pipeline</Link>
        <div className="flex items-center gap-3 mt-1">
          <h1 className="text-2xl font-semibold text-slate-900">Lending policy</h1>
          <Pill tone="slate">version {policy.version}</Pill>
        </div>
        <p className="text-slate-500 text-sm mt-1">
          The exact thresholds the deterministic engine applies. Hard rules auto-decline and
          cannot be overridden; soft rules refer to an underwriter.
        </p>
      </div>

      <Card title="Eligibility rules">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-slate-500">
              <tr>
                <th className="py-2 pr-4 font-medium">Rule</th>
                <th className="py-2 pr-4 font-medium">Threshold</th>
                <th className="py-2 pr-4 font-medium">Type</th>
                <th className="py-2 font-medium">Reason code</th>
              </tr>
            </thead>
            <tbody>
              {policy.rules.map((r) => (
                <tr key={r.reason_code} className="border-t border-slate-100 align-top">
                  <td className="py-2.5 pr-4">
                    <div className="font-medium text-slate-800">{r.label}</div>
                    <div className="text-xs text-slate-400">{r.description}</div>
                  </td>
                  <td className="py-2.5 pr-4 font-medium text-slate-700 whitespace-nowrap">
                    {r.threshold === null ? "required" : threshold(r.threshold, r.unit)}
                  </td>
                  <td className="py-2.5 pr-4">
                    <Pill tone={r.type === "hard" ? "red" : "amber"}>
                      {r.type === "hard" ? "Hard — decline" : "Soft — refer"}
                    </Pill>
                  </td>
                  <td className="py-2.5 text-xs font-mono text-slate-500">{r.reason_code}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <Card title="Risk bands & pricing">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-slate-500">
                <tr>
                  <th className="py-2 pr-4 font-medium">Band</th>
                  <th className="py-2 pr-4 font-medium">Min score</th>
                  <th className="py-2 pr-4 font-medium">Rate</th>
                  <th className="py-2 font-medium">Max amount</th>
                </tr>
              </thead>
              <tbody>
                {policy.bands.map((b) => (
                  <tr key={b.band} className="border-t border-slate-100">
                    <td className="py-2 pr-4 font-semibold text-slate-800">{b.band}</td>
                    <td className="py-2 pr-4 text-slate-700">{b.min_score}</td>
                    <td className="py-2 pr-4 text-slate-700">{b.rate_pct !== null ? `${b.rate_pct}%` : "—"}</td>
                    <td className="py-2 text-slate-700">{b.max_amount !== null ? inr(b.max_amount) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-slate-400 mt-3">
            Minimum score to lend: {policy.scorecard.min_score} · income haircut applied in the
            sensitivity test: {pct(policy.scorecard.income_haircut_pct)}.
          </p>
        </Card>

        <Card title="Offer terms">
          <dl className="grid grid-cols-2 gap-y-2 text-sm">
            <dt className="text-slate-500">Tenure range</dt>
            <dd className="text-right font-medium">{policy.pricing.tenure_min_months}–{policy.pricing.tenure_max_months} months</dd>
            <dt className="text-slate-500">Affordability DTI cap</dt>
            <dd className="text-right font-medium">{pct(policy.pricing.affordability_dti)}</dd>
            <dt className="text-slate-500">Processing fee</dt>
            <dd className="text-right font-medium">{pct(policy.pricing.processing_fee_pct)}</dd>
            <dt className="text-slate-500">GST on fee</dt>
            <dd className="text-right font-medium">{pct(policy.pricing.gst_pct)}</dd>
            <dt className="text-slate-500">Offer validity</dt>
            <dd className="text-right font-medium">{policy.pricing.offer_validity_days} days</dd>
          </dl>
        </Card>
      </div>

      <Card title="Document verification (KYC)">
        <dl className="grid grid-cols-1 sm:grid-cols-2 gap-y-2 text-sm">
          <dt className="text-slate-500">Min field confidence</dt>
          <dd className="sm:text-right font-medium">{pct(policy.documents.min_confidence)}</dd>
          <dt className="text-slate-500">Min OCR confidence</dt>
          <dd className="sm:text-right font-medium">{pct(policy.documents.min_ocr_conf)}</dd>
          <dt className="text-slate-500">Name match threshold</dt>
          <dd className="sm:text-right font-medium">{pct(policy.documents.name_match_min_ratio)}</dd>
          <dt className="text-slate-500">Income match tolerance</dt>
          <dd className="sm:text-right font-medium">{pct(policy.documents.income_match_tolerance_pct)}</dd>
        </dl>
        <div className="mt-3">
          <div className="text-xs text-slate-500 mb-1">Key fields (must be reliable & agree across documents)</div>
          <div className="flex flex-wrap gap-1.5">
            {policy.documents.key_fields.map((f) => (
              <Pill key={f} tone="slate">{f.replace(/_/g, " ")}</Pill>
            ))}
          </div>
        </div>
      </Card>
    </div>
  );
}
