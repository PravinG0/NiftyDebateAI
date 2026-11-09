import React from 'react';
import { CheckCircle2, Target, TrendingUp, ShoppingCart } from 'lucide-react';

function ConfidenceBar({ score, color = 'emerald' }) {
  const percent = Math.min(Math.max((score / 10) * 100, 0), 100);
  const colorMap = {
    emerald: { bar: 'bg-emerald-500', text: 'text-emerald-400' },
    red: { bar: 'bg-rose-500', text: 'text-rose-400' },
    amber: { bar: 'bg-amber-500', text: 'text-amber-400' },
  };
  const c = colorMap[color] || colorMap.emerald;

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-slate-400">Conviction Score</span>
        <span className={`text-base font-bold font-mono ${c.text}`}>{score}<span className="text-xs text-slate-500">/10</span></span>
      </div>
      <div className="h-2 bg-slate-800 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full ${c.bar}`}
          style={{ width: `${percent}%` }}
        />
      </div>
    </div>
  );
}

export default function BullCard({ data, onSelectTrade, lotSize = 25, indexSymbol = 'NIFTY' }) {
  const confidence = data.confidence ?? data.confidence_score ?? 0;
  const rawStrike = data.recommended_strike ?? data.strike ?? data.strike_price;
  const strikeNum = rawStrike ? parseInt(String(rawStrike).replace(/\D/g, ''), 10) : null;
  const args = data.arguments ?? data.key_arguments ?? data.reasons ?? [];
  const structuredArgs = data.structured_arguments ?? null;
  const keyLevels = data.key_levels ?? data.levels ?? null;
  const highConfidence = confidence >= 8;
  const defaultStrike = indexSymbol === 'BANKNIFTY' ? 52000 : 24500;

  return (
    <div
      className={`
        bg-slate-900 border ${highConfidence ? 'border-emerald-500' : 'border-slate-800'}
        rounded-2xl overflow-hidden shadow-lg transition-all
      `}
    >
      {/* Solid green accent bar */}
      <div className="h-1.5 bg-emerald-500" />

      <div className="p-5 space-y-4">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-2xl">🐂</span>
            <div>
              <h3 className="font-bold text-white text-base">Bull Agent</h3>
              <p className="text-[11px] text-emerald-400 font-medium">CE Call Option Advocate</p>
            </div>
          </div>
          <div className="p-2 rounded-lg bg-emerald-950/60 border border-emerald-800/50">
            <TrendingUp size={18} className="text-emerald-400" />
          </div>
        </div>

        {/* Confidence */}
        <ConfidenceBar score={confidence} color="emerald" />

        {/* Recommended Strike & Quick Buy */}
        {rawStrike && (
          <div className="p-3 rounded-xl bg-slate-950 border border-slate-800 flex items-center justify-between">
            <div>
              <span className="text-[11px] uppercase font-semibold text-slate-400">Advocated Contract</span>
              <div className="flex items-center gap-2 mt-0.5">
                <Target size={14} className="text-emerald-400" />
                <span className="font-mono font-bold text-emerald-400 text-sm">
                  {rawStrike}
                </span>
                <span className="text-[11px] font-semibold text-slate-400">
                  (Lot: {lotSize})
                </span>
              </div>
            </div>

            {onSelectTrade && (
              <button
                onClick={() =>
                  onSelectTrade({
                    symbol: indexSymbol,
                    strike: strikeNum || defaultStrike,
                    optionType: 'CE',
                    price: 120,
                    lotSize,
                  })
                }
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-bold transition-colors"
                title={`Paper Trade this ${indexSymbol} CE contract`}
              >
                <ShoppingCart size={13} />
                <span>Trade CE</span>
              </button>
            )}
          </div>
        )}

        {/* Structured Multi-Dimensional Arguments */}
        {structuredArgs && typeof structuredArgs === 'object' && Object.keys(structuredArgs).length > 0 ? (
          <div className="space-y-3">
            <div className="flex items-center justify-between border-b border-slate-800 pb-1.5">
              <span className="text-xs font-semibold uppercase text-slate-400 tracking-wider">
                Full Technical Arguments ({Object.values(structuredArgs).flat().length} Points)
              </span>
            </div>
            <div className="space-y-2.5 max-h-[380px] overflow-y-auto pr-1">
              {Object.entries(structuredArgs).map(([category, points]) => (
                <div key={category} className="space-y-1">
                  <span className="text-[10px] font-bold uppercase tracking-wider text-emerald-400 bg-emerald-950/60 px-2 py-0.5 rounded border border-emerald-900/60">
                    {category.replace(/_/g, ' ')}
                  </span>
                  {(Array.isArray(points) ? points : [points]).map((point, idx) => (
                    <div key={idx} className="flex items-start gap-2 text-xs pl-1">
                      <CheckCircle2 size={13} className="text-emerald-400 mt-0.5 flex-shrink-0" />
                      <span className="text-slate-300 leading-relaxed">{point}</span>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          </div>
        ) : args.length > 0 ? (
          <div className="space-y-2">
            <div className="flex items-center justify-between border-b border-slate-800 pb-1.5">
              <span className="text-xs font-semibold uppercase text-slate-400 tracking-wider">
                Full Technical Arguments ({args.length} Points)
              </span>
            </div>
            <div className="space-y-2 max-h-[340px] overflow-y-auto pr-1">
              {args.map((arg, i) => (
                <div key={i} className="flex items-start gap-2 text-xs">
                  <CheckCircle2 size={14} className="text-emerald-400 mt-0.5 flex-shrink-0" />
                  <span className="text-slate-300 leading-relaxed">{arg}</span>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {/* Key Levels */}
        {keyLevels && (
          <div className="space-y-1.5 pt-1 border-t border-slate-800">
            <span className="text-xs font-semibold text-slate-400">Key Levels</span>
            <div className="grid grid-cols-2 gap-2">
              {Object.entries(keyLevels).map(([label, value]) => (
                <div key={label} className="flex justify-between items-center px-2.5 py-1.5 rounded-lg bg-slate-950 border border-slate-800">
                  <span className="text-[11px] text-slate-400 capitalize">{label.replace(/_/g, ' ')}</span>
                  <span className="text-xs font-mono font-bold text-emerald-400">{value}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
