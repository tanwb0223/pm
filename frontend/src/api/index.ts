import { del, download, get, http, post, put } from '@/lib/http'
import type {
  Alert,
  AlertGroup,
  AuthProviderInfo,
  College,
  Completeness,
  LoginResult,
  Major,
  MajorCollege,
  MeInfo,
  PageData,
  PendingReport,
  ProgressRow,
  ReportItem,
  RiskItem,
  Sub,
  TimelinessDetailRow,
  TodoData,
  UserItem,
  ImportResult,
} from '@/types'

// ================================================================ 认证
export const authApi = {
  providers: () => get<AuthProviderInfo[]>('/auth/providers'),
  login: (username: string, password: string) =>
    post<LoginResult>('/auth/login', { username, password }),
  loginLdap: (username: string, password: string) =>
    post<LoginResult>('/auth/login/ldap', { username, password }),
  me: () => get<MeInfo>('/auth/me'),
  startLogin: (kind: string, redirectAfter = '/') =>
    get<{ authorization_url: string; state: string }>(`/auth/login/${kind}/start`, {
      redirect_after: redirectAfter,
    }),
  exchangeToken: (code: string) =>
    post<LoginResult>('/auth/token', { code }),
}

// ================================================================ 认证管理（后台配置）
export const authAdminApi = {
  providers: () =>
    get<
      {
        kind: string
        enabled: boolean
        display_name: string
        issuer: string
        client_id: string
        has_client_secret: boolean
        scopes: string
        server_url: string
        extra: Record<string, unknown>
        auto_create_user: boolean
        default_role_id: number | null
        redirect_uri: string
        configured: boolean
      }[]
    >('/auth/admin/providers'),
  get: (kind: string) => get<Record<string, unknown>>(`/auth/admin/providers/${kind}`),
  update: (kind: string, data: Record<string, unknown>) =>
    put<Record<string, unknown>>(`/auth/admin/providers/${kind}`, data),
  test: (kind: string) =>
    post<{ ok: boolean; detail: string }>(`/auth/admin/providers/${kind}/test`),
  myIdentities: () =>
    get<{ provider: string; display_name: string; subject: string; last_login_at: string | null }[]>(
      '/auth/me/identities',
    ),
}

// ================================================================ 学院
export const collegeApi = {
  list: () => get<College[]>('/colleges'),
  create: (data: Partial<College>) => post<{ id: number }>('/colleges', data),
  update: (id: number, data: Partial<College>) => put<void>(`/colleges/${id}`, data),
  remove: (id: number) => del<void>(`/colleges/${id}`),
}

// ================================================================ 大项目
export const majorApi = {
  list: (params: Record<string, unknown>) => get<PageData<Major>>('/majors', params),
  detail: (id: number) => get<Major>(`/majors/${id}`),
  create: (data: Partial<Major>) => post<{ id: number }>('/majors', data),
  update: (id: number, data: Partial<Major>) => put<void>(`/majors/${id}`, data),
  remove: (id: number) => del<void>(`/majors/${id}`),
  colleges: (id: number) => get<MajorCollege[]>(`/majors/${id}/colleges`),
  addCollege: (id: number, college_id: number, college_owner_id?: number | null) =>
    post<void>(`/majors/${id}/colleges`, { college_id, college_owner_id }),
  removeCollege: (id: number, collegeId: number) =>
    del<void>(`/majors/${id}/colleges/${collegeId}`),
  completeness: (id: number) => get<Completeness>(`/majors/${id}/completeness`),
}

// ================================================================ 子项目
export const subApi = {
  list: (params: Record<string, unknown>) => get<PageData<Sub>>('/subs', params),
  detail: (id: number) => get<Sub>(`/subs/${id}`),
  create: (data: Partial<Sub>) => post<{ id: number }>('/subs', data),
  update: (id: number, data: Partial<Sub>) => put<void>(`/subs/${id}`, data),
  remove: (id: number) => del<void>(`/subs/${id}`),
  updateProgress: (
    id: number,
    data: { progress: number; note?: string; no_change?: boolean; rollback_reason?: string },
  ) => post<{ record_id: number }>(`/subs/${id}/progress`, data),
  records: (id: number) =>
    get<
      {
        id: number
        progress: number
        note: string | null
        is_no_change: boolean
        is_rollback: boolean
        rollback_reason: string | null
        operator_id: number
        on_behalf_of: number | null
        created_at: string
      }[]
    >(`/subs/${id}/records`),
  view: (id: number) => get<ProgressRow>(`/subs/${id}/view`),
}

