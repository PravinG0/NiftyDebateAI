import React from 'react';
import {
  ArrowUpCircle,
  ArrowDownCircle,
  MinusCircle,
  AlertTriangle,
  Scale,
  Target,
  Shield,
  Zap,
} from 'lucide-react';

function VerdictBadge({ verdict }) {
  const v = (verdict || '').toUpperCase();

  if (v.includes('CE') || v.includes('CALL') || v.includes('BUY_CE')) {
    return (
      <div className="flex items-center gap-2">
        <ArrowUpCircle size={26} className="text-emerald-400" />
        <span className="text-xl font-black text-emerald-400 font-mono tracking-wider">BUY CALL (CE)</span>
      </div>
    );
  }
  if (v.includes('PE') || v.includes('PUT') || v.includes('BUY_PE')) {
    return (
      <div className="flex items-center gap-2">
        <ArrowDownCircle size={26} className="text-rose-400" />
        <span className="text-xl font-black text-rose-400 font-mono tracking-wider">BUY PUT (PE)</span>
      </div>
    );
  }
  return (
    <div className="flex items-center gap-2">
      <MinusCircle size={26} className="text-amber-400" />
      <span className="text-xl font-black text-amber-400 font-mono tracking-wider">NO TRADE</span>
    </div>
  );
}

function TradeParam({ label, price, icon: Icon, highlight, lotSize = 25 }) {
  const p = parseFloat(price);
  const isValid = !isNaN(p) && p > 0;
  const totalAmount = isValid ? Math.round(p * lotSize) : null;

  return (
    <div className="flex items-center justify-between px-3 py-2 rounded-xl bg-slate-950 border border-slate-800">
      <div className="flex items-center gap-2">
        {Icon && <Icon size={14} className="text-slate-400" />}
        <span className="text-xs font-semibold text-slate-400">{label}</span>
      </div>
      <div className="text-right">
        <span className={`text-sm font-bold font-mono ${highlight || 'text-slate-200'}`}>
          ₹{isValid ? p.toFixed(2) : (price ?? '—')}
        </span>
        {totalAmount != null && (
          <div className="text-[10px] text-slate-400 font-mono">
            ₹{totalAmount.toLocaleString('en-IN')} (1 Lot)
          </div>
        )}
      </div>
    </div>
  );
}

export default function JudgeCard({ data, onSelectTrade, lotSize = 25, indexSymbol = 'NIFTY' }) {
  const verdict = data.verdict ?? data.decision ?? data.action ?? 'NO_TRADE';
  const entry = data.entry_price ?? data.entry;
  const sl = data.stop_loss ?? data.sl;
  const target = data.target_price ?? data.target;
  const rr = data.risk_reward ?? data.risk_reward_ratio;
  const reasoning = data.reasoning ?? data.winning_argument ?? data.summary ?? data.rationale;
  const riskWarning = data.risk_warning ?? data.risk_note ?? data.warning;
  const rawStrike = data.strike ?? data.recommended_strike ?? data.strike_price;
  const strikeNum = rawStrike ? parseInt(String(rawStrike).replace(/\D/g, ''), 10) : null;

  const isBuyCE = verdict.toUpperCase().includes('BUY_CE') || verdict.toUpperCase().includes('CE');
  const isBuyPE = verdict.toUpperCase().includes('BUY_PE') || verdict.toUpperCase().includes('PE');
  const isNoTrade = !isBuyCE && !isBuyPE;

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-2xl overflow-hidden shadow-lg transition-all">
      {/* Solid amber accent bar */}
      <div className="h-1.5 bg-amber-500" />

      <div className="p-5 space-y-4">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-2xl">⚖️</span>
            <div>
              <h3 className="font-bold text-white text-base">Judge & Risk Manager</h3>
              <p className="text-[11px] text-amber-400 font-medium">Chief Risk Officer Verdict</p>
            </div>
          </div>
          <div className="p-2 rounded-lg bg-amber-950/60 border border-amber-800/50">
            <Scale size={18} className="text-amber-400" />
          </div>
        </div>

        {/* Verdict Box */}
        <div className="flex flex-col items-center justify-center py-3.5 px-4 rounded-xl bg-slate-950 border border-slate-800 text-center">
          <VerdictBadge verdict={verdict} />
          {rawStrike && !isNoTrade && (
            <div className="flex items-center gap-2 mt-2 text-xs font-semibold text-slate-300">
              <Target size={14} className="text-amber-400" />
              <span>Strike Selected:</span>
              <span className="font-mono font-bold text-white px-2 py-0.5 rounded bg-slate-800 border border-slate-700">
                {rawStrike}
              </span>
            </div>
          )}
        </div>

        {/* Trade Parameters with Angel One Total Amount */}
        {!isNoTrade && (entry || sl || target) && (
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold uppercase text-slate-400">Order Parameters</span>
              <span className="text-[10px] text-emerald-400 font-mono font-bold">1 Lot = {lotSize} Qty (₹5k Budget)</span>
            </div>
            <div className="grid gap-1.5">
              {entry && <TradeParam label="Entry LTP" price={entry} lotSize={lotSize} highlight="text-blue-300" />}
              {sl && <TradeParam label="Stop Loss" price={sl} lotSize={lotSize} icon={Shield} highlight="text-rose-400" />}
              {target && <TradeParam label="Target" price={target} lotSize={lotSize} icon={Target} highlight="text-emerald-400" />}
              {rr && (
                <div className="flex items-center justify-between px-3 py-2 rounded-xl bg-slate-950 border border-slate-800">
                  <span className="text-xs font-semibold text-slate-400">Risk : Reward</span>
                  <span className="text-xs font-bold font-mono text-amber-400">
                    {String(rr).startsWith('1:') ? rr : `1 : ${rr}`}
                  </span>
                </div>
              )}
            </div>

            {/* Execute Judge Trade Button */}
            {onSelectTrade && (
              <button
                onClick={() =>
                  onSelectTrade({
                    symbol: indexSymbol,
                    strike: strikeNum || (indexSymbol === 'BANKNIFTY' ? 52000 : 24500),
                    optionType: isBuyCE ? 'CE' : 'PE',
                    price: parseFloat(entry) || 120,
                    stopLoss: parseFloat(sl) || 90,
                    target: parseFloat(target) || 160,
                    lotSize: lotSize,
                  })
                }
                className="w-full mt-2 py-2.5 px-4 rounded-xl font-bold text-xs flex items-center justify-center gap-2 bg-amber-500 hover:bg-amber-400 text-slate-950 shadow-md transition-colors"
              >
                <Zap size={14} />
                <span>Paper Trade Judge Pick (1 Lot {indexSymbol})</span>
              </button>
            )}
          </div>
        )}

        {/* Reasoning */}
        {reasoning && (
          <div className="space-y-1">
            <span className="text-xs font-semibold uppercase text-slate-400">Verdict Rationale</span>
            <blockquote className="border-l-2 border-amber-500 pl-3 py-1 text-xs text-slate-300 leading-relaxed italic bg-slate-950/60 rounded-r-lg">
              {reasoning}
            </blockquote>
          </div>
        )}

        {/* Risk Warning */}
        {riskWarning && (
          <div className="flex items-start gap-2 px-3 py-2.5 rounded-xl bg-amber-950/40 border border-amber-600/30">
            <AlertTriangle size={15} className="text-amber-400 mt-0.5 flex-shrink-0" />
            <span className="text-xs text-amber-200/90 leading-relaxed">{riskWarning}</span>
          </div>
        )}
      </div>
    </div>
  );
}
