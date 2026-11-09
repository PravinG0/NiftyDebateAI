import React, { useState, useEffect } from 'react';
import { X, Shield, Target, DollarSign, ArrowUpCircle, ArrowDownCircle, Check, Loader2, AlertTriangle, Lock } from 'lucide-react';

export default function ManualTradeModal({
  isOpen,
  onClose,
  selectedIndex = 'NIFTY',
  initialStrike = 24500,
  initialType = 'CE',
  initialPrice = 120.0,
  spotPrice = 24500.0,
  onTradePlaced,
}) {
  const lotSize = selectedIndex === 'BANKNIFTY' ? 15 : 25;
  const strikeStep = selectedIndex === 'BANKNIFTY' ? 100 : 50;
  const budgetLimit = 5000.0;
  const maxStopLossINR = 700.0;
  const maxTargetINR = 1000.0;

  // Strict 1-lot fixed guardrail
  const lots = 1;
  const quantity = lots * lotSize;

  // Max points allowed for SL and Target based on lot size
  const maxSlPoints = Math.floor((maxStopLossINR / quantity) * 100) / 100;
  const maxTargetPoints = Math.floor((maxTargetINR / quantity) * 100) / 100;

  const [strike, setStrike] = useState(initialStrike);
  const [optionType, setOptionType] = useState(initialType);
  const [price, setPrice] = useState(initialPrice);
  const [stopLoss, setStopLoss] = useState(Math.max(1, Math.round((initialPrice - maxSlPoints) * 10) / 10));
  const [target, setTarget] = useState(Math.round((initialPrice + maxTargetPoints) * 10) / 10);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  const totalAmount = Math.round(price * quantity * 100) / 100;
  const maxRisk = Math.max(0, Math.round((price - stopLoss) * quantity * 100) / 100);
  const expectedProfit = Math.max(0, Math.round((target - price) * quantity * 100) / 100);

  const exceedsBudget = totalAmount > budgetLimit;
  const exceedsRisk = maxRisk > (maxStopLossINR + 0.1);
  const exceedsTarget = expectedProfit > (maxTargetINR + 0.1);

  useEffect(() => {
    const p = parseFloat(initialPrice) || 120.0;
    const q = (selectedIndex === 'BANKNIFTY' ? 15 : 25);
    const slPts = Math.floor((maxStopLossINR / q) * 100) / 100;
    const tgtPts = Math.floor((maxTargetINR / q) * 100) / 100;

    setStrike(initialStrike);
    setOptionType(initialType);
    setPrice(p);
    setStopLoss(Math.max(1, Math.round((p - slPts) * 10) / 10));
    setTarget(Math.round((p + tgtPts) * 10) / 10);
    setError(null);
  }, [initialStrike, initialType, initialPrice, selectedIndex, isOpen]);

  if (!isOpen) return null;

  const handlePriceChange = (val) => {
    const p = parseFloat(val) || 0;
    setPrice(p);
    setStopLoss(Math.max(1, Math.round((p - maxSlPoints) * 10) / 10));
    setTarget(Math.round((p + maxTargetPoints) * 10) / 10);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (price <= 0) {
      setError('Price must be greater than 0');
      return;
    }
    if (exceedsBudget) {
      setError(`Trade outlay ₹${totalAmount.toFixed(2)} exceeds your ₹${budgetLimit} capital budget.`);
      return;
    }
    if (exceedsRisk) {
      setError(`Stop loss risk ₹${maxRisk.toFixed(2)} exceeds maximum ₹${maxStopLossINR} limit.`);
      return;
    }
    if (exceedsTarget) {
      setError(`Target profit ₹${expectedProfit.toFixed(2)} exceeds maximum ₹${maxTargetINR} limit.`);
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      await onTradePlaced({
        symbol: selectedIndex,
        strike: parseInt(strike, 10),
        option_type: optionType,
        lots: 1, // Strictly 1 lot enforced
        entry_price: parseFloat(price),
        stop_loss: parseFloat(stopLoss),
        target: parseFloat(target),
      });
      onClose();
    } catch (err) {
      setError(err.response?.data?.detail || err.message || 'Failed to place manual trade');
    } finally {
      setSubmitting(false);
    }
  };

  const isCE = optionType === 'CE';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-2 sm:p-4 bg-black/80 backdrop-blur-sm overflow-y-auto">
      <div className="relative w-full max-w-lg bg-slate-900 border border-slate-800 rounded-2xl shadow-2xl overflow-hidden my-auto max-h-[94vh] flex flex-col">
        {/* Header Bar */}
        <div className={`px-4 sm:px-6 py-3 sm:py-4 flex items-center justify-between border-b border-slate-800 flex-shrink-0 ${
          isCE ? 'bg-emerald-950/60' : 'bg-rose-950/60'
        }`}>
          <div className="flex items-center gap-3">
            {isCE ? (
              <div className="w-8 h-8 sm:w-9 sm:h-9 rounded-xl bg-emerald-600 flex items-center justify-center text-white flex-shrink-0">
                <ArrowUpCircle size={20} />
              </div>
            ) : (
              <div className="w-8 h-8 sm:w-9 sm:h-9 rounded-xl bg-rose-600 flex items-center justify-center text-white flex-shrink-0">
                <ArrowDownCircle size={20} />
              </div>
            )}
            <div>
              <div className="flex flex-wrap items-center gap-1.5 sm:gap-2">
                <h3 className="text-sm sm:text-base font-bold text-white font-mono">
                  {selectedIndex === 'BANKNIFTY' ? 'BANK NIFTY' : 'NIFTY'} {strike} {optionType}
                </h3>
                <span className={`px-2 py-0.5 rounded text-[10px] sm:text-[11px] font-bold ${
                  isCE ? 'bg-emerald-600 text-white' : 'bg-rose-600 text-white'
                }`}>
                  {isCE ? 'CALL' : 'PUT'}
                </span>
                <span className="px-1.5 sm:px-2 py-0.5 rounded text-[10px] font-bold bg-slate-800 text-slate-300 border border-slate-700">
                  1 Lot
                </span>
              </div>
              <p className="text-[11px] sm:text-xs text-slate-400 mt-0.5">
                Spot: ₹{Number(spotPrice).toLocaleString('en-IN')} • 1 Lot = {lotSize} Qty • Max SL ₹700 • Max Target ₹1,000
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 transition-colors"
          >
            <X size={18} />
          </button>
        </div>

        {/* Form Body - scrollable on mobile */}
        <form onSubmit={handleSubmit} className="p-4 sm:p-6 space-y-4 overflow-y-auto flex-1">
          {error && (
            <div className="p-3 text-xs bg-rose-950 border border-rose-800 rounded-lg text-rose-200 flex items-center gap-2">
              <AlertTriangle size={15} className="text-rose-400 flex-shrink-0" />
              <span>{error}</span>
            </div>
          )}

          {/* Budget Warning Banner if Exceeded */}
          {exceedsBudget && (
            <div className="p-2.5 sm:p-3 text-xs bg-amber-950 border border-amber-700 rounded-lg text-amber-200 flex items-center gap-2">
              <AlertTriangle size={16} className="text-amber-400 flex-shrink-0" />
              <div>
                <span className="font-bold">Budget Guardrail:</span> Outlay ₹{totalAmount.toFixed(2)} exceeds ₹{budgetLimit.toLocaleString('en-IN')}. Choose an OTM strike or lower entry.
              </div>
            </div>
          )}

          {/* Risk Limits Banner if Exceeded */}
          {(exceedsRisk || exceedsTarget) && (
            <div className="p-2.5 sm:p-3 text-xs bg-rose-950 border border-rose-800 rounded-lg text-rose-200 space-y-1">
              {exceedsRisk && (
                <div className="flex items-center gap-2">
                  <AlertTriangle size={14} className="text-rose-400 flex-shrink-0" />
                  <span>Max Stop-Loss is ₹{maxStopLossINR}. Current risk: ₹{maxRisk.toFixed(2)}. Minimum allowed SL is ₹{Math.max(1, price - maxSlPoints).toFixed(2)}.</span>
                </div>
              )}
              {exceedsTarget && (
                <div className="flex items-center gap-2">
                  <AlertTriangle size={14} className="text-amber-400 flex-shrink-0" />
                  <span>Max Target profit is ₹{maxTargetINR}. Current profit: ₹{expectedProfit.toFixed(2)}. Maximum allowed Target is ₹{(price + maxTargetPoints).toFixed(2)}.</span>
                </div>
              )}
            </div>
          )}

          {/* Type Toggle */}
          <div className="grid grid-cols-2 gap-2 p-1 bg-slate-950 rounded-xl border border-slate-800">
            <button
              type="button"
              onClick={() => setOptionType('CE')}
              className={`py-2 text-xs font-bold rounded-lg transition-all flex items-center justify-center gap-1.5 ${
                isCE ? 'bg-emerald-600 text-white' : 'text-slate-400 hover:text-white'
              }`}
            >
              <ArrowUpCircle size={14} />
              BUY CE (Call)
            </button>
            <button
              type="button"
              onClick={() => setOptionType('PE')}
              className={`py-2 text-xs font-bold rounded-lg transition-all flex items-center justify-center gap-1.5 ${
                !isCE ? 'bg-rose-600 text-white' : 'text-slate-400 hover:text-white'
              }`}
            >
              <ArrowDownCircle size={14} />
              BUY PE (Put)
            </button>
          </div>

          {/* Strike and Lots */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4">
            <div>
              <label className="block text-xs font-semibold text-slate-400 mb-1">
                Strike Price (Step: {strikeStep})
              </label>
              <input
                type="number"
                step={strikeStep}
                value={strike}
                onChange={(e) => setStrike(e.target.value)}
                className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-white font-mono text-sm focus:outline-none focus:border-blue-500"
              />
            </div>
            <div>
              <label className="block text-xs font-semibold text-slate-400 mb-1 flex items-center justify-between">
                <span>Lot Size (Locked)</span>
                <span className="text-[10px] text-emerald-400 font-bold flex items-center gap-1">
                  <Lock size={10} /> 1 Lot Max
                </span>
              </label>
              <div className="flex items-center px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-white font-mono text-sm">
                <span className="font-bold text-slate-200">1 Lot ({lotSize} Qty)</span>
                <span className="ml-auto text-[10px] px-2 py-0.5 rounded bg-blue-950 text-blue-300 border border-blue-800 font-sans">
                  ₹5k Budget
                </span>
              </div>
            </div>
          </div>

          {/* Price, SL, Target - Stack on mobile, 3 columns on tablet/desktop */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <div className="flex items-center justify-between mb-1">
                <label className="text-xs font-semibold text-slate-400">Entry LTP (₹)</label>
              </div>
              <input
                type="number"
                step="0.05"
                min="0.5"
                value={price}
                onChange={(e) => handlePriceChange(e.target.value)}
                className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-white font-mono text-sm focus:outline-none focus:border-blue-500"
              />
            </div>
            <div>
              <div className="flex items-center justify-between mb-1">
                <label className="text-xs font-semibold text-slate-400">Stop Loss (₹)</label>
                <span className="text-[10px] text-rose-400 font-mono">Max -₹700</span>
              </div>
              <input
                type="number"
                step="0.05"
                min="0.5"
                value={stopLoss}
                onChange={(e) => setStopLoss(parseFloat(e.target.value) || 0)}
                className={`w-full px-3 py-2 bg-slate-950 border rounded-lg font-mono text-sm focus:outline-none ${
                  exceedsRisk ? 'border-rose-500 text-rose-400' : 'border-slate-800 text-rose-300 focus:border-rose-500'
                }`}
              />
            </div>
            <div>
              <div className="flex items-center justify-between mb-1">
                <label className="text-xs font-semibold text-slate-400">Target (₹)</label>
                <span className="text-[10px] text-emerald-400 font-mono">Max +₹1k</span>
              </div>
              <input
                type="number"
                step="0.05"
                min="0.5"
                value={target}
                onChange={(e) => setTarget(parseFloat(e.target.value) || 0)}
                className={`w-full px-3 py-2 bg-slate-950 border rounded-lg font-mono text-sm focus:outline-none ${
                  exceedsTarget ? 'border-amber-500 text-amber-400' : 'border-slate-800 text-emerald-300 focus:border-emerald-500'
                }`}
              />
            </div>
          </div>

          {/* Sizing, Guardrails & Budget Summary */}
          <div className="p-3.5 rounded-xl bg-slate-950 border border-slate-800 space-y-2 text-xs">
            <div className="flex items-center justify-between">
              <span className="text-slate-400">Total Capital Outlay:</span>
              <span className={`font-mono font-bold ${exceedsBudget ? 'text-amber-400' : 'text-white'}`}>
                ₹{totalAmount.toLocaleString('en-IN', { minimumFractionDigits: 2 })}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-slate-400">Stop-Loss Risk (Max ₹700):</span>
              <span className={`font-mono font-semibold ${exceedsRisk ? 'text-rose-500 font-bold' : 'text-rose-400'}`}>
                -₹{maxRisk.toLocaleString('en-IN', { minimumFractionDigits: 2 })}
                {exceedsRisk && ' (Exceeds ₹700!)'}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-slate-400">Target Profit (Max ₹1,000):</span>
              <span className={`font-mono font-semibold ${exceedsTarget ? 'text-amber-500 font-bold' : 'text-emerald-400'}`}>
                +₹{expectedProfit.toLocaleString('en-IN', { minimumFractionDigits: 2 })}
                {exceedsTarget && ' (Exceeds ₹1,000!)'}
              </span>
            </div>
            <div className="pt-2 border-t border-slate-800 flex items-center justify-between text-[11px] text-slate-400">
              <span>Risk:Reward Ratio</span>
              <span className="font-mono font-bold text-blue-300">
                1 : {maxRisk > 0 ? (expectedProfit / maxRisk).toFixed(2) : '1.43'}
              </span>
            </div>
          </div>

          {/* Submit & Cancel Buttons */}
          <div className="flex flex-col sm:flex-row gap-2.5 pt-1">
            <button
              type="button"
              onClick={onClose}
              className="w-full sm:flex-1 py-2.5 px-4 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs sm:text-sm font-semibold transition-colors order-2 sm:order-1"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting || exceedsBudget || exceedsRisk || exceedsTarget}
              className={`w-full sm:flex-1 py-2.5 px-4 rounded-xl text-white text-xs sm:text-sm font-bold shadow transition-colors flex items-center justify-center gap-2 order-1 sm:order-2 ${
                submitting || exceedsBudget || exceedsRisk || exceedsTarget
                  ? 'bg-slate-800 text-slate-500 cursor-not-allowed border border-slate-700'
                  : isCE
                  ? 'bg-emerald-600 hover:bg-emerald-500'
                  : 'bg-rose-600 hover:bg-rose-500'
              }`}
            >
              {submitting ? (
                <>
                  <Loader2 size={16} className="animate-spin" />
                  <span>Placing Trade…</span>
                </>
              ) : exceedsBudget ? (
                <span>Exceeds ₹5k Budget</span>
              ) : exceedsRisk ? (
                <span>SL Risk Exceeds ₹700</span>
              ) : exceedsTarget ? (
                <span>Target Exceeds ₹1,000</span>
              ) : (
                <>
                  <Check size={16} />
                  <span>Place 1-Lot Paper Trade</span>
                </>
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
