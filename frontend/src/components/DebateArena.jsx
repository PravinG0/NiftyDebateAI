import React, { useState, useEffect } from 'react';
import BullCard from './BullCard';
import BearCard from './BearCard';
import JudgeCard from './JudgeCard';
import SentimentMeter from './SentimentMeter';
import DebateVoting from './DebateVoting';
import DebateStepper from './DebateStepper';
import { Brain, Clock, Bookmark, BookmarkCheck, Share2 } from 'lucide-react';
import { formatIST } from '../utils/time';

export const SAVED_DEBATES_KEY = 'niftydebate_saved_sessions';

export default function DebateArena({
  debate,
  loading,
  runningDebate = false,
  onSelectTrade,
  selectedIndex = 'NIFTY',
  onPromptAuth,
}) {
  const [isSaved, setIsSaved] = useState(false);
  const [copyNotice, setCopyNotice] = useState(false);

  const debateId = debate?.id || debate?.session_id;

  // Check if current debate is bookmarked
  useEffect(() => {
    if (!debateId) {
      setIsSaved(false);
      return;
    }
    try {
      const saved = JSON.parse(localStorage.getItem(SAVED_DEBATES_KEY) || '[]');
      setIsSaved(saved.some((d) => (d.id || d.session_id) === debateId));
    } catch {
      setIsSaved(false);
    }
  }, [debateId]);

  const toggleSaveDebate = () => {
    if (!debate) return;
    try {
      const saved = JSON.parse(localStorage.getItem(SAVED_DEBATES_KEY) || '[]');
      const currentId = debate.id || debate.session_id;
      const index = saved.findIndex((d) => (d.id || d.session_id) === currentId);

      let updated;
      if (index >= 0) {
        updated = saved.filter((d) => (d.id || d.session_id) !== currentId);
        setIsSaved(false);
      } else {
        updated = [debate, ...saved].slice(0, 30);
        setIsSaved(true);
      }
      localStorage.setItem(SAVED_DEBATES_KEY, JSON.stringify(updated));
    } catch (err) {
      console.error('Failed to bookmark debate:', err);
    }
  };

  const handleShare = () => {
    if (typeof navigator !== 'undefined' && navigator.clipboard) {
      navigator.clipboard.writeText(window.location.href);
      setCopyNotice(true);
      setTimeout(() => setCopyNotice(false), 2000);
    }
  };

  if (loading && !debate) {
    return (
      <div className="flex flex-col items-center justify-center py-24 space-y-3">
        <div className="w-10 h-10 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
        <p className="text-xs text-slate-400 font-medium">Loading AI Debate Session...</p>
      </div>
    );
  }

  const bullData = debate?.bull || debate?.bull_analysis || {};
  const bearData = debate?.bear || debate?.bear_analysis || {};
  const judgeData = debate?.judge || debate?.judge_verdict || debate?.verdict || {};
  const spotPrice = debate?.spot_price || debate?.nifty_spot;
  const timestamp = debate?.timestamp_ist || debate?.timestamp || debate?.created_at;
  const indexSymbol = debate?.index_symbol || debate?.symbol || selectedIndex || 'NIFTY';
  const lotSize = indexSymbol === 'BANKNIFTY' ? 15 : 25;

  const bullConfidence = bullData.confidence ?? bullData.confidence_score ?? 0;
  const bearConfidence = bearData.confidence ?? bearData.confidence_score ?? 0;

  return (
    <div className="space-y-4">
      {/* Live Stepper when debate is running */}
      <DebateStepper isRunning={runningDebate} selectedIndex={indexSymbol} />

      {!debate ? (
        <div className="animate-card-appear bg-[#0D131F]/90 backdrop-blur-md border border-slate-800/80 rounded-2xl p-12 text-center">
          <div className="flex flex-col items-center gap-4">
            <div className="w-16 h-16 rounded-2xl bg-blue-600/10 border border-blue-500/20 flex items-center justify-center text-blue-400 shadow-inner">
              <Brain size={32} />
            </div>
            <div>
              <h3 className="text-lg font-bold text-slate-200">No Active AI Debate Yet</h3>
              <p className="text-xs text-slate-400 mt-1.5 max-w-md mx-auto leading-relaxed">
                Click <span className="text-blue-400 font-semibold">"Trigger AI Debate"</span> above to launch a 3-agent adversarial deliberation between Bull, Bear, and Judge agents based on live Angel One SmartAPI exchange candles.
              </p>
            </div>
          </div>
        </div>
      ) : (
        <>
          {/* Debate Header Ribbon */}
          <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2.5 px-1">
            <div className="flex flex-wrap items-center gap-2 sm:gap-3">
              <h2 className="text-xs sm:text-sm font-bold text-slate-300 uppercase tracking-wider flex items-center gap-1.5">
                <Brain size={16} className="text-blue-400" />
                <span>AI Debate Arena</span>
              </h2>

              <span className="text-[11px] font-bold px-2.5 py-0.5 rounded-lg bg-blue-950/80 text-blue-300 border border-blue-800/60 font-mono">
                {indexSymbol === 'BANKNIFTY' ? 'BANK NIFTY' : 'NIFTY 50'} (Lot: {lotSize})
              </span>

              {timestamp && (
                <span className="text-[11px] text-slate-400 font-mono flex items-center gap-1">
                  <Clock size={12} className="text-blue-400" />
                  {formatIST(timestamp)}
                </span>
              )}
            </div>

            <div className="flex items-center gap-2">
              {spotPrice && (
                <span className="text-xs font-mono text-slate-400 mr-1 tabular-nums">
                  Spot: <span className="text-blue-400 font-bold">₹{Number(spotPrice).toLocaleString('en-IN', { minimumFractionDigits: 2 })}</span>
                </span>
              )}

              {/* Bookmark Debate Button */}
              <button
                onClick={toggleSaveDebate}
                className={`flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-semibold border transition-all ${
                  isSaved
                    ? 'bg-amber-950/60 border-amber-500/80 text-amber-300'
                    : 'bg-slate-900 border-slate-800 text-slate-400 hover:text-slate-200'
                }`}
                title={isSaved ? 'Bookmarked in My Dashboard' : 'Bookmark this debate'}
              >
                {isSaved ? <BookmarkCheck size={13} className="text-amber-400" /> : <Bookmark size={13} />}
                <span>{isSaved ? 'Saved' : 'Save'}</span>
              </button>

              {/* Share Button */}
              <button
                onClick={handleShare}
                className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-semibold bg-slate-900 border border-slate-800 text-slate-400 hover:text-slate-200 transition-colors"
                title="Copy share link"
              >
                <Share2 size={13} />
                <span>{copyNotice ? 'Copied!' : 'Share'}</span>
              </button>
            </div>
          </div>

          {/* Conviction Tug-of-War Meter */}
          <SentimentMeter
            bullScore={bullConfidence}
            bearScore={bearConfidence}
          />

          {/* Three-Column Debate Layout */}
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

          {/* Community Consensus & Voting Widget */}
          <DebateVoting
            debateId={debateId}
            onPromptAuth={onPromptAuth}
          />
        </>
      )}
    </div>
  );
}
