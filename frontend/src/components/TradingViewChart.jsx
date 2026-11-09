import React, { useEffect, useRef, useState, useCallback, memo } from 'react';
import { createChart, CandlestickSeries, ColorType, CrosshairMode } from 'lightweight-charts';
import { getMarketCandles } from '../api';
import { TrendingUp, Maximize2, Minimize2, ExternalLink, RefreshCw, BarChart2 } from 'lucide-react';

const PRESET_SYMBOLS = [
  { label: 'NIFTY 50', symbol: 'NIFTY', tvSymbol: 'NSE:NIFTY' },
  { label: 'BANK NIFTY', symbol: 'BANKNIFTY', tvSymbol: 'NSE:BANKNIFTY' },
  { label: 'RELIANCE', symbol: 'RELIANCE', tvSymbol: 'NSE:RELIANCE' },
  { label: 'HDFC BANK', symbol: 'HDFCBANK', tvSymbol: 'NSE:HDFCBANK' },
];

const TIMEFRAMES = [
  { label: '5m', interval: '5m', range: '5d' },
  { label: '15m', interval: '15m', range: '5d' },
  { label: '1D', interval: '1d', range: '1mo' },
];

function TradingViewChart({ selectedIndex = 'NIFTY', onSelectIndex }) {
  const [selectedPreset, setSelectedPreset] = useState(PRESET_SYMBOLS[0]);
  const [selectedTf, setSelectedTf] = useState(TIMEFRAMES[1]); // default 15m
  const [isExpanded, setIsExpanded] = useState(false);
  const [loading, setLoading] = useState(true);
  const [lastBar, setLastBar] = useState(null);
  const [hoverBar, setHoverBar] = useState(null);

  const chartContainerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);

  // Sync index preset when parent index changes
  useEffect(() => {
    if (selectedIndex === 'BANKNIFTY') {
      setSelectedPreset(PRESET_SYMBOLS[1]);
    } else {
      setSelectedPreset(PRESET_SYMBOLS[0]);
    }
  }, [selectedIndex]);

  // Fetch candle data and update chart
  const loadCandleData = useCallback(async () => {
    try {
      setLoading(true);
      const res = await getMarketCandles(selectedPreset.symbol, selectedTf.interval, selectedTf.range);
      const candles = res.data?.candles || [];
      if (candles.length > 0 && seriesRef.current) {
        // Ensure strictly sorted ascending timestamps
        const sorted = [...candles].sort((a, b) => a.time - b.time);
        seriesRef.current.setData(sorted);
        chartRef.current?.timeScale().fitContent();

        const latest = sorted[sorted.length - 1];
        const prev = sorted.length > 1 ? sorted[sorted.length - 2] : latest;
        const change = latest.close - prev.close;
        const changePct = prev.close > 0 ? (change / prev.close) * 100 : 0;
        setLastBar({ ...latest, change, changePct });
      }
    } catch (err) {
      console.error('Failed to load candles:', err);
    } finally {
      setLoading(false);
    }
  }, [selectedPreset, selectedTf]);

  // Initialize TradingView Lightweight Chart
  useEffect(() => {
    const container = chartContainerRef.current;
    if (!container) return;

    const chart = createChart(container, {
      layout: {
        background: { type: ColorType.Solid, color: '#090d16' },
        textColor: '#94a3b8',
        fontSize: 12,
      },
      grid: {
        vertLines: { color: 'rgba(30, 41, 59, 0.45)' },
        horzLines: { color: 'rgba(30, 41, 59, 0.45)' },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: {
          color: '#38bdf8',
          width: 1,
          style: 3,
          labelBackgroundColor: '#0284c7',
        },
        horzLine: {
          color: '#38bdf8',
          width: 1,
          style: 3,
          labelBackgroundColor: '#0284c7',
        },
      },
      timeScale: {
        borderColor: '#1e293b',
        timeVisible: true,
        secondsVisible: false,
      },
      rightPriceScale: {
        borderColor: '#1e293b',
        scaleMargins: {
          top: 0.1,
          bottom: 0.1,
        },
      },
      handleScale: {
        mouseWheel: true,
        pinch: true,
        axisPressedMouseMove: true,
      },
      handleScroll: {
        mouseWheel: true,
        pressedMouseMove: true,
      },
    });

    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#10b981',
      downColor: '#f43f5e',
      borderVisible: false,
      wickUpColor: '#10b981',
      wickDownColor: '#f43f5e',
    });

    chartRef.current = chart;
    seriesRef.current = series;

    // Track crosshair hover for real-time OHLC display
    chart.subscribeCrosshairMove((param) => {
      if (param.time) {
        const data = param.seriesData.get(series);
        if (data) {
          setHoverBar(data);
          return;
        }
      }
      setHoverBar(null);
    });

    // Handle container resize
    const handleResize = () => {
      if (container && chart) {
        chart.applyOptions({
          width: container.clientWidth,
          height: container.clientHeight,
        });
      }
    };

    window.addEventListener('resize', handleResize);
    handleResize();

    return () => {
      window.removeEventListener('resize', handleResize);
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []);

  // Reload data when preset symbol or timeframe changes
  useEffect(() => {
    loadCandleData();
    const interval = setInterval(loadCandleData, 20000); // 20s live poll
    return () => clearInterval(interval);
  }, [loadCandleData]);

  // Adjust chart size on expand
  useEffect(() => {
    const container = chartContainerRef.current;
    if (container && chartRef.current) {
      setTimeout(() => {
        chartRef.current.applyOptions({
          width: container.clientWidth,
          height: container.clientHeight,
        });
      }, 50);
    }
  }, [isExpanded]);

  const activeBar = hoverBar || lastBar;

  return (
    <div className="backdrop-blur-xl bg-slate-900/60 border border-slate-700/40 rounded-2xl overflow-hidden shadow-2xl transition-all duration-300">
      {/* Header Bar */}
      <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-3 border-b border-slate-800/80 bg-slate-900/40">
        {/* Title & Live Status */}
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-blue-500/10 border border-blue-500/20 flex items-center justify-center text-blue-400">
            <TrendingUp size={18} />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-semibold text-slate-200 tracking-wide">
                Live {selectedPreset.label} Candlestick Chart
              </h2>
              <span className="flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                TradingView Engine
              </span>
            </div>
            {/* Real-time OHLC legend */}
            {activeBar && (
              <div className="flex items-center gap-2.5 text-xs font-mono mt-0.5 text-slate-400">
                <span>O: <span className="text-slate-200">{activeBar.open?.toLocaleString()}</span></span>
                <span>H: <span className="text-slate-200">{activeBar.high?.toLocaleString()}</span></span>
                <span>L: <span className="text-slate-200">{activeBar.low?.toLocaleString()}</span></span>
                <span>C: <span className={activeBar.close >= activeBar.open ? 'text-emerald-400' : 'text-rose-400'}>{activeBar.close?.toLocaleString()}</span></span>
                {activeBar.changePct !== undefined && (
                  <span className={activeBar.change >= 0 ? 'text-emerald-400' : 'text-rose-400'}>
                    {activeBar.change >= 0 ? '+' : ''}{activeBar.changePct?.toFixed(2)}%
                  </span>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Center Controls: Symbol Presets & Timeframes */}
        <div className="flex flex-wrap items-center gap-2">
          {/* Symbol Presets */}
          <div className="flex items-center gap-1 bg-slate-950/60 p-1 rounded-xl border border-slate-800">
            {PRESET_SYMBOLS.map((item) => {
              const isActive = selectedPreset.symbol === item.symbol;
              return (
                <button
                  key={item.symbol}
                  onClick={() => {
                    setSelectedPreset(item);
                    if ((item.symbol === 'NIFTY' || item.symbol === 'BANKNIFTY') && onSelectIndex) {
                      onSelectIndex(item.symbol);
                    }
                  }}
                  className={`px-3 py-1 rounded-lg text-xs font-medium transition-all ${
                    isActive
                      ? 'bg-blue-600 text-white shadow-sm shadow-blue-500/30'
                      : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
                  }`}
                >
                  {item.label}
                </button>
              );
            })}
          </div>

          {/* Timeframe Presets */}
          <div className="flex items-center gap-1 bg-slate-950/60 p-1 rounded-xl border border-slate-800">
            {TIMEFRAMES.map((tf) => {
              const isActive = selectedTf.interval === tf.interval;
              return (
                <button
                  key={tf.interval}
                  onClick={() => setSelectedTf(tf)}
                  className={`px-2.5 py-1 rounded-lg text-xs font-mono transition-all ${
                    isActive
                      ? 'bg-slate-700 text-white font-semibold'
                      : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  {tf.label}
                </button>
              );
            })}
          </div>
        </div>

        {/* Action Controls */}
        <div className="flex items-center gap-2">
          {/* Refresh Button */}
          <button
            onClick={loadCandleData}
            title="Refresh candles"
            className="p-1.5 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-slate-800/60 transition-colors"
          >
            <RefreshCw size={15} className={loading ? 'animate-spin text-blue-400' : ''} />
          </button>

          {/* Direct TradingView Link */}
          <a
            href={`https://www.tradingview.com/chart/?symbol=${selectedPreset.tvSymbol}`}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1.5 px-3 py-1 rounded-lg text-xs font-medium bg-slate-800/80 hover:bg-slate-700/80 text-blue-400 hover:text-blue-300 border border-slate-700 transition-colors"
            title="Open full chart on TradingView.com"
          >
            <span>TradingView.com</span>
            <ExternalLink size={13} />
          </a>

          {/* Expand / Minimize */}
          <button
            onClick={() => setIsExpanded(!isExpanded)}
            title={isExpanded ? 'Normal height' : 'Expand chart'}
            className="p-1.5 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-slate-800/60 transition-colors"
          >
            {isExpanded ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
          </button>
        </div>
      </div>

      {/* Chart Canvas Area */}
      <div
        className={`w-full transition-all duration-300 relative bg-[#090d16] ${
          isExpanded ? 'h-[650px]' : 'h-[460px]'
        }`}
      >
        <div ref={chartContainerRef} className="h-full w-full" />

        {/* Loading Overlay */}
        {loading && (
          <div className="absolute top-3 right-3 bg-slate-900/80 backdrop-blur-md px-3 py-1.5 rounded-lg border border-slate-700 text-xs text-slate-400 flex items-center gap-2 pointer-events-none">
            <div className="w-3.5 h-3.5 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
            <span>Fetching live candles...</span>
          </div>
        )}
      </div>
    </div>
  );
}

export default memo(TradingViewChart);
