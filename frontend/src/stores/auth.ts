import { useCallback } from 'react'
import { create } from 'zustand'
import { persist } from 'zustand/middleware'

import type { MeInfo } from '@/types'

interface AuthState {
  token: string | null
  user: MeInfo | null
  setToken: (token: string) => void
  setUser: (user: MeInfo) => void
  logout: () => void
}

/**
 * 登录态 store
 *
 * 注意：只在 persist 里保留 token，user 每次刷新后由 /auth/me 重新拉取。
 * 因此**不要**在组件里用 `useAuthStore(s => s.hasPerm)` 取权限判断函数——
 * 那个函数引用地址永远不变，user 变化时组件不会重渲染，刷新后菜单/按钮会丢。
 * 组件里请用下面的 useCan() / useCanAny()。
 */
export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      user: null,

      setToken: (token) => set({ token }),

      setUser: (user) => set({ user }),

      logout: () => set({ token: null, user: null }),
    }),
    {
      name: 'pm-auth',
      partialize: (state) => ({ token: state.token }),
    },
  ),
)

/** 权限判定（订阅 user，用户信息到位后组件会正确重渲染） */
export function useCan() {
  const user = useAuthStore((s) => s.user)
  return useCallback(
    (code: string) => {
      if (!user) return false
      if (user.roles.includes('SYS_ADMIN')) return true
      return user.permissions.includes(code)
    },
    [user],
  )
}

/** 任一权限满足即 true（订阅 user，同上） */
export function useCanAny() {
  const user = useAuthStore((s) => s.user)
  return useCallback(
    (...codes: string[]) => {
      if (!user) return false
      if (user.roles.includes('SYS_ADMIN')) return true
      return codes.some((c) => user.permissions.includes(c))
    },
    [user],
  )
}
