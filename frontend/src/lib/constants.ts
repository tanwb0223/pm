/**
 * 全局常量与格式化工具
 *
 * 约定：
 * - *_LABEL 为「枚举值 → 中文」映射，页面上直接用 `LABEL[value] ?? value` 兜底
 * - *_COLOR 为 antd Tag 的预设色名（red / orange / gold / blue / green / default ...）
 * - 偏差口径与后端预警规则一致：< -10% 滞后（橙），<= -25% 严重滞后（红）
 */
import dayjs from 'dayjs'

// ================================================================ 大项目
/** 项目类型（与后端 schemas/project.py 的 MajorType 保持一致） */
export const MAJOR_TYPES: string[] = [
  '产业学院共建',
  '实训基地建设',
  '订单班与现代学徒制',
  '课程与教材共建',
  '双师基地建设',
  '其他',
]

export const MAJOR_STATUS_LABEL: Record<string, string> = {
  ACTIVE: '进行中',
  CLOSED: '已结项',
  ABORTED: '已中止',
}

export const MAJOR_STATUS_COLOR: Record<string, string> = {
  ACTIVE: 'blue',
  CLOSED: 'green',
  ABORTED: 'default',
}

// ================================================================ 子项目
export const SUB_STATUS_LABEL: Record<string, string> = {
  NOT_STARTED: '未开始',
  IN_PROGRESS: '进行中',
  ABORTED: '已中止',
  CLOSED: '已结项',
}

export const SUB_STATUS_COLOR: Record<string, string> = {
  NOT_STARTED: 'default',
  IN_PROGRESS: 'blue',
  ABORTED: 'default',
  CLOSED: 'green',
}

// ================================================================ 预警
export const ALERT_LEVEL_LABEL: Record<string, string> = {
  RED: '红色',
  ORANGE: '橙色',
  YELLOW: '黄色',
}

export const ALERT_LEVEL_COLOR: Record<string, string> = {
  RED: '#cf1322',
  ORANGE: '#d46b08',
  YELLOW: '#d4b106',
}

export const ALERT_STATUS_LABEL: Record<string, string> = {
  PENDING: '待处置',
  CLAIMED: '已认领',
  CLOSED: '已关闭',
  IGNORED: '已忽略',
}

/** 11 条预警规则（与后端 alert_rules 的 code 一一对应） */
export const ALERT_TYPE_LABEL: Record<string, string> = {
  'AL-01': '进度滞后',
  'AL-02': '严重滞后',
  'AL-03': '进度停滞',
  'AL-04': '进度回调',
  'AL-05': '项目临期',
  'AL-06': '报表逾期',
  'AL-07': '连续未填',
  'AL-08': '风险超期',
  'AL-09': '责任空缺',
  'AL-10': '计划缺失',
  'AL-11': '长期零进度',
}

// ================================================================ 报表
export const REPORT_TYPE_LABEL: Record<string, string> = {
  DAILY: '日报',
  WEEKLY: '周报',
  MONTHLY: '月报',
}

export const REPORT_STATUS_LABEL: Record<string, string> = {
  DRAFT: '草稿',
  SUBMITTED: '已提交',
  VOIDED: '已作废',
}

// ================================================================ 风险
export const RISK_LEVEL_LABEL: Record<string, string> = {
  HIGH: '高',
  MEDIUM: '中',
  LOW: '低',
}

// ================================================================ 偏差口径
/** 滞后线：偏差低于 -10% 判为滞后（与 AL-01 同口径） */
export const DEVIATION_LAG = -10
/** 严重滞后线：偏差 <= -25% 判为严重滞后（与 AL-02 同口径） */
export const DEVIATION_SEVERE = -25

/**
 * 偏差数字的颜色
 * - 严重滞后（<= -25%）红
 * - 滞后（< -10%）橙
 * - 正常/超前 绿
 */
export function deviationColor(value: number | null | undefined): string {
  if (value === null || value === undefined) return '#bfbfbf'
  if (value <= DEVIATION_SEVERE) return '#cf1322'
  if (value < DEVIATION_LAG) return '#d46b08'
  return '#389e0d'
}

// ================================================================ 日期格式化
/** 日期：YYYY-MM-DD；空值显示破折号 */
export function fmtDate(value: string | null | undefined): string {
  if (!value) return '—'
  const d = dayjs(value)
  return d.isValid() ? d.format('YYYY-MM-DD') : '—'
}

/** 日期时间：YYYY-MM-DD HH:mm；空值显示破折号 */
export function fmtDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  const d = dayjs(value)
  return d.isValid() ? d.format('YYYY-MM-DD HH:mm') : '—'
}

/**
 * 距目标时间还有几天（按自然日取整）
 *
 * - 返回 null 表示没有时间或时间非法
 * - 0 表示今天到期
 * - 负数表示已超期多少天
 */
export function daysLeft(value: string | null | undefined): number | null {
  if (!value) return null
  const target = dayjs(value)
  if (!target.isValid()) return null
  return target.startOf('day').diff(dayjs().startOf('day'), 'day')
}
