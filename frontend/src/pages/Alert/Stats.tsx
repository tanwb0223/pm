import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Card, Col, Row, Select, Space, Statistic, Tag } from 'antd'
import type { EChartsOption } from '@/lib/echarts'

import Chart from '@/components/Chart'
import PageHeader from '@/components/PageHeader'
import { alertApi } from '@/api'
import { ALERT_LEVEL_LABEL, ALERT_STATUS_LABEL, ALERT_TYPE_LABEL } from '@/lib/constants'

const DAYS_OPTIONS = [
  { value: 7, label: '近 7 天' },
  { value: 30, label: '近 30 天' },
  { value: 90, label: '近 90 天' },
]

const LEVEL_COLOR: Record<string, string> = {
  RED: '#ef4444',
  ORANGE: '#f59e0b',
  YELLOW: '#eab308',
}

const STATUS_COLOR: Record<string, string> = {
  PENDING: '#ef4444',
  CLAIMED: '#4f46e5',
  CLOSED: '#10b981',
  IGNORED: '#9ca3af',
}

export default function AlertStatsPage() {
  const [days, setDays] = useState(30)

  const { data, isLoading } = useQuery({
    queryKey: ['alerts-stats', days],
    queryFn: () => alertApi.stats(days),
  })

  // ---------------------------------------------------------------- 趋势图
  const trendOption = useMemo<EChartsOption>(() => {
    const trend = data?.trend ?? []
    return {
      tooltip: { trigger: 'axis' },
      legend: { data: ['红色', '橙色', '黄色'], right: 0, top: 0, itemWidth: 10, itemHeight: 10 },
      grid: { left: 8, right: 16, top: 36, bottom: 8, containLabel: true },
      xAxis: {
        type: 'category',
        data: trend.map((t) => t.date.slice(5)),
        axisLine: { lineStyle: { color: '#e5e7eb' } },
        // 标签数量 = 数据点，超过 14 个则按比例抽稀
        axisLabel: {
          color: '#6b7280',
          fontSize: 11,
          interval: Math.max(0, Math.ceil(trend.length / 12) - 1),
          rotate: 0,
        },
      },
      yAxis: {
        type: 'value',
        minInterval: 1,
        splitLine: { lineStyle: { color: '#f3f4f6' } },
        axisLabel: { color: '#6b7280', fontSize: 11 },
      },
      series: [
        {
          name: '红色',
          type: 'line',
          smooth: true,
          symbol: 'circle',
          symbolSize: 5,
          data: trend.map((t) => t.red),
          itemStyle: { color: LEVEL_COLOR.RED },
          areaStyle: { opacity: 0.12 },
        },
        {
          name: '橙色',
          type: 'line',
          smooth: true,
          symbol: 'circle',
          symbolSize: 5,
          data: trend.map((t) => t.orange),
          itemStyle: { color: LEVEL_COLOR.ORANGE },
        },
        {
          name: '黄色',
          type: 'line',
          smooth: true,
          symbol: 'circle',
          symbolSize: 5,
          data: trend.map((t) => t.yellow),
          itemStyle: { color: LEVEL_COLOR.YELLOW },
        },
      ],
    }
  }, [data])

  // ---------------------------------------------------------------- 等级环形图
  const levelOption = useMemo<EChartsOption>(() => {
    const items = (['RED', 'ORANGE', 'YELLOW'] as const).map((lv) => ({
      name: ALERT_LEVEL_LABEL[lv],
      value: data?.by_level?.[lv] ?? 0,
      itemStyle: { color: LEVEL_COLOR[lv] },
    }))
    return {
      tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
      legend: { bottom: 0, itemWidth: 10, itemHeight: 10 },
      series: [
        {
          type: 'pie',
          radius: ['48%', '72%'],
          center: ['50%', '44%'],
          avoidLabelOverlap: true,
          itemStyle: { borderColor: '#fff', borderWidth: 2 },
          label: { show: false },
          emphasis: { label: { show: true, fontSize: 14, fontWeight: 'bold' } },
          data: items,
        },
      ],
    }
  }, [data])

  // ---------------------------------------------------------------- 类型横向柱状
  const typeOption = useMemo<EChartsOption>(() => {
    const entries = Object.entries(data?.by_type ?? {})
      .map(([k, v]) => ({ name: ALERT_TYPE_LABEL[k] ?? k, value: v }))
      .sort((a, b) => a.value - b.value)
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      grid: { left: 8, right: 32, top: 8, bottom: 8, containLabel: true },
      xAxis: {
        type: 'value',
        minInterval: 1,
        splitLine: { lineStyle: { color: '#f3f4f6' } },
        axisLabel: { color: '#6b7280', fontSize: 11 },
      },
      yAxis: {
        type: 'category',
        data: entries.map((e) => e.name),
        axisLine: { lineStyle: { color: '#e5e7eb' } },
        axisLabel: { color: '#4b5563', fontSize: 11 },
      },
      series: [
        {
          type: 'bar',
          barMaxWidth: 16,
          data: entries.map((e) => e.value),
          itemStyle: { color: '#4f46e5', borderRadius: [0, 3, 3, 0] },
          label: { show: true, position: 'right', fontSize: 11, color: '#4b5563' },
        },
      ],
    }
  }, [data])

  // ---------------------------------------------------------------- 按项目
  const majorOption = useMemo<EChartsOption>(() => {
    const rows = data?.by_major ?? []
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      legend: { data: ['预警总数', '其中红色'], right: 0, top: 0, itemWidth: 10, itemHeight: 10 },
      grid: { left: 8, right: 16, top: 36, bottom: 8, containLabel: true },
      xAxis: {
        type: 'category',
        data: rows.map((r) =>
          r.major_name.length > 10 ? `${r.major_name.slice(0, 10)}…` : r.major_name,
        ),
        axisLabel: { color: '#6b7280', fontSize: 10, interval: 0, rotate: rows.length > 4 ? 18 : 0 },
        axisLine: { lineStyle: { color: '#e5e7eb' } },
      },
      yAxis: {
        type: 'value',
        minInterval: 1,
        splitLine: { lineStyle: { color: '#f3f4f6' } },
        axisLabel: { color: '#6b7280', fontSize: 11 },
      },
      series: [
        {
          name: '预警总数',
          type: 'bar',
          barMaxWidth: 26,
          data: rows.map((r) => r.total),
          itemStyle: { color: '#8b5cf6', borderRadius: [3, 3, 0, 0] },
        },
        {
          name: '其中红色',
          type: 'bar',
          barMaxWidth: 26,
          data: rows.map((r) => r.red),
          itemStyle: { color: LEVEL_COLOR.RED, borderRadius: [3, 3, 0, 0] },
        },
      ],
    }
  }, [data])

  // ---------------------------------------------------------------- 处置时长
  const ageOption = useMemo<EChartsOption>(() => {
    const buckets = data?.age_buckets ?? {}
    const order = ['1天内', '1-3天', '3-7天', '7天以上']
    const colorMap: Record<string, string> = {
      '1天内': '#10b981',
      '1-3天': '#eab308',
      '3-7天': '#f59e0b',
      '7天以上': '#ef4444',
    }
    return {
      tooltip: { trigger: 'item', formatter: '{b}: {c} 条 ({d}%)' },
      legend: { bottom: 0, itemWidth: 10, itemHeight: 10 },
      series: [
        {
          type: 'pie',
          radius: '62%',
          center: ['50%', '44%'],
          itemStyle: { borderColor: '#fff', borderWidth: 2 },
          label: { fontSize: 11, color: '#4b5563', formatter: '{b}\n{c} 条' },
          data: order
            .map((k) => ({
              name: k,
              value: buckets[k] ?? 0,
              itemStyle: { color: colorMap[k] },
            }))
            .filter((d) => d.value > 0),
        },
      ],
    }
  }, [data])

  const rate = data?.on_time_rate
  const rateColor =
    rate == null ? '#6b7280' : rate >= 90 ? '#10b981' : rate >= 70 ? '#f59e0b' : '#ef4444'
  const total = data?.total ?? 0
  const trendEmpty = !data?.trend?.some((t) => t.total > 0)
  const ageEmpty = !Object.values(data?.age_buckets ?? {}).some((v) => v > 0)

  return (
    <div className="page-container">
      <PageHeader
        title="预警统计"
        subtitle="按时处置率 = 按期关闭数 ÷ 应处置数（不含黄色预警与已忽略，含已升级）"
        extra={
          <Select value={days} style={{ width: 120 }} options={DAYS_OPTIONS} onChange={setDays} />
        }
      />

      {/* 顶部统计卡 */}
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}>
          <Card size="small">
            <Statistic title="预警总数" value={total} loading={isLoading} />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="按时处置率"
              value={rate ?? undefined}
              suffix={rate != null ? '%' : ''}
              precision={1}
              valueStyle={{ color: rateColor }}
              loading={isLoading}
              formatter={(v) => (v === null || v === undefined ? '暂无数据' : `${v}`)}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic title="应处置数" value={data?.should_handle ?? 0} loading={isLoading} />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="按期关闭数"
              value={data?.on_time_closed ?? 0}
              valueStyle={{ color: '#10b981' }}
              loading={isLoading}
            />
          </Card>
        </Col>
      </Row>

      {/* 趋势 */}
      <Card title="预警趋势" size="small" style={{ marginBottom: 16 }}>
        <Chart option={trendOption} height={260} loading={isLoading} empty={trendEmpty} />
      </Card>

      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={10}>
          <Card title="按等级分布" size="small">
            <Chart option={levelOption} height={280} loading={isLoading} empty={total === 0} />
          </Card>
        </Col>
        <Col span={14}>
          <Card title="按类型分布" size="small">
            <Chart
              option={typeOption}
              height={280}
              loading={isLoading}
              empty={Object.keys(data?.by_type ?? {}).length === 0}
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={16}>
        <Col span={14}>
          <Card title="按大项目分布（Top 10）" size="small">
            <Chart
              option={majorOption}
              height={280}
              loading={isLoading}
              empty={(data?.by_major ?? []).length === 0}
            />
          </Card>
        </Col>
        <Col span={10}>
          <Card
            title="未处置时长分布"
            size="small"
            extra={
              <Space size={4}>
                {Object.entries(ALERT_STATUS_LABEL).map(([k, label]) => (
                  <Tag key={k} color={STATUS_COLOR[k]} style={{ color: '#fff', border: 'none' }}>
                    {label} {data?.by_status?.[k] ?? 0}
                  </Tag>
                ))}
              </Space>
            }
          >
            <Chart option={ageOption} height={280} loading={isLoading} empty={ageEmpty} />
          </Card>
        </Col>
      </Row>
    </div>
  )
}
