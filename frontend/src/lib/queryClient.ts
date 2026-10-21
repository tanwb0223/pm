import { QueryClient } from '@tanstack/react-query'

/**
 * 全局 QueryClient
 *
 * 单独抽出成模块，是为了让「退出登录 / 切换账号」能拿到同一个实例清缓存。
 * 缓存按账号隔离：不同账号的 ['me']、['todo']、['alerts'] 等绝不能复用，
 * 否则换账号后界面会继续显示上一个账号的数据（菜单、用户名、待办数、列表）。
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
      staleTime: 30 * 1000,
    },
  },
})

/** 清空所有查询缓存（退出登录、切换账号时调用） */
export function clearQueryCache() {
  queryClient.clear()
}