import React from 'react';
import BullCard from './BullCard';
import BearCard from './BearCard';
import JudgeCard from './JudgeCard';
import { Brain, Clock } from 'lucide-react';
import { formatIST } from '../utils/time';

export default function DebateArena({ debate, loading, onSelectTrade, selectedIndex = 'NIFTY' }) {
  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin-slow" />
      </div>
    );
  }

  if (!debate) {
    return (
      <div className="animate-card-appear bg-slate-900 border border-slate-800 rounded-2xl p-12 text-center">
        <div className="flex flex-col items-center gap-4">
          <div className="w-16 h-16 rounded-2xl bg-slate-800 border border-slate-700 flex items-center justify-center">
            <Brain size={32} className="text-slate-500" />
          </div>
          <div>
            <h3 className="text-lg font-semibold text-slate-300">No Analysis Yet</h3>
            <p className="text-sm text-slate-500 mt-1 max-w-md mx-auto">
              Run an AI analysis to see the bull vs bear debate. The AI agents will analyze current market conditions
              and provide their trading recommendations for {selectedIndex === 'BANKNIFTY' ? 'Bank Nifty' : 'Nifty 50'}.
            </p>
          </div>
        </div>
      </div>
    );
  }

  const bullData = debate.bull || debate.bull_analysis || {};
  const bearData = debate.bear || debate.bear_analysis || {};
  const judgeData = debate.judge || debate.judge_verdict || debate.verdict || {};
  const spotPrice = debate.spot_price || debate.nifty_spot;
  const timestamp = debate.timestamp_ist || debate.timestamp || debate.created_at;
  const indexSymbol = debate.index_symbol || debate.symbol || selectedIndex || 'NIFTY';
  const lotSize = indexSymbol === 'BANKNIFTY' ? 15 : 25;

  return (
    <div className="space-y-3">
      {/* Debate Header */}
      <div className="flex items-center justify-between px-1">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider">
            AI Debate Arena
          </h2>
          <span className="text-xs font-bold px-2 py-0.5 rounded bg-blue-950 text-blue-300 border border-blue-800 font-mono">
            {indexSymbol === 'BANKNIFTY' ? 'BANK NIFTY' : 'NIFTY 50'} (Lot: {lotSize})
          </span>
          {timestamp && (
            <span className="text-xs text-slate-400 font-mono flex items-center gap-1">
              <Clock size={12} className="text-blue-400" />
              {formatIST(timestamp)}
            </span>
          )}
        </div>
        {spotPrice && (
          <span className="text-xs font-mono text-slate-400">
            Spot: <span className="text-blue-400 font-bold">₹{Number(spotPrice).toLocaleString('en-IN', { minimumFractionDigits: 2 })}</span>
          </span>
        )}
      </div>

      {/* Three-column debate layout */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <BullCard
          data={bullData}
          onSelectTrade={onSelectTrade}
          lotSize={lotSize}
          indexSymbol={indexSymbol}
        />
        <BearCard
          data={bearData}
          onSelectTrade={onSelectTrade}
          lotSize={lotSize}
          indexSymbol={indexSymbol}
        />
        <JudgeCard
          data={judgeData}
          onSelectTrade={onSelectTrade}
          lotSize={lotSize}
          indexSymbol={indexSymbol}
        />
      </div>
    </div>
  );
}
