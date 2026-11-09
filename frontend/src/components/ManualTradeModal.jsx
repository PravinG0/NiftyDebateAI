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
  const [strike, setStrike] = useState(initialStrike);
  const [optionType, setOptionType] = useState(initialType);
  const [price, setPrice] = useState(initialPrice);
  const [stopLoss, setStopLoss] = useState(Math.round(Math.max(1, initialPrice * 0.75)));
  const [target, setTarget] = useState(Math.round(initialPrice * 1.50));
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  const lotSize = selectedIndex === 'BANKNIFTY' ? 15 : 25;
  const strikeStep = selectedIndex === 'BANKNIFTY' ? 100 : 50;
  const budgetLimit = 5000.0;

  // Strict 1-lot fixed guardrail
  const lots = 1;
  const quantity = lots * lotSize;
  const totalAmount = Math.round(price * quantity * 100) / 100;
  const slAmount = Math.round(stopLoss * quantity * 100) / 100;
  const targetAmount = Math.round(target * quantity * 100) / 100;
  const maxRisk = Math.max(0, Math.round((price - stopLoss) * quantity * 100) / 100);
  const expectedProfit = Math.max(0, Math.round((target - price) * quantity * 100) / 100);

  const exceedsBudget = totalAmount > budgetLimit;

  useEffect(() => {
    setStrike(initialStrike);
    setOptionType(initialType);
    setPrice(initialPrice);
    setStopLoss(Math.round(Math.max(1, initialPrice * 0.75)));
    setTarget(Math.round(initialPrice * 1.50));
    setError(null);
  }, [initialStrike, initialType, initialPrice, isOpen]);

  if (!isOpen) return null;

  const handlePriceChange = (val) => {
    const p = parseFloat(val) || 0;
    setPrice(p);
    setStopLoss(Math.round(Math.max(1, p * 0.75)));
    setTarget(Math.round(p * 1.50));
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
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm">
      <div className="relative w-full max-w-lg bg-slate-900 border border-slate-800 rounded-2xl shadow-2xl overflow-hidden">
        {/* Header Bar */}
        <div className={`px-6 py-4 flex items-center justify-between border-b border-slate-800 ${
          isCE ? 'bg-emerald-950/60' : 'bg-rose-950/60'
        }`}>
          <div className="flex items-center gap-3">
            {isCE ? (
              <div className="w-9 h-9 rounded-xl bg-emerald-600 flex items-center justify-center text-white">
                <ArrowUpCircle size={22} />
              </div>
            ) : (
              <div className="w-9 h-9 rounded-xl bg-rose-600 flex items-center justify-center text-white">
                <ArrowDownCircle size={22} />
              </div>
            )}
            <div>
              <div className="flex items-center gap-2">
                <h3 className="text-base font-bold text-white font-mono">
                  {selectedIndex === 'BANKNIFTY' ? 'BANK NIFTY' : 'NIFTY'} {strike} {optionType}
                </h3>
                <span className={`px-2 py-0.5 rounded text-[11px] font-bold ${
                  isCE ? 'bg-emerald-600 text-white' : 'bg-rose-600 text-white'
                }`}>
                  {isCE ? 'CALL' : 'PUT'}
                </span>
                <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-slate-800 text-slate-300 border border-slate-700">
                  1 Lot Fixed
                </span>
              </div>
              <p className="text-xs text-slate-400">
                Spot: ₹{Number(spotPrice).toLocaleString('en-IN')} • 1 Lot = {lotSize} Qty • Budget Guardrail ₹5,000
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

        {/* Form Body */}
        <form onSubmit={handleSubmit} className="p-6 space-y-5">
          {error && (
            <div className="p-3 text-xs bg-rose-950 border border-rose-800 rounded-lg text-rose-200 flex items-center gap-2">
              <AlertTriangle size={15} className="text-rose-400 flex-shrink-0" />
              <span>{error}</span>
            </div>
          )}

          {/* Budget Warning Banner if Exceeded */}
          {exceedsBudget && (
            <div className="p-3 text-xs bg-amber-950 border border-amber-700 rounded-lg text-amber-200 flex items-center gap-2">
              <AlertTriangle size={16} className="text-amber-400 flex-shrink-0" />
              <div>
                <span className="font-bold">Budget Guardrail:</span> Outlay ₹{totalAmount.toFixed(2)} exceeds ₹{budgetLimit.toLocaleString('en-IN')}. Choose an OTM strike or lower entry.
              </div>
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
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-semibold text-slate-400 mb-1.5">
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
              <label className="block text-xs font-semibold text-slate-400 mb-1.5 flex items-center justify-between">
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

          {/* Price, SL, Target */}
          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="block text-xs font-semibold text-slate-400 mb-1.5">
                Entry LTP (₹)
              </label>
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
              <label className="block text-xs font-semibold text-slate-400 mb-1.5">
                Stop Loss (₹)
              </label>
              <input
                type="number"
                step="0.05"
                min="0.5"
                value={stopLoss}
                onChange={(e) => setStopLoss(parseFloat(e.target.value) || 0)}
                className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-rose-300 font-mono text-sm focus:outline-none focus:border-rose-500"
              />
            </div>
            <div>
              <label className="block text-xs font-semibold text-slate-400 mb-1.5">
                Target (₹)
              </label>
              <input
                type="number"
                step="0.05"
                min="0.5"
                value={target}
                onChange={(e) => setTarget(parseFloat(e.target.value) || 0)}
                className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-emerald-300 font-mono text-sm focus:outline-none focus:border-emerald-500"
              />
            </div>
          </div>

          {/* Angel One Style Sizing & Budget Summary */}
          <div className="p-4 rounded-xl bg-slate-950 border border-slate-800 space-y-2.5">
            <div className="flex items-center justify-between text-xs">
              <span className="text-slate-400">Total Capital Outlay:</span>
              <span className={`font-mono font-bold text-sm ${exceedsBudget ? 'text-amber-400' : 'text-white'}`}>
                ₹{totalAmount.toLocaleString('en-IN', { minimumFractionDigits: 2 })}
              </span>
            </div>
            <div className="flex items-center justify-between text-xs">
              <span className="text-slate-400">Max Risk (SL):</span>
              <span className="font-mono font-semibold text-rose-400">
                -₹{maxRisk.toLocaleString('en-IN', { minimumFractionDigits: 2 })}
              </span>
            </div>
            <div className="flex items-center justify-between text-xs">
              <span className="text-slate-400">Expected Profit (Target):</span>
              <span className="font-mono font-semibold text-emerald-400">
                +₹{expectedProfit.toLocaleString('en-IN', { minimumFractionDigits: 2 })}
              </span>
            </div>
            <div className="pt-2 border-t border-slate-800 flex items-center justify-between text-[11px] text-slate-400">
              <span>Risk:Reward Ratio</span>
              <span className="font-mono font-bold text-blue-300">
                1 : {maxRisk > 0 ? (expectedProfit / maxRisk).toFixed(2) : '2.00'}
              </span>
            </div>
          </div>

          {/* Submit Button */}
          <div className="flex gap-3 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="flex-1 py-2.5 px-4 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 text-sm font-semibold transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting || exceedsBudget}
              className={`flex-1 py-2.5 px-4 rounded-xl text-white text-sm font-bold shadow transition-colors flex items-center justify-center gap-2 ${
                submitting || exceedsBudget
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
