import { Progress, Tag, Tooltip } from 'antd'

import {
  ALERT_LEVEL_COLOR,
  ALERT_LEVEL_LABEL,
  ALERT_STATUS_LABEL,
  ALERT_TYPE_LABEL,
  MAJOR_STATUS_COLOR,
  MAJOR_STATUS_LABEL,
  REPORT_STATUS_LABEL,
  RISK_LEVEL_LABEL,
  SUB_STATUS_COLOR,
  SUB_STATUS_LABEL,
  deviationColor,
} from '@/lib/constants'

/** 大项目状态 */
export function MajorStatusTag({ status }: { status: string }) {
  return <Tag color={MAJOR_STATUS_COLOR[status]}>{MAJOR_STATUS_LABEL[status] ?? status}</Tag>
}

/** 子项目状态 */
export function SubStatusTag({ status }: { status: string }) {
  return <Tag color={SUB_STATUS_COLOR[status]}>{SUB_STATUS_LABEL[status] ?? status}</Tag>
}

/** 预警等级 */
export function AlertLevelTag({ level }: { level: string }) {
  return (
    <Tag color={ALERT_LEVEL_COLOR[level]} style={{ color: '#fff', border: 'none' }}>
      {ALERT_LEVEL_LABEL[level] ?? level}
    </Tag>
  )
}

/** 预警类型 */
export function AlertTypeTag({ type }: { type: string }) {
  return (
    <Tooltip title={type}>
      <Tag>{ALERT_TYPE_LABEL[type] ?? type}</Tag>
    </Tooltip>
  )
}

/** 预警状态 */
export function AlertStatusTag({ status }: { status: string }) {
  const colorMap: Record<string, string> = {
    PENDING: 'volcano',
    CLAIMED: 'blue',
    CLOSED: 'green',
    IGNORED: 'default',
  }
  return <Tag color={colorMap[status]}>{ALERT_STATUS_LABEL[status] ?? status}</Tag>
}

/** 报表状态 */
export function ReportStatusTag({ status }: { status: string }) {
  const colorMap: Record<string, string> = {
    DRAFT: 'default',
    SUBMITTED: 'green',
    VOIDED: 'red',
  }
  return <Tag color={colorMap[status]}>{REPORT_STATUS_LABEL[status] ?? status}</Tag>
}

/** 风险等级 */
export function RiskLevelTag({ level }: { level: string }) {
  const colorMap: Record<string, string> = { HIGH: 'red', MEDIUM: 'orange', LOW: 'blue' }
  return <Tag color={colorMap[level]}>{RISK_LEVEL_LABEL[level] ?? level}</Tag>
}

/** 偏差数字（带颜色，滞后 < -10% 橙色，<= -25% 红色） */
export function DeviationText({ value }: { value: number | null }) {
  if (value === null) return <span style={{ color: '#9ca3af' }}>—</span>
  return (
    <span style={{ color: deviationColor(value), fontWeight: Math.abs(value) >= 10 ? 600 : 400 }}>
      {value > 0 ? `+${value}` : value}
    </span>
  )
}

/** 进度条的语义色：跑完绿、正常靛蓝、偏低橙、严重滞后红 */
function progressColor(v: number) {
  if (v >= 100) return '#10b981'
  if (v >= 60) return '#4f46e5'
  if (v >= 30) return '#d48806'
  return '#ef4444'
}

/**
 * 表格里的进度单元格（进度条 + 百分比）
 *
 * 全站统一同一个形态，避免各页面有的只有进度条、有的只有数字、
 * 有的进度条宽度写死导致列宽一改就错位。
 * 进度条用 flex 自适应单元格宽度，百分比右对齐等宽，纵向能对齐。
 */
export function ProgressCell({ value }: { value: number | null }) {
  if (value === null || value === undefined) {
    return <span style={{ color: '#9ca3af' }}>—</span>
  }
  return (
    <div className="cell-progress">
      <Progress
        percent={value}
        size="small"
        showInfo={false}
        strokeColor={progressColor(value)}
        style={{ marginBottom: 0, flex: '1 1 auto', minWidth: 0 }}
      />
      <span className="cell-progress-value">{value}%</span>
    </div>
  )
}
