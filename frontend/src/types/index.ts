/** 全局类型定义 */

export interface ApiResp<T = unknown> {
  code: number
  message: string
  data: T
}

export interface PageData<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

// ---------------------------------------------------------------- 认证
export interface LoginResult {
  access_token: string
  token_type: string
  user?: { id: number; name: string; staff_no?: string }
}

export interface AuthProviderInfo {
  kind: string
  display_name: string
  flow: 'password' | 'redirect' | 'post'
  callback_path: string
}

export interface MeInfo {
  id: number
  staff_no: string
  name: string
  college_id: number | null
  roles: string[]
  permissions: string[]
  scopes: { type: string; id: number | null }[]
}

// ---------------------------------------------------------------- 组织
export interface College {
  id: number
  name: string
  code: string | null
  parent_id: number | null
  sort: number
}

export interface UserItem {
  id: number
  staff_no: string
  name: string
  college_id: number | null
  college_name: string | null
  phone: string | null
  email: string | null
  status: string
  last_login_at: string | null
  has_password: boolean
}

// ---------------------------------------------------------------- 项目
export type MajorStatus = 'ACTIVE' | 'CLOSED' | 'ABORTED'
export type SubStatus = 'NOT_STARTED' | 'IN_PROGRESS' | 'ABORTED' | 'CLOSED'

export interface IncompleteItem {
  code: string
  label: string
  detail: string
}

export interface Major {
  id: number
  name: string
  type: string
  partner_company: string | null
  build_period: string | null
  owner_id: number | null
  owner_name: string | null
  status: MajorStatus
  original_start: string | null
  original_end: string | null
  budget_total: number | null
  budget_used: number | null
  acceptance_status: string
  college_count: number
  sub_count: number
  incomplete_reason: { complete: boolean; items: IncompleteItem[] } | null
  created_at: string
  colleges?: MajorCollege[]
}

export interface MajorCollege {
  college_id: number
  college_name: string
  college_owner_id: number | null
  college_owner_name: string | null
  sub_count: number
}

export interface Sub {
  id: number
  major_id: number
  major_name: string | null
  college_id: number
  college_name: string | null
  name: string
  owner_id: number | null
  owner_name: string | null
  plan_start: string | null
  plan_end: string | null
  weight: number | null
  status: SubStatus
  current_progress: number
  last_update_at: string | null
  created_at: string
}

/** 进度总表一行 */
export interface ProgressRow {
  id: number
  name: string
  major_id: number
  major_name: string | null
  college_id: number
  college_name: string | null
  owner_id: number | null
  owner_name: string | null
  status: SubStatus
  current_progress: number
  time_progress: number | null
  deviation: number | null
  plan_start: string | null
  plan_end: string | null
  weight: number | null
  last_update_at: string | null
  days_since_update: number | null
  is_stagnant: boolean
  is_near_deadline: boolean
  has_plan: boolean
}

export interface Completeness {
  major_id: number
  complete: boolean
  items: IncompleteItem[]
  summary: Record<string, number>
}

// ---------------------------------------------------------------- 预警
export type AlertLevel = 'RED' | 'ORANGE' | 'YELLOW'
export type AlertStatus = 'PENDING' | 'CLAIMED' | 'CLOSED' | 'IGNORED'

export interface Alert {
  id: number
  alert_type: string
  level: AlertLevel
  status: AlertStatus
  sub_id: number | null
  sub_name: string | null
  major_id: number | null
  college_id: number | null
  trigger_at: string
  deadline_at: string | null
  claimed_by: number | null
  claimed_at: string | null
  closed_at: string | null
  close_reason: string | null
  escalated_at: string | null
  merged_count: number
  dedup_key: string
}

export interface AlertGroup {
  major_id: number | null
  major_name: string | null
  college_id: number | null
  college_name: string | null
  red: number
  orange: number
  yellow: number
  total: number
}

export interface TodoData {
  pending_alerts: number
  lagging_subs: number
  alerts: Alert[]
}

/** 我的督办接口返回的轻量预警项 */
export interface TodoAlert {
  id: number
  alert_type: string
  level: AlertLevel
  status: AlertStatus
  sub_id: number | null
  sub_name: string | null
  major_id: number | null
  college_id: number | null
  deadline_at: string | null
  trigger_at: string
  merged_count: number
}

// ---------------------------------------------------------------- 报表
export interface ReportItem {
  id: number
  sub_id: number
  sub_name: string | null
  type: 'DAILY' | 'WEEKLY' | 'MONTHLY'
  period_start: string
  period_end: string
  progress: number | null
  content: Record<string, unknown>
  status: 'DRAFT' | 'SUBMITTED' | 'VOIDED'
  submitted_at: string | null
  submitted_by: number | null
  correct_count: number
  void_reason: string | null
  voided_at: string | null
}

export interface PendingReport {
  sub_id: number
  sub_name: string
  type: 'DAILY' | 'WEEKLY' | 'MONTHLY'
  type_label: string
  period_start: string
  period_end: string
  deadline: string
  overdue_days: number
}

/** 及时率明细一行 */
export interface TimelinessDetailRow {
  sub_id: number
  sub_name: string
  major_id: number
  college_id: number
  type: 'DAILY' | 'WEEKLY' | 'MONTHLY'
  type_label: string
  period_start: string
  period_end: string
  deadline: string
  report_id: number | null
  state: 'MISSING' | 'LATE' | 'ON_TIME'
  state_label: string
  submitted_at: string | null
  overdue_days: number
}

// ---------------------------------------------------------------- 风险
export interface RiskItem {
  id: number
  major_id: number
  sub_id: number | null
  level: 'HIGH' | 'MEDIUM' | 'LOW'
  level_label: string
  description: string
  owner_id: number | null
  owner_name: string | null
  plan_close_date: string
  actual_close_date: string | null
  status: 'OPEN' | 'CLOSED'
  overdue_days: number
  created_at: string
}
