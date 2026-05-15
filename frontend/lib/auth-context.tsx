"use client";

import { AxiosError } from "axios";
import { useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import { api } from "@/lib/api";
import type { AuthResponse, Market, RiskProfile, User } from "@/lib/types";

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  signup: (data: SignupData) => Promise<void>;
  logout: () => Promise<void>;
}

export interface SignupData {
  email: string;
  password: string;
  full_name?: string;
  default_market?: Market;
  risk_profile?: RiskProfile;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();

  useEffect(() => {
    let cancelled = false;
    api
      .get<User>("/api/auth/me")
      .then(({ data }) => {
        if (!cancelled) setUser(data);
      })
      .catch((err: unknown) => {
        // Only treat 401 as "logged out". Network or 5xx errors leave the
        // session state untouched so a transient outage doesn't sign users out.
        if (err instanceof AxiosError && err.response?.status === 401) {
          if (!cancelled) setUser(null);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(
    async (email: string, password: string) => {
      const { data } = await api.post<AuthResponse>("/api/auth/login", {
        email,
        password,
      });
      setUser(data.user);
      router.push("/dashboard");
    },
    [router]
  );

  const signup = useCallback(
    async (payload: SignupData) => {
      const { data } = await api.post<AuthResponse>("/api/auth/signup", payload);
      setUser(data.user);
      router.push("/dashboard");
    },
    [router]
  );

  const logout = useCallback(async () => {
    try {
      await api.post("/api/auth/logout");
    } catch {
      // Ignore network errors; we still want to clear local state.
    }
    setUser(null);
    router.push("/login");
  }, [router]);

  const value = useMemo(
    () => ({ user, loading, login, signup, logout }),
    [user, loading, login, signup, logout]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
