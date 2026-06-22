// The §4 origination flow as labelled lanes with connectors. Each node shows its
// status: done (visited), active (current), or upcoming. Read-only.

interface Lane {
  label: string;
  states: string[];
}

const LANES: Lane[] = [
  { label: "Lead", states: ["LEAD", "LEAD_QUALIFIED"] },
  { label: "Application", states: ["APPLICATION_SUBMITTED"] },
  { label: "KYC", states: ["KYC_IN_PROGRESS", "KYC_VERIFIED"] },
  { label: "Underwriting", states: ["UNDERWRITING", "DECISION_READY"] },
  { label: "Decision", states: ["APPROVED", "REFERRED", "DECLINED"] },
  { label: "Offer", states: ["OFFER_GENERATED", "OFFER_ACCEPTED"] },
];

const EXCEPTIONS = ["LEAD_EXCEPTION", "LEAD_DECLINED", "KYC_EXCEPTION", "UW_EXCEPTION", "OFFER_EXPIRED"];

function nodeClass(state: string, current: string | null, visited: Set<string>): string {
  if (state === current) return "bg-brand text-white border-brand shadow-glow scale-105";
  if (visited.has(state)) return "bg-emerald-50 text-emerald-700 border-emerald-300";
  return "bg-white text-slate-400 border-slate-200";
}

function Node({ state, current, visited }: { state: string; current: string | null; visited: Set<string> }) {
  const done = visited.has(state) && state !== current;
  return (
    <div className={`rounded-lg border px-2.5 py-1.5 text-[11px] font-medium whitespace-nowrap transition ${nodeClass(state, current, visited)}`}>
      {done && "✓ "}
      {state}
    </div>
  );
}

export function StateGraph({ current, visited }: { current: string | null; visited: Set<string> }) {
  return (
    <div className="space-y-2.5">
      {LANES.map((lane, i) => (
        <div key={lane.label} className="flex items-center gap-3">
          <div className="w-24 shrink-0 text-xs text-slate-400 text-right">{lane.label}</div>
          <div className="flex items-center gap-1.5 flex-wrap">
            {lane.states.map((s, j) => (
              <div key={s} className="flex items-center gap-1.5">
                <Node state={s} current={current} visited={visited} />
                {j < lane.states.length - 1 && <span className="text-slate-300 text-xs">→</span>}
              </div>
            ))}
          </div>
        </div>
      ))}

      <div className="pt-2.5 mt-1 border-t border-slate-100 flex items-start gap-3">
        <div className="w-24 shrink-0 text-xs text-slate-400 text-right pt-1.5">Exceptions</div>
        <div className="flex flex-wrap gap-1.5">
          {EXCEPTIONS.map((s) => (
            <Node key={s} state={s} current={current} visited={visited} />
          ))}
        </div>
      </div>
    </div>
  );
}
