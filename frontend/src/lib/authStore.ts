import { create } from "zustand";
import { persist } from "zustand/middleware";
import { api, UserResponse } from "./api";

interface AuthState {
  token: string | null;
  user: UserResponse | null;
  isLoading: boolean;
  error: string | null;
  signup: (email: string, password: string, name: string) => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  hydrateUser: () => Promise<void>;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      token: null,
      user: null,
      isLoading: false,
      error: null,

      signup: async (email, password, name) => {
        set({ isLoading: true, error: null });
        try {
          await api.signup(email, password, name);
        } catch (e) {
          set({ error: e instanceof Error ? e.message : "Signup failed" });
          throw e;
        } finally {
          set({ isLoading: false });
        }
      },

      login: async (email, password) => {
        set({ isLoading: true, error: null });
        try {
          const { access_token } = await api.login(email, password);
          set({ token: access_token });
          await get().hydrateUser();
        } catch (e) {
          set({ error: e instanceof Error ? e.message : "Login failed" });
          throw e;
        } finally {
          set({ isLoading: false });
        }
      },

      logout: () => set({ token: null, user: null }),

      hydrateUser: async () => {
        const { token } = get();
        if (!token) return;
        try {
          const user = await api.me(token);
          set({ user });
        } catch {
          // token invalid/expired
          set({ token: null, user: null });
        }
      },
    }),
    {
      name: "gdtrainer-auth",
      partialize: (state) => ({ token: state.token }),
    }
  )
);
