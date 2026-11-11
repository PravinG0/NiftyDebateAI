import React, { useState, useRef, useEffect } from 'react';
import {
  Zap,
  Layers,
  Brain,
  LayoutDashboard,
  TrendingUp,
  User,
  LogOut,
  ChevronDown,
  Menu,
  X,
  Shield,
  Sparkles,
  Wallet,
} from 'lucide-react';
import { useAuth } from '../context/AuthContext';

export const NAV_VIEWS = {
  ARENA: 'arena',
  OPTION_CHAIN: 'option_chain',
  STUDIO: 'studio',
  DASHBOARD: 'dashboard',
};

export default function Navbar({
  activeView = NAV_VIEWS.ARENA,
  onSelectView,
  onOpenAuthModal,
  systemStatus,
}) {
  const { user, isAuthenticated, isGuest, logout } = useAuth();
  const [profileDropdownOpen, setProfileDropdownOpen] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const dropdownRef = useRef(null);

  // Close dropdown when clicking outside
  useEffect(() => {
    function handleClickOutside(event) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
        setProfileDropdownOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const navItems = [
    { id: NAV_VIEWS.ARENA, label: 'Live Arena', icon: Zap },
    { id: NAV_VIEWS.OPTION_CHAIN, label: 'Option Chain', icon: Layers },
    { id: NAV_VIEWS.STUDIO, label: 'Debate Studio', icon: Brain },
    { id: NAV_VIEWS.DASHBOARD, label: 'My Dashboard', icon: LayoutDashboard },
  ];

  return (
    <nav className="relative z-40 bg-[#080C14] border-b border-slate-800 px-3 sm:px-6 py-2.5">
      <div className="max-w-[1600px] mx-auto flex items-center justify-between gap-4">
        {/* Brand & Logo */}
        <div className="flex items-center gap-3">
          <div className="relative flex-shrink-0">
            <div className="w-9 h-9 sm:w-10 sm:h-10 rounded-xl bg-gradient-to-tr from-blue-600 to-indigo-500 flex items-center justify-center text-white shadow-lg shadow-blue-500/20">
              <Zap size={20} className="fill-white/20" />
            </div>
            <div className="absolute -top-0.5 -right-0.5 w-3 h-3 bg-emerald-400 rounded-full border-2 border-[#080C14] animate-pulse" />
          </div>

          <div>
            <div className="flex items-center gap-2">
              <span className="font-extrabold text-base sm:text-lg tracking-tight bg-clip-text text-transparent bg-gradient-to-r from-white via-slate-100 to-slate-400">
                NIFTY DEBATE AI
              </span>
              <span className="hidden sm:inline-flex text-[9px] font-bold uppercase px-1.5 py-0.5 rounded bg-blue-950/80 text-blue-300 border border-blue-800">
                Paper 1-Lot
              </span>
            </div>
            <p className="text-[10px] text-slate-400 font-medium tracking-wide hidden sm:block">
              Multi-Agent Options Debate Arena
            </p>
          </div>
        </div>

        {/* Desktop View Switcher Tabs */}
        <div className="hidden md:flex items-center bg-[#0D131F] p-1 rounded-xl border border-slate-800/80 shadow-inner">
          {navItems.map((item) => {
            const Icon = item.icon;
            const isActive = activeView === item.id;
            return (
              <button
                key={item.id}
                onClick={() => onSelectView(item.id)}
                className={`flex items-center gap-2 px-3.5 py-1.5 rounded-lg text-xs font-semibold transition-all duration-200 ${
                  isActive
                    ? 'bg-blue-600 text-white shadow-md shadow-blue-600/30 font-bold'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/40'
                }`}
              >
                <Icon size={14} />
                <span>{item.label}</span>
              </button>
            );
          })}
        </div>

        {/* Right: Auth Profile / Sign In Pill */}
        <div className="flex items-center gap-2 sm:gap-3">
          {isAuthenticated ? (
            <div className="relative" ref={dropdownRef}>
              <button
                onClick={() => setProfileDropdownOpen(!profileDropdownOpen)}
                className="flex items-center gap-2 px-2.5 sm:px-3 py-1.5 rounded-xl bg-[#0D131F] hover:bg-slate-800/80 border border-slate-700/60 transition-colors text-xs"
              >
                <div className="w-6 h-6 rounded-lg bg-gradient-to-tr from-emerald-500 to-teal-400 flex items-center justify-center text-slate-950 font-bold text-xs">
                  {user.username?.charAt(0).toUpperCase() || 'U'}
                </div>
                <div className="text-left hidden sm:block">
                  <div className="font-semibold text-slate-200 text-xs leading-none">
                    {user.username}
                  </div>
                  <div className="text-[10px] text-emerald-400 font-mono font-medium leading-tight mt-0.5">
                    ₹1,00,000 Paper
                  </div>
                </div>
                <ChevronDown size={14} className="text-slate-400" />
              </button>

              {/* Profile Dropdown */}
              {profileDropdownOpen && (
                <div className="absolute right-0 mt-2 w-56 bg-[#0D131F] border border-slate-700/80 rounded-2xl shadow-2xl p-2 space-y-1 z-50 animate-slide-up">
                  <div className="px-3 py-2 border-b border-slate-800">
                    <p className="text-xs font-semibold text-white truncate">{user.full_name || user.username}</p>
                    <p className="text-[11px] text-slate-400 font-mono truncate">{user.email}</p>
                    <div className="flex items-center gap-1 mt-1.5 text-[10px] text-emerald-400 font-mono bg-emerald-950/40 px-2 py-0.5 rounded border border-emerald-800/40">
                      <Wallet size={11} />
                      <span>Balance: ₹1,00,000</span>
                    </div>
                  </div>

                  <button
                    onClick={() => {
                      onSelectView(NAV_VIEWS.DASHBOARD);
                      setProfileDropdownOpen(false);
                    }}
                    className="w-full flex items-center gap-2 px-3 py-2 text-xs font-medium text-slate-300 hover:text-white hover:bg-slate-800/60 rounded-xl transition-colors text-left"
                  >
                    <LayoutDashboard size={14} className="text-blue-400" />
                    <span>My Dashboard & Portfolio</span>
                  </button>

                  <button
                    onClick={() => {
                      logout();
                      setProfileDropdownOpen(false);
                    }}
                    className="w-full flex items-center gap-2 px-3 py-2 text-xs font-medium text-rose-400 hover:text-rose-300 hover:bg-rose-950/40 rounded-xl transition-colors text-left"
                  >
                    <LogOut size={14} />
                    <span>Sign Out</span>
                  </button>
                </div>
              )}
            </div>
          ) : (
            <div className="flex items-center gap-1.5 sm:gap-2">
              <span className="hidden lg:inline-flex items-center gap-1 px-2 py-1 rounded-lg bg-slate-900 border border-slate-800 text-[11px] text-slate-400 font-medium">
                <span className="w-1.5 h-1.5 rounded-full bg-slate-500" />
                Guest Mode
              </span>

              <button
                onClick={() => onOpenAuthModal && onOpenAuthModal('login')}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-blue-600 hover:bg-blue-500 text-white font-semibold text-xs transition-colors shadow-md shadow-blue-600/20"
              >
                <User size={13} />
                <span>Sign In / Join</span>
              </button>
            </div>
          )}

          {/* Mobile Menu Hamburger */}
          <button
            onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
            className="md:hidden p-2 rounded-xl text-slate-400 hover:text-white hover:bg-slate-800 transition-colors"
            aria-label="Toggle Navigation Menu"
          >
            {mobileMenuOpen ? <X size={20} /> : <Menu size={20} />}
          </button>
        </div>
      </div>

      {/* Mobile Drawer Menu */}
      {mobileMenuOpen && (
        <div className="md:hidden pt-3 pb-2 border-t border-slate-800/80 mt-2 space-y-2 animate-fade-in">
          <div className="grid grid-cols-2 gap-1.5">
            {navItems.map((item) => {
              const Icon = item.icon;
              const isActive = activeView === item.id;
              return (
                <button
                  key={item.id}
                  onClick={() => {
                    onSelectView(item.id);
                    setMobileMenuOpen(false);
                  }}
                  className={`flex items-center gap-2 px-3 py-2 rounded-xl text-xs font-semibold transition-colors ${
                    isActive
                      ? 'bg-blue-600 text-white font-bold'
                      : 'text-slate-300 hover:bg-slate-800 bg-[#0D131F]'
                  }`}
                >
                  <Icon size={14} />
                  <span>{item.label}</span>
                </button>
              );
            })}
          </div>
        </div>
      )}
    </nav>
  );
}
