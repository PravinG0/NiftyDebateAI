import React, { useState, useEffect } from 'react';
import { Wifi, Brain, ShieldCheck, CheckCircle2, Loader2, Sparkles } from 'lucide-react';

export default function DebateStepper({ isRunning = false, selectedIndex = 'NIFTY' }) {
  const [currentStep, setCurrentStep] = useState(1);

  // Cycle through steps during live debate execution
  useEffect(() => {
    if (!isRunning) {
      setCurrentStep(1);
      return;
    }

    const timer1 = setTimeout(() => setCurrentStep(2), 2200);
    const timer2 = setTimeout(() => setCurrentStep(3), 4800);

    return () => {
      clearTimeout(timer1);
      clearTimeout(timer2);
    };
  }, [isRunning]);

  if (!isRunning) return null;

  const steps = [
    {
      step: 1,
      title: 'Angel One Snapshot',
      desc: `Ingesting live ${selectedIndex} OHLC candles & Option Chain`,
      icon: Wifi,
    },
    {
      step: 2,
      title: 'Agent Deliberation',
      desc: 'Bull & Bear agents formulating technical theses',
      icon: Brain,
    },
    {
      step: 3,
      title: 'Judge Guardrail Sizing',
      desc: 'Evaluating 1-lot position sizing within ₹5,000 budget',
      icon: ShieldCheck,
    },
  ];

  return (
    <div className="bg-[#0D131F] border border-blue-600/40 rounded-2xl p-4 sm:p-5 shadow-2xl space-y-4 animate-card-appear">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Loader2 size={16} className="text-blue-400 animate-spin" />
          <h3 className="text-xs sm:text-sm font-bold text-white tracking-wide">
            Adversarial Debate In Progress
          </h3>
        </div>
        <span className="text-[11px] font-mono font-bold text-blue-400 bg-blue-950/60 px-2.5 py-0.5 rounded-lg border border-blue-800/60">
          Step {currentStep} of 3
        </span>
      </div>

      {/* Stepper Timeline */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {steps.map((s) => {
          const Icon = s.icon;
          const isDone = currentStep > s.step;
          const isActive = currentStep === s.step;

          return (
            <div
              key={s.step}
              className={`p-3.5 rounded-xl border transition-all ${
                isActive
                  ? 'bg-blue-950/40 border-blue-500/80 shadow-md ring-1 ring-blue-500/30'
                  : isDone
                  ? 'bg-slate-900/40 border-emerald-800/40 text-slate-400'
                  : 'bg-slate-950/40 border-slate-800/50 text-slate-500'
              }`}
            >
              <div className="flex items-center gap-2.5 mb-1.5">
                <div
                  className={`w-6 h-6 rounded-lg flex items-center justify-center text-xs font-bold ${
                    isDone
                      ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
                      : isActive
                      ? 'bg-blue-500 text-white animate-pulse'
                      : 'bg-slate-800 text-slate-400'
                  }`}
                >
                  {isDone ? <CheckCircle2 size={14} /> : s.step}
                </div>
                <h4
                  className={`text-xs font-bold ${
                    isActive ? 'text-white' : isDone ? 'text-emerald-300' : 'text-slate-400'
                  }`}
                >
                  {s.title}
                </h4>
              </div>
              <p className="text-[11px] text-slate-400 leading-snug">{s.desc}</p>
            </div>
          );
        })}
      </div>
    </div>
  );
}
