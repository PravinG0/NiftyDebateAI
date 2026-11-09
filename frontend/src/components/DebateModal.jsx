import React, { useEffect, useCallback } from 'react';
import { X, TrendingUp, TrendingDown, MinusCircle, Target, Shield, AlertTriangle, CheckCircle2, XCircle, Scale, Clock } from 'lucide-react';
import { formatIST } from '../utils/time';

function ConfidenceBar({ score, color }) {
  const percent = Math.min(Math.max((score / 10) * 100, 0), 100);
  const colors = {
    emerald: 'bg-emerald-500',
    red: 'bg-red-500',
  };

  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 bg-slate-800 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full ${colors[color] || 'bg-blue-500'} animate-fill-bar`}
          style={{ width: `${percent}%` }}
        />
      </div>
      <span className="text-xs font-mono font-bold text-slate-300">{score}/10</span>
    </div>
  );
}

export default function DebateModal({ debate, onClose }) {
  const handleKeyDown = useCallback(
    (e) => {
      if (e.key === 'Escape') onClose();
    },
    [onClose]
  );

  useEffect(() => {
    document.addEventListener('keydown', handleKeyDown);
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      document.body.style.overflow = '';
    };
  }, [handleKeyDown]);

  if (!debate) return null;

  const bull = debate.bull || debate.bull_analysis || {};
  const bear = debate.bear || debate.bear_analysis || {};
  const judge = debate.judge || debate.judge_verdict || debate.verdict || {};
  const spotPrice = debate.spot_price || debate.nifty_spot;
  const timestamp = debate.timestamp_ist || debate.timestamp || debate.created_at;
  const indexSymbol = debate.index_symbol || debate.symbol || 'NIFTY';
  const lotSize = indexSymbol === 'BANKNIFTY' ? 15 : 25;

  const verdict = judge.verdict ?? judge.decision ?? judge.action ?? 'NO_TRADE';
  const vUpper = (verdict || '').toUpperCase();
  const isCE = vUpper.includes('CE') || vUpper.includes('CALL');
  const isPE = vUpper.includes('PE') || vUpper.includes('PUT');

  const bullArgs = bull.arguments ?? bull.key_arguments ?? bull.reasons ?? [];
  const bearArgs = bear.arguments ?? bear.key_arguments ?? bear.reasons ?? [];

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-2 sm:p-4 animate-fade-in"
      onClick={onClose}
    >
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" />

      {/* Modal */}
      <div
        className="relative w-full max-w-4xl max-h-[94vh] overflow-y-auto rounded-2xl bg-slate-900 border border-slate-700/60 shadow-2xl shadow-black/40 animate-slide-up"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="sticky top-0 z-10 flex items-center justify-between px-4 sm:px-6 py-3 sm:py-4 border-b border-slate-800 bg-slate-900/95 backdrop-blur-sm">
          <div className="flex items-center gap-2.5 sm:gap-3">
            <Scale size={20} className="text-amber-400 flex-shrink-0" />
            <div>
              <div className="flex flex-wrap items-center gap-1.5 sm:gap-2">
                <h2 className="text-base sm:text-lg font-bold text-white">Debate Details</h2>
                <span className="text-[10px] sm:text-xs font-bold px-1.5 py-0.5 rounded bg-blue-950 text-blue-300 border border-blue-800 font-mono">
                  {indexSymbol === 'BANKNIFTY' ? 'BANK NIFTY' : 'NIFTY 50'} (Lot: {lotSize})
                </span>
              </div>
              <div className="flex flex-wrap items-center gap-2 sm:gap-3 mt-0.5">
                {timestamp && (
                  <span className="text-[11px] sm:text-xs text-slate-400 font-mono flex items-center gap-1">
                    <Clock size={12} className="text-blue-400" />
                    {formatIST(timestamp)}
                  </span>
                )}
                {spotPrice && (
                  <span className="text-[11px] sm:text-xs font-mono text-blue-400">
                    Spot: ₹{Number(spotPrice).toLocaleString('en-IN', { minimumFractionDigits: 2 })}
                  </span>
                )}
              </div>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 sm:p-2 rounded-xl text-slate-400 hover:text-white hover:bg-slate-800 transition-all"
          >
            <X size={18} />
          </button>
        </div>

        <div className="p-4 sm:p-6 space-y-4 sm:space-y-6">
          {/* Verdict Banner */}
          <div className={`flex items-center justify-center gap-3 py-4 rounded-xl border ${
            isCE
              ? 'bg-emerald-950/40 border-emerald-500/30'
              : isPE
                ? 'bg-red-950/40 border-red-500/30'
                : 'bg-amber-950/40 border-amber-500/30'
          }`}>
            {isCE && <TrendingUp size={28} className="text-emerald-400" />}
            {isPE && <TrendingDown size={28} className="text-red-400" />}
            {!isCE && !isPE && <MinusCircle size={28} className="text-amber-400" />}
            <span className={`text-2xl font-extrabold font-mono tracking-wider ${
              isCE ? 'text-emerald-400' : isPE ? 'text-red-400' : 'text-amber-400'
            }`}>
              {verdict}
            </span>
          </div>

          {/* Bull & Bear Side by Side */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* Bull */}
            <div className="rounded-xl bg-emerald-950/30 border border-emerald-500/20 p-4 space-y-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-xl">🐂</span>
                  <h3 className="font-bold text-emerald-300">Bull Agent</h3>
                </div>
                {bull.recommended_strike && (
                  <span className="px-2 py-1 rounded-md bg-emerald-900/50 border border-emerald-500/30 text-xs font-mono font-bold text-emerald-300">
                    {bull.recommended_strike ?? bull.strike} (Lot: {lotSize})
                  </span>
                )}
              </div>
              <ConfidenceBar score={bull.confidence ?? bull.confidence_score ?? 0} color="emerald" />
              {bullArgs.length > 0 && (
                <div className="space-y-2">
                  {bullArgs.map((arg, i) => (
                    <div key={i} className="flex items-start gap-2 text-sm">
                      <CheckCircle2 size={14} className="text-emerald-500 mt-0.5 flex-shrink-0" />
                      <span className="text-slate-300 leading-relaxed">{arg}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Bear */}
            <div className="rounded-xl bg-red-950/30 border border-red-500/20 p-4 space-y-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-xl">🐻</span>
                  <h3 className="font-bold text-red-300">Bear Agent</h3>
                </div>
                {bear.recommended_strike && (
                  <span className="px-2 py-1 rounded-md bg-red-900/50 border border-red-500/30 text-xs font-mono font-bold text-red-300">
                    {bear.recommended_strike ?? bear.strike} (Lot: {lotSize})
                  </span>
                )}
              </div>
              <ConfidenceBar score={bear.confidence ?? bear.confidence_score ?? 0} color="red" />
              {bearArgs.length > 0 && (
                <div className="space-y-2">
                  {bearArgs.map((arg, i) => (
                    <div key={i} className="flex items-start gap-2 text-sm">
                      <XCircle size={14} className="text-red-500 mt-0.5 flex-shrink-0" />
                      <span className="text-slate-300 leading-relaxed">{arg}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* Judge Reasoning */}
          <div className="rounded-xl bg-amber-950/20 border border-amber-500/20 p-4 space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-xl">⚖️</span>
                <h3 className="font-bold text-amber-300">Judge Reasoning</h3>
              </div>
              <span className="text-xs font-mono text-emerald-400 font-semibold">
                1 Lot = {lotSize} Qty (₹5k Budget Guardrail)
              </span>
            </div>

            {/* Trade params */}
            {(judge.entry_price || judge.entry || judge.stop_loss || judge.sl || judge.target_price || judge.target) && (
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                {(judge.entry_price ?? judge.entry) && (
                  <div className="px-3 py-2 rounded-lg bg-slate-800/50">
                    <span className="text-xs text-slate-500">Entry</span>
                    <p className="font-mono font-bold text-blue-300 text-sm">{judge.entry_price ?? judge.entry}</p>
                  </div>
                )}
                {(judge.stop_loss ?? judge.sl) && (
                  <div className="px-3 py-2 rounded-lg bg-slate-800/50">
                    <span className="text-xs text-slate-500 flex items-center gap-1"><Shield size={10} /> Stop Loss</span>
                    <p className="font-mono font-bold text-red-300 text-sm">{judge.stop_loss ?? judge.sl}</p>
                  </div>
                )}
                {(judge.target_price ?? judge.target) && (
                  <div className="px-3 py-2 rounded-lg bg-slate-800/50">
                    <span className="text-xs text-slate-500 flex items-center gap-1"><Target size={10} /> Target</span>
                    <p className="font-mono font-bold text-emerald-300 text-sm">{judge.target_price ?? judge.target}</p>
                  </div>
                )}
                {(judge.risk_reward ?? judge.risk_reward_ratio) && (
                  <div className="px-3 py-2 rounded-lg bg-slate-800/50">
                    <span className="text-xs text-slate-500">R:R</span>
                    <p className="font-mono font-bold text-amber-300 text-sm">1:{judge.risk_reward ?? judge.risk_reward_ratio}</p>
                  </div>
                )}
              </div>
            )}

            {/* Reasoning text */}
            {(judge.reasoning ?? judge.winning_argument ?? judge.summary ?? judge.rationale) && (
              <blockquote className="border-l-2 border-amber-500/50 pl-3 text-sm text-slate-300 leading-relaxed italic">
                {judge.reasoning ?? judge.winning_argument ?? judge.summary ?? judge.rationale}
              </blockquote>
            )}

            {/* Risk warning */}
            {(judge.risk_warning ?? judge.risk_note ?? judge.warning) && (
              <div className="flex items-start gap-2 px-3 py-2 rounded-lg bg-amber-900/20 border border-amber-500/20">
                <AlertTriangle size={14} className="text-amber-500 mt-0.5 flex-shrink-0" />
                <span className="text-xs text-amber-300/80 leading-relaxed">
                  {judge.risk_warning ?? judge.risk_note ?? judge.warning}
                </span>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