// ================================================================ 进度
export const progressApi = {
  table: (params: Record<string, unknown>) => get<PageData<ProgressRow>>('/progress/table', params),
  majorSummary: (id: number) =>
    get<{
      major_progress: number
      college_progress: Record<string, number>
      counted_subs: number
      school_progress: number
      major_name: string
      detail: {
        sub_id: number
        name: string
        college_id: number
        weight: number | null
        progress: number
        status: string
      }[]
    }>(`/progress/major/${id}`),
  school: () =>
    get<{
      school_progress: number
      major_count: number
      majors: { id: number; name: string; progress: number }[]
    }>('/progress/school'),
  /** 子项目进度趋势曲线 */
  subTrend: (subId: number, days = 90) =>
    get<{
      sub_id: number
      sub_name: string
      plan_start: string | null
      plan_end: string | null
      current_progress: number
      has_plan: boolean
      first_lag_date: string | null
      lag_days: number
      points: {
        date: string
        expected: number | null
        actual: number | null
        deviation: number | null
        is_free_day: boolean
      }[]
    }>(`/progress/sub/${subId}/trend`, { days }),
  /** 学院横向对比 */
  collegeCompare: (majorId?: number) =>
    get<
      {
        college_id: number
        college_name: string
        total_subs: number
        avg_deviation: number | null
        lagging_count: number
        severe_count: number
        updated_count: number
        update_rate: number | null
      }[]
    >('/progress/college-compare', majorId ? { major_id: majorId } : undefined),
}

// ================================================================ 预警
export const alertApi = {
  list: (params: Record<string, unknown>) => get<PageData<Alert>>('/alerts', params),
  grouped: () =>
    get<{ total: number; red: number; orange: number; yellow: number; groups: AlertGroup[] }>(
      '/alerts/grouped',
    ),
  stats: (days = 30) =>
    get<{
      total: number
      by_type: Record<string, number>
      by_level: Record<string, number>
      by_status: Record<string, number>
      on_time_rate: number | null
      should_handle: number
      on_time_closed: number
      trend: { date: string; total: number; red: number; orange: number; yellow: number }[]
      by_major: { major_id: number; major_name: string; total: number; red: number }[]
      age_buckets: Record<string, number>
    }>('/alerts/stats', { days }),
  detail: (id: number) => get<Alert & { actions: unknown[] }>(`/alerts/${id}`),
  claim: (id: number) => post<void>(`/alerts/${id}/claim`),
  close: (id: number, reason?: string) => post<void>(`/alerts/${id}/close`, { reason }),
  ignore: (id: number, reason: string) => post<void>(`/alerts/${id}/ignore`, { reason }),
  transfer: (id: number, to_user_id: number) =>
    post<void>(`/alerts/${id}/transfer`, { to_user_id }),
  scan: () => post<{ created: number; merged: number }>('/alerts/scan', {}),
  todo: () => get<TodoData>('/dashboard/todo'),}

// ================================================================ 报表
export const reportApi = {
  list: (params: Record<string, unknown>) => get<PageData<ReportItem>>('/reports', params),
  detail: (id: number) => get<ReportItem>(`/reports/${id}`),
  submit: (data: {
    sub_id: number
    type: string
    period_start?: string
    progress?: number
    content: Record<string, unknown>
  }) => post<{ id: number; status: string }>('/reports', data),
  void: (id: number, reason: string) => post<void>(`/reports/${id}/void`, { reason }),
  weeklyDraft: (sub_id: number, week_start?: string) =>
    post<{ summary: string; next_week: string; progress: number; progress_delta: number }>(
      '/reports/weekly-draft',
      { sub_id, week_start },
    ),
  pending: () => get<PendingReport[]>('/reports/pending'),
  timeliness: (params: { start: string; end: string; type?: string; major_id?: number }) =>
    get<{
      should_submit: number
      on_time: number
      late: number
      missing: number
      rate: number | null
    }>('/reports/timeliness', params),
  timelinessDetail: (params: {
    start: string
    end: string
    type?: string
    major_id?: number
    state?: 'MISSING' | 'LATE' | 'ON_TIME'
    page?: number
    page_size?: number
  }) => get<PageData<TimelinessDetailRow>>('/reports/timeliness/detail', params),
  period: (type: string, day?: string) =>
    get<{
      type: string
      period_start: string
      period_end: string
      deadline: string
    }>('/reports/period', { type, day }),
}

// ================================================================ 风险
export const riskApi = {
  list: (params: Record<string, unknown>) => get<PageData<RiskItem>>('/risks', params),
  create: (data: Partial<RiskItem>) => post<{ id: number }>('/risks', data),
  update: (id: number, data: Partial<RiskItem>) => put<void>(`/risks/${id}`, data),
  close: (id: number) => post<void>(`/risks/${id}/close`),
  remove: (id: number) => del<void>(`/risks/${id}`),
}

