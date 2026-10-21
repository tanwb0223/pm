import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Card, Col, Progress, Row, Select, Space, Tag, Tooltip } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import type { EChartsOption } from '@/lib/echarts'

import Chart from '@/components/Chart'
import DataTable from '@/components/DataTable'
import PageHeader from '@/components/PageHeader'
import { progressApi } from '@/api'
import { deviationColor } from '@/lib/constants'
import { useDictStore } from '@/stores/dict'
import type { CollegeCompareRow } from '@/types'

/**
 * 学院横向对比（文档 3.2(6)）
 *
 * 把各学院「平均慢了多少、几个滞后、更新及不及时」摆在一起
 */
export default function CollegeComparePage() {
  const majors = useDictStore((s) => s.majors)
  const [majorId, setMajorId] = useState<number | undefined>()

  const { data = [], isLoading } = useQuery({
    queryKey: ['college-compare', majorId],
    queryFn: () => progressApi.collegeCompare(majorId),
  })

  // 平均偏差柱状图（负值向左）
  const devOption = useMemo<EChartsOption>(() => {
    const rows = [...data].sort((a, b) => (a.avg_deviation ?? 0) - (b.avg_deviation ?? 0))
    return {
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        formatter: (p: unknown) => {
          const arr = p as { name: string; value: number }[]
          if (!arr?.length) return ''
          const v = arr[0].value
          return `${arr[0].name}<br/>平均偏差：<b>${v > 0 ? '+' : ''}${v}</b> 个百分点`
        },
      },
      grid: { left: 8, right: 40, top: 16, bottom: 8, containLabel: true },
      xAxis: {
        type: 'value',
        axisLabel: { color: '#6b7280', fontSize: 11 },
        splitLine: { lineStyle: { color: '#f3f4f6' } },
      },
      yAxis: {
        type: 'category',
        data: rows.map((r) => r.college_name),
        axisLine: { lineStyle: { color: '#e5e7eb' } },
        axisLabel: { color: '#4b5563', fontSize: 12 },
      },
      series: [
        {
          type: 'bar',
          barMaxWidth: 22,
          data: rows.map((r) => ({
            value: r.avg_deviation ?? 0,
            itemStyle: {
              color: deviationColor(r.avg_deviation),
              borderRadius: r.avg_deviation != null && r.avg_deviation < 0 ? [3, 0, 0, 3] : [0, 3, 3, 0],
            },
          })),
          label: {
            show: true,
            position: 'right',
            fontSize: 11,
            color: '#4b5563',
            formatter: (p: unknown) => {
              const v = (p as { value: number }).value
              return `${v > 0 ? '+' : ''}${v}`
            },
          },
          markLine: {
            silent: true,
            symbol: 'none',
            lineStyle: { color: '#ef4444', type: 'dashed', width: 1 },
            label: { formatter: '滞后线 -10', fontSize: 10, color: '#ef4444' },
            data: [{ xAxis: -10 }],
          },
        },
      ],
    }
  }, [data])

  // 滞后子项目数
  const lagOption = useMemo<EChartsOption>(() => {
    const rows = [...data].sort((a, b) => b.lagging_count - a.lagging_count)
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      legend: { data: ['滞后', '严重滞后'], right: 0, top: 0, itemWidth: 10, itemHeight: 10 },
      grid: { left: 8, right: 16, top: 36, bottom: 8, containLabel: true },
      xAxis: {
        type: 'category',
        data: rows.map((r) => r.college_name),
        axisLabel: { color: '#6b7280', fontSize: 10, interval: 0, rotate: rows.length > 4 ? 20 : 0 },
        axisLine: { lineStyle: { color: '#e5e7eb' } },
      },
      yAxis: {
        type: 'value',
        minInterval: 1,
        axisLabel: { color: '#6b7280', fontSize: 11 },
        splitLine: { lineStyle: { color: '#f3f4f6' } },
      },
      series: [
        {
          name: '滞后',
          type: 'bar',
          barMaxWidth: 24,
          stack: 'lag',
          data: rows.map((r) => r.lagging_count),
          itemStyle: { color: '#f59e0b' },
        },
        {
          name: '严重滞后',
          type: 'bar',
          barMaxWidth: 24,
          stack: 'lag',
          data: rows.map((r) => r.severe_count),
          itemStyle: { color: '#ef4444' },
        },
      ],
    }
  }, [data])

  const columns: ColumnsType<CollegeCompareRow> = [
    {
      title: '学院',
      dataIndex: 'college_name',
      render: (v: string) => <span style={{ fontWeight: 500 }}>{v}</span>,
    },
    { title: '子项目数', dataIndex: 'total_subs', width: 100, align: 'center' },
    {
      title: '平均偏差',
      dataIndex: 'avg_deviation',
      width: 110,
      align: 'right',
      sorter: (a, b) => (a.avg_deviation ?? 0) - (b.avg_deviation ?? 0),
      render: (v: number | null) =>
        v === null ? (
          <span style={{ color: '#9ca3af' }}>—</span>
        ) : (
          <span style={{ color: deviationColor(v), fontWeight: Math.abs(v) >= 10 ? 600 : 400 }}>
            {v > 0 ? `+${v}` : v}
          </span>
        ),
    },
    {
      title: '滞后子项目',
      dataIndex: 'lagging_count',
      width: 110,
      align: 'center',
      sorter: (a, b) => a.lagging_count - b.lagging_count,
      render: (v: number) =>
        v > 0 ? <Tag color="orange">{v} 个</Tag> : <span style={{ color: '#9ca3af' }}>0</span>,
    },
    {
      title: '严重滞后',
      dataIndex: 'severe_count',
      width: 110,
      align: 'center',
      render: (v: number) =>
        v > 0 ? <Tag color="red">{v} 个</Tag> : <span style={{ color: '#9ca3af' }}>0</span>,
    },
    {
      title: '更新及时率',
      dataIndex: 'update_rate',
      width: 180,
      render: (v: number | null) => {
        if (v === null) return <span style={{ color: '#9ca3af' }}>—</span>
        return (
          <Tooltip title="21 个有效日内更新过的子项目占比">
            <Progress
              percent={v}
              size="small"
              strokeColor={v >= 90 ? '#10b981' : v >= 70 ? '#f59e0b' : '#ef4444'}
            />
          </Tooltip>
        )
      },
    },
  ]

  const worst = data[0]
  const lagTotal = data.reduce((s, r) => s + r.lagging_count, 0)
  const severeTotal = data.reduce((s, r) => s + r.severe_count, 0)

  return (
    <div className="page-container">
      <PageHeader
        title="学院对比"
        subtitle="横向比较各学院整体进度表现：平均慢了多少、有几个子项目滞后、更新及不及时"
        extra={
          <Select
            allowClear
            showSearch
            optionFilterProp="label"
            placeholder="全部大项目"
            style={{ width: 260 }}
            options={majors.map((m) => ({ value: m.id, label: m.name }))}
            value={majorId}
            onChange={setMajorId}
          />
        }
      />

      <Row gutter={16} style={{ marginBottom: 20 }}>
        <Col xs={12} xl={6}>
          <div className="metric-card">
            <div className="metric-label">参与学院</div>
            <div className="metric-value" style={{ color: '#4f46e5' }}>
              {data.length}
              <span className="metric-suffix">个</span>
            </div>
          </div>
        </Col>
        <Col xs={12} xl={6}>
          <div className="metric-card">
            <div className="metric-label">子项目总数</div>
            <div className="metric-value">
              {data.reduce((s, r) => s + (r.total_subs ?? 0), 0)}
              <span className="metric-suffix">个</span>
            </div>
          </div>
        </Col>
        <Col xs={12} xl={6}>
          <div className="metric-card is-warning">
            <div className="metric-label">滞后子项目</div>
            <div className="metric-value is-warning">
              {data.reduce((s, r) => s + (r.lagging_count ?? 0), 0)}
              <span className="metric-suffix">个</span>
            </div>
          </div>
        </Col>
        <Col xs={12} xl={6}>
          <div className="metric-card is-danger">
            <div className="metric-label">严重滞后</div>
            <div className="metric-value is-danger">
              {data.reduce((s, r) => s + (r.severe_count ?? 0), 0)}
              <span className="metric-suffix">个</span>
            </div>
          </div>
        </Col>
      </Row>

      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={12}>
          <Card title="平均偏差（负值表示滞后）" size="small">
            <Chart
              option={devOption}
              height={Math.max(220, data.length * 46)}
              loading={isLoading}
              empty={data.length === 0}
            />
          </Card>
        </Col>
        <Col span={12}>
          <Card title="滞后子项目数量" size="small">
            <Chart
              option={lagOption}
              height={Math.max(220, data.length * 46)}
              loading={isLoading}
              empty={data.length === 0}
            />
          </Card>
        </Col>
      </Row>

      <Card title="明细" size="small">
        <DataTable<CollegeCompareRow>
          rowKey="college_id"
          size="small"
          loading={isLoading}
          columns={columns}
          dataSource={data}
          minTableWidth={900}
          pagination={false}
          locale={{ emptyText: '暂无数据' }}
        />
      </Card>

      <div style={{ color: '#6b7280', fontSize: 12, marginTop: 12, lineHeight: 1.8 }}>
        口径说明：平均偏差为该学院所有非中止子项目偏差的简单平均（未按权重折算）；
        滞后指偏差低于 −10%，严重滞后指偏差低于或等于 −25%，与预警 AL-01 / AL-02 一致；
        更新及时率指 21 个有效日内更新过的子项目占比。
      </div>
    </div>
  )
}
