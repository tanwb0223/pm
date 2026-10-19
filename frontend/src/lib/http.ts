/**
 * HTTP 客户端（axios 封装）
 *
 * 约定：
 * - 后端统一返回 { code, message, data }，这里直接解包出 data
 * - 请求自动带 Bearer Token（从 zustand 持久化的 pm-auth 里读）
 * - 401 清空登录态并跳转登录页
 * - 其它错误统一用 antd message 提示，页面里的 catch 只做流程控制
 * - 文件下载单独走 download()，用 blob + Content-Disposition 还原文件名
 */
import axios, {
  AxiosError,
  type AxiosInstance,
  type AxiosRequestConfig,
  type InternalAxiosRequestConfig,
} from 'axios'
import { message } from 'antd'

import type { ApiResp } from '@/types'

/** localStorage 里 zustand persist 的 key（见 stores/auth.ts） */
const AUTH_STORAGE_KEY = 'pm-auth'

/** 避免 401 时多个并发请求重复跳转 */
let redirecting = false

function getToken(): string | null {
  try {
    const raw = localStorage.getItem(AUTH_STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as { state?: { token?: string | null } }
    return parsed?.state?.token ?? null
  } catch {
    return null
  }
}

function clearAuthAndRedirect() {
  if (redirecting) return
  redirecting = true
  try {
    localStorage.removeItem(AUTH_STORAGE_KEY)
  } catch {
    // 忽略存储异常
  }
  const { pathname, search } = window.location
  if (!pathname.startsWith('/login')) {
    const redirect = encodeURIComponent(pathname + search)
    window.location.href = `/login?redirect=${redirect}`
  } else {
    redirecting = false
  }
}

/** 从后端错误响应里提取可读信息 */
function pickErrorMessage(error: AxiosError): string {
  const resp = error.response
  if (!resp) {
    if (error.code === 'ECONNABORTED') return '请求超时，请稍后重试'
    return '网络异常，请检查网络连接'
  }

  const data = resp.data as unknown
  // FastAPI 的 HTTPException：{"detail": "..."}
  if (data && typeof data === 'object' && 'detail' in data) {
    const detail = (data as { detail: unknown }).detail
    if (typeof detail === 'string' && detail) return detail
    // 422 校验错误：detail 是数组
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { loc?: unknown[]; msg?: string }
      const loc = Array.isArray(first.loc)
        ? first.loc.filter((x) => x !== 'body' && x !== 'query').join('.')
        : ''
      const msg = first.msg ?? '参数错误'
      return loc ? `${loc}：${msg}` : msg
    }
  }
  // 本项目统一响应：{"code": 400, "message": "...", "data": null}
  if (data && typeof data === 'object' && 'message' in data) {
    const msg = (data as { message: unknown }).message
    if (typeof msg === 'string' && msg) return msg
  }
  if (resp.status === 403) return '无权限执行该操作'
  if (resp.status === 404) return '请求的资源不存在'
  if (resp.status >= 500) return '服务器内部错误，请联系管理员'
  return `请求失败（${resp.status}）`
}

// ---------------------------------------------------------------- 实例
export const http: AxiosInstance = axios.create({
  baseURL: '/api',
  timeout: 60_000,
  headers: { 'Content-Type': 'application/json' },
})

// 请求：注入 token
http.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = getToken()
  if (token) {
    config.headers.set('Authorization', `Bearer ${token}`)
  }
  return config
})

// 响应：解包 + 统一错误提示
http.interceptors.response.use(
  (response) => {
    // 文件流不走解包：download() 需要读取 Content-Disposition，所以保留完整响应
    if (response.config.responseType === 'blob') {
      return response as never
    }
    const body = response.data as ApiResp<unknown> | undefined
    if (body && typeof body === 'object' && 'code' in body) {
      if (body.code === 0) {
        return body.data as never
      }
      message.error(body.message || '操作失败')
      return Promise.reject(new Error(body.message || '操作失败'))
    }
    return response.data as never
  },
  (error: AxiosError) => {
    if (error.response?.status === 401) {
      message.error('登录已过期，请重新登录')
      clearAuthAndRedirect()
      return Promise.reject(error)
    }
    const msg = pickErrorMessage(error)
    message.error(msg)
    return Promise.reject(new Error(msg))
  },
)

// ---------------------------------------------------------------- 便捷方法
/** GET，params 自动过滤 undefined / 空字符串 */
export function get<T>(
  url: string,
  params?: Record<string, unknown> | object,
  config?: AxiosRequestConfig,
): Promise<T> {
  const cleaned: Record<string, unknown> = {}
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v === undefined || v === null || v === '') continue
      cleaned[k] = v
    }
  }
  return http.get(url, { ...config, params: cleaned }) as unknown as Promise<T>
}

export function post<T>(
  url: string,
  data?: unknown,
  config?: AxiosRequestConfig,
): Promise<T> {
  return http.post(url, data, config) as unknown as Promise<T>
}

export function put<T>(
  url: string,
  data?: unknown,
  config?: AxiosRequestConfig,
): Promise<T> {
  return http.put(url, data, config) as unknown as Promise<T>
}

export function del<T>(
  url: string,
  data?: unknown,
  config?: AxiosRequestConfig,
): Promise<T> {
  return http.delete(url, { ...config, data }) as unknown as Promise<T>
}

// ---------------------------------------------------------------- 文件下载
/** 从 Content-Disposition 里取文件名，支持 filename*=UTF-8''... */
function parseFilename(disposition: string | undefined, fallback: string): string {
  if (!disposition) return fallback

  const utf8Match = /filename\*=UTF-8''([^;]+)/i.exec(disposition)
  if (utf8Match?.[1]) {
    try {
      return decodeURIComponent(utf8Match[1].replace(/^"|"$/g, ''))
    } catch {
      // 落到下面的普通匹配
    }
  }

  const plainMatch = /filename="?([^";]+)"?/i.exec(disposition)
  if (plainMatch?.[1]) {
    try {
      return decodeURIComponent(plainMatch[1])
    } catch {
      return plainMatch[1]
    }
  }
  return fallback
}

/** 触发浏览器下载一个 Blob */
function saveBlob(blob: Blob, filename: string) {
  const url = window.URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  // 延迟释放，避免部分浏览器下载中断
  window.setTimeout(() => window.URL.revokeObjectURL(url), 1000)
}

/**
 * 下载文件（导出 Excel / PDF、下载导入模板）
 *
 * 后端通过 Content-Disposition 给出文件名，这里原样还原。
 */
export async function download(
  url: string,
  params?: Record<string, unknown> | object,
  fallbackName = 'download',
): Promise<void> {
  const cleaned: Record<string, unknown> = {}
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v === undefined || v === null || v === '') continue
      cleaned[k] = v
    }
  }

  const resp = (await http.get(url, {
    params: cleaned,
    responseType: 'blob',
    timeout: 300_000, // 导出大文件可能较慢
  })) as unknown as { data: Blob; headers?: Record<string, string> }

  const filename = parseFilename(resp.headers?.['content-disposition'], fallbackName)
  saveBlob(resp.data, filename)
}

export default http
