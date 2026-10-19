import { create } from 'zustand'
import { persist } from 'zustand/middleware'

import type { MeInfo } from '@/types'

interface AuthState {
  token: string | null
  user: MeInfo | null
  setToken: (token: string) => void
  setUser: (user: MeInfo) => void
  logout: () => void
  hasPerm: (code: string) => boolean
  hasAnyPerm: (...codes: string[]) => boolean
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      token: null,
      user: null,

      setToken: (token) => set({ token }),

      setUser: (user) => set({ user }),

      logout: () => set({ token: null, user: null }),

      hasPerm: (code) => {
        const u = get().user
        if (!u) return false
        if (u.roles.includes('SYS_ADMIN')) return true
        return u.permissions.includes(code)
      },

      hasAnyPerm: (...codes) => {
        const u = get().user
        if (!u) return false
        if (u.roles.includes('SYS_ADMIN')) return true
        return codes.some((c) => u.permissions.includes(c))
      },
    }),
    {
      name: 'pm-auth',
      partialize: (state) => ({ token: state.token }),
    },
  ),
)
