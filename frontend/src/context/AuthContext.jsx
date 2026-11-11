import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import api, { auth, TOKEN_STORAGE_KEY, USER_STORAGE_KEY } from '../api';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [token, setToken] = useState(() => {
    try {
      return localStorage.getItem(TOKEN_STORAGE_KEY) || null;
    } catch {
      return null;
    }
  });

  const [user, setUser] = useState(() => {
    try {
      const saved = localStorage.getItem(USER_STORAGE_KEY);
      return saved ? JSON.parse(saved) : null;
    } catch {
      return null;
    }
  });

  const [loading, setLoading] = useState(true);
  const [authError, setAuthError] = useState(null);

  // Validate existing token on mount
  useEffect(() => {
    let isCurrent = true;
    const validateSession = async () => {
      const storedToken = localStorage.getItem(TOKEN_STORAGE_KEY);
      if (!storedToken) {
        if (isCurrent) setLoading(false);
        return;
      }
      try {
        const res = await auth.me();
        if (isCurrent && res.data) {
          setUser(res.data);
          localStorage.setItem(USER_STORAGE_KEY, JSON.stringify(res.data));
        }
      } catch (err) {
        // Token invalid or expired, clear it
        if (isCurrent) {
          setToken(null);
          setUser(null);
          try {
            localStorage.removeItem(TOKEN_STORAGE_KEY);
            localStorage.removeItem(USER_STORAGE_KEY);
          } catch {
            // ignore
          }
        }
      } finally {
        if (isCurrent) setLoading(false);
      }
    };

    validateSession();

    // Listen for 401 expiration event from Axios interceptor
    const handleAuthExpired = () => {
      setToken(null);
      setUser(null);
    };

    window.addEventListener('niftydebate_auth_expired', handleAuthExpired);
    return () => {
      isCurrent = false;
      window.removeEventListener('niftydebate_auth_expired', handleAuthExpired);
    };
  }, []);

  const login = useCallback(async (credentials) => {
    setAuthError(null);
    try {
      const res = await auth.login(credentials);
      const { access_token, user: userData } = res.data;
      setToken(access_token);
      setUser(userData);
      try {
        localStorage.setItem(TOKEN_STORAGE_KEY, access_token);
        localStorage.setItem(USER_STORAGE_KEY, JSON.stringify(userData));
      } catch {
        // ignore
      }
      return { success: true, user: userData };
    } catch (err) {
      const msg = err.response?.data?.detail || err.message || 'Login failed';
      setAuthError(msg);
      return { success: false, error: msg };
    }
  }, []);

  const register = useCallback(async (userData) => {
    setAuthError(null);
    try {
      const res = await auth.register(userData);
      const { access_token, user: createdUser } = res.data;
      setToken(access_token);
      setUser(createdUser);
      try {
        localStorage.setItem(TOKEN_STORAGE_KEY, access_token);
        localStorage.setItem(USER_STORAGE_KEY, JSON.stringify(createdUser));
      } catch {
        // ignore
      }
      return { success: true, user: createdUser };
    } catch (err) {
      const msg = err.response?.data?.detail || err.message || 'Registration failed';
      setAuthError(msg);
      return { success: false, error: msg };
    }
  }, []);

  const logout = useCallback(async () => {
    try {
      await auth.logout();
    } catch {
      // ignore
    } finally {
      setToken(null);
      setUser(null);
      try {
        localStorage.removeItem(TOKEN_STORAGE_KEY);
        localStorage.removeItem(USER_STORAGE_KEY);
      } catch {
        // ignore
      }
    }
  }, []);

  const isAuthenticated = Boolean(token && user);
  const isGuest = !isAuthenticated;

  const value = {
    user,
    token,
    isAuthenticated,
    isGuest,
    loading,
    authError,
    setAuthError,
    login,
    register,
    logout,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}

export default AuthContext;
