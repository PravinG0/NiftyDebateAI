import React from 'react';
import { TrendingUp, TrendingDown, Scale } from 'lucide-react';

export default function SentimentMeter({ bullScore = 0, bearScore = 0 }) {
  const bull = Number(bullScore) || 0;
  const bear = Number(bearScore) || 0;
  const total = bull + bear;

  // Calculate percentage shares of the total conviction points
  const bullPct = total > 0 ? Math.round((bull / total) * 100) : 50;
  const bearPct = total > 0 ? 100 - bullPct : 50;

  const diff = bull - bear;
  let advantageText = 'Balanced Conviction';
  let advantageColor = 'text-amber-400';

  if (diff > 1.5) {
    advantageText = `Bull Dominance (+${bull} vs ${bear})`;
    advantageColor = 'text-emerald-400';
  } else if (diff < -1.5) {
    advantageText = `Bear Dominance (+${bear} vs ${bull})`;
    advantageColor = 'text-rose-400';
  } else if (diff !== 0) {
    advantageText = diff > 0 ? `Slight Bull Edge (${bull}/10)` : `Slight Bear Edge (${bear}/10)`;
    advantageColor = diff > 0 ? 'text-emerald-300' : 'text-rose-300';
  }

  return (
    <div className="bg-[#0D131F]/90 backdrop-blur-md border border-slate-800/80 rounded-2xl p-4 sm:p-5 shadow-lg space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-lg bg-indigo-500/10 border border-indigo-500/20 flex items-center justify-center text-indigo-400">
            <Scale size={15} />
          </div>
          <div>
            <h3 className="text-xs sm:text-sm font-bold text-white tracking-wide">
              Conviction Tug-of-War
            </h3>
            <p className="text-[10px] text-slate-400">
              Multi-Agent relative conviction momentum balance
            </p>
          </div>
        </div>

        <span className={`text-xs font-bold font-mono ${advantageColor} tabular-nums`}>
          {advantageText}
        </span>
      </div>

      {/* Conviction Scores & Tug-of-War Bar */}
      <div className="space-y-2">
        <div className="flex items-center justify-between text-xs font-mono font-bold tabular-nums">
          <div className="flex items-center gap-1.5 text-emerald-400">
            <span>🐂 Bull Agent</span>
            <span className="px-1.5 py-0.5 rounded bg-emerald-950/80 border border-emerald-800/60 text-[11px]">
              {bull.toFixed(1)}/10 ({bullPct}%)
            </span>
          </div>

          <div className="flex items-center gap-1.5 text-rose-400">
            <span className="px-1.5 py-0.5 rounded bg-rose-950/80 border border-rose-800/60 text-[11px]">
              ({bearPct}%) {bear.toFixed(1)}/10
            </span>
            <span>Bear Agent 🐻</span>
          </div>
        </div>

        {/* Tug-of-War Split Bar */}
        <div className="relative h-3.5 bg-slate-950 rounded-full overflow-hidden p-0.5 border border-slate-800 flex">
          {/* Bull side */}
          <div
            className="h-full bg-gradient-to-r from-emerald-600 to-emerald-400 rounded-l-full transition-all duration-700 ease-out"
            style={{ width: `${bullPct}%` }}
          />

          {/* Center Dividing Notch */}
          <div className="w-1 h-full bg-white/40 shadow-sm z-10 -ml-0.5" />

          {/* Bear side */}
          <div
            className="h-full bg-gradient-to-l from-rose-600 to-rose-400 rounded-r-full transition-all duration-700 ease-out"
            style={{ width: `${bearPct}%` }}
          />
        </div>
      </div>
    </div>
  );
}