// ================================================================ 系统
export const systemApi = {
  users: (params: Record<string, unknown>) => get<PageData<UserItem>>('/system/users', params),
  /** 人员下拉（任意登录用户可用，只返回 id/name/staff_no/college） */
  pickUsers: (params?: { keyword?: string; college_id?: number; limit?: number }) =>
    get<{ id: number; name: string; staff_no: string; college_id: number | null; college_name: string | null }[]>(
      '/system/users/picker',
      params,
    ),
  createUser: (data: Record<string, unknown>) => post<{ id: number }>('/system/users', data),
  updateUser: (id: number, data: Record<string, unknown>) =>
    put<void>(`/system/users/${id}`, data),
  disableUser: (id: number) => del<void>(`/system/users/${id}`),
  roles: () =>
    get<{ id: number; code: string; name: string; is_system: boolean; permission_count: number }[]>(
      '/system/roles',
    ),
  createRole: (data: { code: string; name: string; remark?: string }) =>
    post<{ id: number }>('/system/roles', data),
  permissions: () =>
    get<
      { id: number; code: string; name: string; type: string; parent_id: number | null }[]
    >('/system/permissions'),
  setRolePermissions: (roleId: number, permission_ids: number[]) =>
    put<void>(`/system/roles/${roleId}/permissions`, { permission_ids }),
  getRolePermissions: (roleId: number) =>
    get<{ role_id: number; role_code: string; permission_ids: number[] }>(
      `/system/roles/${roleId}/permissions`,
    ),
  grants: (user_id?: number) =>
    get<
      {
        id: number
        user_id: number
        user_name: string
        staff_no: string
        role_id: number
        role_code: string
        role_name: string
        scope_type: string
        scope_id: number | null
        valid_from: string | null
        valid_to: string | null
      }[]
    >('/system/grants', user_id ? { user_id } : undefined),
  addGrant: (data: Record<string, unknown>) => post<{ id: number }>('/system/grants', data),
  removeGrant: (id: number) => del<void>(`/system/grants/${id}`),
  configs: () =>
    get<{ key: string; value: string; value_type: string; remark: string | null }[]>(
      '/system/configs',
    ),
  setConfig: (key: string, value: string) => put<void>(`/system/configs/${key}`, { value }),
  holidays: (year?: number) =>
    get<{ id: number; date: string; type: string; name: string | null }[]>(
      '/system/holidays',
      year ? { year } : undefined,
    ),
  addHolidays: (dates: string[], type = 'HOLIDAY', name?: string) =>
    post<{ added: number; total: number }>('/system/holidays', { dates, type, name }),
  removeHoliday: (id: number) => del<void>(`/system/holidays/${id}`),
  auditLogs: (params: Record<string, unknown>) =>
    get<PageData<Record<string, unknown>>>('/system/audit-logs', params),
  /** 下载导入模板 */
  downloadTemplate: (kind: 'user' | 'college') =>
    download(`/system/import/templates/${kind}`),
  /** 批量导入用户 */
  importUsers: async (file: File, opts?: { default_password?: string; update_existing?: boolean }) => {
    const form = new FormData()
    form.append('file', file)
    const resp = await http.post('/system/import/users', form, {
      params: opts,
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    return resp as unknown as ImportResult
  },
  /** 批量导入学院 */
  importColleges: async (file: File, updateExisting = false) => {
    const form = new FormData()
    form.append('file', file)
    const resp = await http.post('/system/import/colleges', form, {
      params: { update_existing: updateExisting },
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    return resp as unknown as ImportResult
  },
}

// ================================================================ 导出
export const exportApi = {
  majors: (params: Record<string, unknown>) => download('/export/majors', params),
  progress: (params: Record<string, unknown>) => download('/export/progress', params),
  reports: (params: Record<string, unknown>) => download('/export/reports', params),
  fields: () =>
    get<{
      major: { key: string; label: string }[]
      progress: { key: string; label: string }[]
      report: { key: string; label: string }[]
    }>('/export/fields'),
}

// ================================================================ 附件
export interface AttachmentItem {
  id: number
  biz_type: string
  biz_id: number
  file_name: string
  file_size: number
  size_text: string
  file_ext: string
  mime_type: string
  download_count: number
  uploader_id: number
  uploader_name: string | null
  uploaded_at: string
}

export const attachmentApi = {
  /** 上传附件 */
  upload: async (file: File, bizType: string, bizId: number) => {
    const form = new FormData()
    form.append('file', file)
    form.append('biz_type', bizType)
    form.append('biz_id', String(bizId))
    const resp = await http.post('/attachments/upload', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    return resp as unknown as AttachmentItem
  },

  /** 附件列表 */
  list: (bizType: string, bizId: number) =>
    get<AttachmentItem[]>('/attachments', { biz_type: bizType, biz_id: bizId }),

  /** 下载地址 */
  downloadUrl: (id: number) => `/api/attachments/${id}/download`,

  /** 预览地址 */
  previewUrl: (id: number) => `/api/attachments/${id}/preview`,

  /** 删除 */
  remove: (id: number) => del<void>(`/attachments/${id}`),

  /** 存储统计 */
  stats: () =>
    get<{
      count: number
      size_mb: number
      storage_dir: string
      disk_free_mb: number
      disk_total_mb: number
    }>('/attachments/stats'),
}
