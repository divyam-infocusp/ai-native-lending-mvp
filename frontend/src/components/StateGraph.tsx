// The §4 origination state machine, laid out as connected nodes with the current
// state highlighted and visited states marked. Read-only.

const ROWS: string[][] = [
  ["LEAD", "LEAD_QUALIFIED", "APPLICATION_SUBMITTED"],
  ["KYC_IN_PROGRESS", "KYC_VERIFIED"],
  ["UNDERWRITING", "DECISION_READY"],
  ["APPROVED", "REFERRED", "DECLINED"],
  ["OFFER_GENERATED", "OFFER_ACCEPTED"],
];

const EXCEPTIONS = ["LEAD_EXCEPTION", "LEAD_DECLINED", "KYC_EXCEPTION", "UW_EXCEPTION", "OFFER_EXPIRED"];

function nodeClass(state: string, current: string | null, visited: Set<string>): string {
  if (state === current) return "bg-brand text-white border-brand shadow";
  if (visited.has(state)) return "bg-emerald-50 text-emerald-700 border-emerald-300";
  return "bg-white text-slate-400 border-slate-200";
}

export function StateGraph({
  current,
  visited,
}: {
  current: string | null;
  visited: Set<string>;
}) {
  const Node = ({ state }: { state: string }) => (
    <div
      className={`rounded-lg border px-3 py-1.5 text-xs font-medium whitespace-nowrap ${nodeClass(
        state,
        current,
        visited,
      )}`}
    >
      {state}
    </div>
  );

  return (
    <div className="space-y-3">
      {ROWS.map((row, i) => (
        <div key={i} className="flex flex-wrap items-center gap-2">
          {row.map((state, j) => (
            <div key={state} className="flex items-center gap-2">
              <Node state={state} />
              {j < row.length - 1 && <span className="text-slate-300">→</span>}
            </div>
          ))}
        </div>
      ))}

      <div className="pt-3 border-t border-slate-100">
        <div className="text-xs text-slate-400 mb-2">Exception / terminal states</div>
        <div className="flex flex-wrap gap-2">
          {EXCEPTIONS.map((state) => (
            <Node key={state} state={state} />
          ))}
        </div>
      </div>
    </div>
  );
}
