import React from 'react';
import {
  Compass,
  TrendingUp,
  TrendingDown,
  ShieldAlert,
  ShieldCheck,
  Layers,
  Activity,
  Globe,
  Building2,
  Anchor,
} from 'lucide-react';

export default function InstitutionalMatrix({ snapshot, selectedIndex = 'NIFTY' }) {
  if (!snapshot) return null;

  const spot = Number(snapshot.spot_price || snapshot.nifty_spot || 0);
  const cprPivot = snapshot.cpr_pivot ? Number(snapshot.cpr_pivot) : null;
  const cprBc = snapshot.cpr_bc ? Number(snapshot.cpr_bc) : null;
  const cprTc = snapshot.cpr_tc ? Number(snapshot.cpr_tc) : null;
  const cprWidthType = snapshot.cpr_width_type || 'NARROW_TRENDING';

  const supertrendDir = (snapshot.supertrend_direction || 'BULLISH').toUpperCase();
  const supertrendVal = snapshot.supertrend_value ? Number(snapshot.supertrend_value) : null;

  const maxPain = snapshot.max_pain;
  const callWall = snapshot.call_oi_wall;
  const putWall = snapshot.put_oi_wall;

  const adRatio = snapshot.advance_decline_ratio != null ? Number(snapshot.advance_decline_ratio).toFixed(2) : '1.20';
  const heavyweights = snapshot.heavyweights_alignment || 'BULLISH';
  const fii = snapshot.fii_sentiment || 'NEUTRAL';
  const globalSentiment = snapshot.global_sentiment || 'POSITIVE';

  // Determine CPR positioning
  let cprPosition = 'Inside CPR (Range)';
  let cprColor = 'text-amber-400 border-amber-800/40 bg-amber-950/20';
  if (cprTc != null && spot > cprTc) {
    cprPosition = 'Above TC (Bullish Breakout)';
    cprColor = 'text-emerald-400 border-emerald-800/40 bg-emerald-950/20';
  } else if (cprBc != null && spot < cprBc) {
    cprPosition = 'Below BC (Bearish Breakdown)';
    cprColor = 'text-rose-400 border-rose-800/40 bg-rose-950/20';
  }

  return (
    <div className="bg-[#0D131F]/90 backdrop-blur-md border border-slate-800/80 rounded-2xl p-4 sm:p-5 shadow-xl space-y-3.5">
      {/* Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2 border-b border-slate-800/80 pb-3">
        <div className="flex items-center gap-2">
          <div className="p-1.5 rounded-lg bg-blue-600/20 text-blue-400 border border-blue-500/30">
            <Compass size={16} />
          </div>
          <div>
            <h3 className="text-xs sm:text-sm font-bold text-slate-200 uppercase tracking-wider">
              Institutional Market Matrix & Key Levels
            </h3>
            <p className="text-[11px] text-slate-400">
              Smart Money Open Interest Walls, CPR Range & Institutional Breadth Confluence
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <span className="text-[10px] font-mono uppercase px-2 py-0.5 rounded-md bg-blue-950 text-blue-300 border border-blue-800">
            {selectedIndex === 'BANKNIFTY' ? 'BANK NIFTY' : 'NIFTY 50'}
          </span>
          <span className={`text-[10px] font-bold px-2 py-0.5 rounded-md border ${cprColor}`}>
            {cprPosition}
          </span>
        </div>
      </div>

      {/* Grid of Institutional Indicator Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2.5 sm:gap-3">
        {/* 1. Central Pivot Range (CPR) */}
        <div className="p-3 rounded-xl bg-[#080C14] border border-slate-800/90 space-y-1.5">
          <div className="flex items-center justify-between text-slate-400 text-[11px]">
            <span className="font-semibold flex items-center gap-1">
              <Layers size={13} className="text-blue-400" />
              CPR Pivot Range
            </span>
            <span className="text-[9px] font-mono px-1 py-0.2 rounded bg-slate-800 text-slate-300">
              {cprWidthType === 'NARROW_TRENDING' ? 'Narrow' : 'Wide'}
            </span>
          </div>

          <div className="font-mono text-xs space-y-0.5">
            <div className="flex items-center justify-between text-slate-400">
              <span>TC:</span>
              <span className="text-slate-200 font-semibold">{cprTc != null ? `₹${cprTc.toFixed(2)}` : '—'}</span>
            </div>
            <div className="flex items-center justify-between text-blue-300 font-bold">
              <span>Pivot:</span>
              <span>{cprPivot != null ? `₹${cprPivot.toFixed(2)}` : '—'}</span>
            </div>
            <div className="flex items-center justify-between text-slate-400">
              <span>BC:</span>
              <span className="text-slate-200 font-semibold">{cprBc != null ? `₹${cprBc.toFixed(2)}` : '—'}</span>
            </div>
          </div>
        </div>

        {/* 2. Supertrend (10, 3) */}
        <div className="p-3 rounded-xl bg-[#080C14] border border-slate-800/90 space-y-1.5">
          <div className="flex items-center justify-between text-slate-400 text-[11px]">
            <span className="font-semibold flex items-center gap-1">
              <Activity size={13} className="text-cyan-400" />
              Supertrend (10, 3)
            </span>
            <span
              className={`text-[9px] font-bold px-1.5 py-0.2 rounded ${
                supertrendDir === 'BULLISH'
                  ? 'bg-emerald-950 text-emerald-300 border border-emerald-800'
                  : 'bg-rose-950 text-rose-300 border border-rose-800'
              }`}
            >
              {supertrendDir}
            </span>
          </div>

          <div className="space-y-1">
            <span className="text-[10px] text-slate-400 uppercase font-semibold">Trailing SL Stop</span>
            <div className="font-mono text-sm font-bold text-slate-100">
              {supertrendVal != null ? `₹${supertrendVal.toFixed(2)}` : '—'}
            </div>
            <div className="text-[10px] text-slate-400 font-mono">
              {spot && supertrendVal ? `${Math.abs(spot - supertrendVal).toFixed(1)} pts buffer` : ''}
            </div>
          </div>
        </div>

        {/* 3. OI Resistance & Support Walls */}
        <div className="p-3 rounded-xl bg-[#080C14] border border-slate-800/90 space-y-1.5">
          <div className="flex items-center justify-between text-slate-400 text-[11px]">
            <span className="font-semibold flex items-center gap-1">
              <ShieldAlert size={13} className="text-amber-400" />
              Institutional Walls
            </span>
            <span className="text-[9px] font-mono px-1 py-0.2 rounded bg-amber-950 text-amber-300 border border-amber-900/50">
              Max OI
            </span>
          </div>

          <div className="font-mono text-xs space-y-1">
            <div className="flex items-center justify-between">
              <span className="text-[10px] text-rose-400 font-semibold">Call Wall (Res):</span>
              <span className="text-rose-300 font-bold">{callWall ? `${callWall} CE` : '—'}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-[10px] text-emerald-400 font-semibold">Put Wall (Sup):</span>
              <span className="text-emerald-300 font-bold">{putWall ? `${putWall} PE` : '—'}</span>
            </div>
            <div className="flex items-center justify-between text-purple-300">
              <span className="text-[10px] text-purple-400 font-semibold">Max Pain:</span>
              <span className="font-bold">{maxPain ? `${maxPain}` : '—'}</span>
            </div>
          </div>
        </div>

        {/* 4. Heavyweights & Market Breadth */}
        <div className="p-3 rounded-xl bg-[#080C14] border border-slate-800/90 space-y-1.5">
          <div className="flex items-center justify-between text-slate-400 text-[11px]">
            <span className="font-semibold flex items-center gap-1">
              <Building2 size={13} className="text-indigo-400" />
              Constituents Breadth
            </span>
            <span
              className={`text-[9px] font-bold px-1.5 py-0.2 rounded ${
                heavyweights === 'BULLISH'
                  ? 'bg-emerald-950 text-emerald-300'
                  : heavyweights === 'BEARISH'
                  ? 'bg-rose-950 text-rose-300'
                  : 'bg-slate-800 text-slate-300'
              }`}
            >
              {heavyweights}
            </span>
          </div>

          <div className="font-mono text-xs space-y-1">
            <div className="flex items-center justify-between text-slate-400">
              <span>A/D Ratio:</span>
              <span className="text-slate-100 font-bold">{adRatio}</span>
            </div>
            <div className="flex items-center justify-between text-slate-400">
              <span>FII Net Flow:</span>
              <span
                className={`font-semibold ${
                  fii === 'NET_BUYER'
                    ? 'text-emerald-400'
                    : fii === 'NET_SELLER'
                    ? 'text-rose-400'
                    : 'text-slate-300'
                }`}
              >
                {fii}
              </span>
            </div>
            <div className="flex items-center justify-between text-slate-400">
              <span>Global Cues:</span>
              <span
                className={`font-semibold ${
                  globalSentiment === 'POSITIVE'
                    ? 'text-emerald-400'
                    : globalSentiment === 'NEGATIVE'
                    ? 'text-rose-400'
                    : 'text-slate-300'
                }`}
              >
                {globalSentiment}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
