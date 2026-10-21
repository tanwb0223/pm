import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Alert, Descriptions, Drawer, Empty, Space, Spin, Tag, Tooltip, Typography } from 'antd'
import type { EChartsOption } from '@/lib/echarts'

import Chart from '@/components/Chart'
import { progressApi } from '@/api'
import { fmtDate } from '@/lib/constants'

const { Text } = Typography

interface Props {
  subId: number | null
  subName?: string
  open: boolean
  onClose: () => void
}

/**
 * 子项目进度趋势抽屉（文档 3.2(6)）
 *
 * 两条线：实际进度 vs 按时间应有进度
 * 两线交叉处即「开始变慢」的时间点，用 markPoint 标出
 */
export default function SubTrendDrawer({ subId, subName, open, onClose }: Props) {
  const { data, isLoading } = useQuery({
    queryKey: ['sub-trend', subId],
    queryFn: () => progressApi.subTrend(subId!, 120),
    enabled: open && subId !== null,
  })

  const option = useMemo<EChartsOption>(() => {
    const points = data?.points ?? []
    const dates = points.map((p) => p.date.slice(5))

    const series: NonNullable<EChartsOption['series']> = [
      {
        name: '按时间应有',
        type: 'line',
        smooth: true,
        symbol: 'none',
        lineStyle: { width: 2, type: 'dashed', color: '#9ca3af' },
        itemStyle: { color: '#9ca3af' },
        data: points.map((p) => p.expected),
        connectNulls: false,
      },
      {
        name: '实际进度',
        type: 'line',
        // 进度是阶梯式变化的：填报后才跳变，不是匀速爬升
        step: 'end',
        smooth: false,
        symbol: 'circle',
        symbolSize: 5,
        showSymbol: false,
        lineStyle: { width: 3, color: '#4f46e5' },
        itemStyle: { color: '#4f46e5' },
        areaStyle: { opacity: 0.18, color: '#4f46e5' },
        data: points.map((p) => p.actual),
        connectNulls: true,
        z: 5,
      },
    ]

    // 标出首次滞后点
    if (data?.first_lag_date) {
      const idx = points.findIndex((p) => p.date === data.first_lag_date)
      if (idx >= 0) {
        ;(series[1] as { markPoint?: unknown }).markPoint = {
          symbol: 'pin',
          symbolSize: 46,
          itemStyle: { color: '#ef4444' },
          label: { fontSize: 10, color: '#fff', formatter: '开始滞后' },
          data: [{ coord: [dates[idx], points[idx].actual], value: '滞后' }],
        }
      }
    }

    return {
      tooltip: {
        trigger: 'axis',
        formatter: (params: unknown) => {
          const arr = params as { axisValue: string; seriesName: string; value: number | null }[]
          if (!arr?.length) return ''
          const p = points.find((x) => x.date.slice(5) === arr[0].axisValue)
          if (!p) return ''
          const lines = [
            `<b>${p.date}</b>`,
            `按时间应有：${p.expected ?? '—'}%`,
            `实际进度：${p.actual ?? '未填报'}%`,
          ]
          if (p.deviation !== null) {
            const color = p.deviation < -25 ? '#ef4444' : p.deviation < -10 ? '#f59e0b' : '#10b981'
            lines.push(`偏差：<span style="color:${color};font-weight:600">${p.deviation > 0 ? '+' : ''}${p.deviation}</span>`)
          }
          if (p.is_free_day) lines.push('<span style="color:#9ca3af">（免填日）</span>')
          return lines.join('<br/>')
        },
      },
      legend: { data: ['按时间应有', '实际进度'], right: 0, top: 0, itemWidth: 14, itemHeight: 8 },
      grid: { left: 8, right: 20, top: 40, bottom: 8, containLabel: true },
      xAxis: {
        type: 'category',
        boundaryGap: false,
        data: dates,
        axisLine: { lineStyle: { color: '#e5e7eb' } },
        axisLabel: {
          color: '#9ca3af',
          fontSize: 11,
          interval: Math.max(0, Math.ceil(dates.length / 10) - 1),
        },
      },
      yAxis: {
        type: 'value',
        min: -3,
        max: 100,
        axisLabel: { color: '#9ca3af', fontSize: 11, formatter: '{value}%' },
        splitLine: { lineStyle: { color: '#f3f4f6' } },
      },
      series,
    }
  }, [data])

  return (
    <Drawer
      title={subName ? `进度趋势 · ${subName}` : '进度趋势'}
      width={880}
      open={open}
      onClose={onClose}
      destroyOnHidden
    >
      {isLoading ? (
        <div style={{ textAlign: 'center', padding: 80 }}>
          <Spin />
        </div>
      ) : !data ? (
        <Empty description="暂无数据" />
      ) : !data.has_plan ? (
        <Alert
          type="warning"
          showIcon
          message="该子项目未设置计划起止时间"
          description="没有计划时间就无法计算「按时间应有进度」，请先在子项目里补上计划开始和结束日期。"
        />
      ) : (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Descriptions size="small" column={4} bordered>
            <Descriptions.Item label="计划起止">
              {fmtDate(data.plan_start)} ~ {fmtDate(data.plan_end)}
            </Descriptions.Item>
            <Descriptions.Item label="当前进度">
              <Text strong>{data.current_progress}%</Text>
            </Descriptions.Item>
            <Descriptions.Item label="首次滞后">
              {data.first_lag_date ? (
                <Tooltip title="从这一天起，实际进度开始低于按时间应有进度的 10%">
                  <Tag color="red">{data.first_lag_date}</Tag>
                </Tooltip>
              ) : (
                <Tag color="green">未滞后</Tag>
              )}
            </Descriptions.Item>
            <Descriptions.Item label="连续滞后">
              {data.lag_days > 0 ? (
                <Tooltip title="截至今天，连续低于滞后线的天数">
                  <Tag color="orange">{data.lag_days} 天</Tag>
                </Tooltip>
              ) : (
                <span style={{ color: '#9ca3af' }}>—</span>
              )}
            </Descriptions.Item>
          </Descriptions>

          {data.first_lag_date && (
            <Alert
              type="warning"
              showIcon
              style={{ fontSize: 13 }}
              message={`从 ${data.first_lag_date} 开始变慢，已连续滞后 ${data.lag_days} 天`}
              description={`当前应完成 ${data.points[data.points.length - 1]?.expected ?? '—'}%，实际完成 ${data.current_progress}%。`}
            />
          )}

          <Chart option={option} height={380} />

          <div style={{ color: '#9ca3af', fontSize: 12, lineHeight: 1.8 }}>
            说明：虚线是「按时间算现在应该做到多少」，实线是「实际填了多少」。
            两条线的距离就是偏差，实线开始落后于虚线的位置就是变慢的起点。
            滞后判定口径与预警 AL-01 一致（偏差低于 −10%）。
          </div>
        </Space>
      )}
    </Drawer>
  )
}
