import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Card,
  Col,
  Collapse,
  DatePicker,
  Progress,
  Row,
  Segmented,
  Select,
  Space,
  Statistic,
  Tag,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import dayjs, { type Dayjs } from 'dayjs'

import PageHeader from '@/components/PageHeader'
import DataTable from '@/components/DataTable'
import { reportApi } from '@/api'
import { PAGE_SIZE, tablePagination } from '@/lib/pagination'
import { REPORT_TYPE_LABEL, fmtDate, fmtDateTime } from '@/lib/constants'
import { useDictStore } from '@/stores/dict'
import type { TimelinessDetailRow } from '@/types'

const STATE_COLOR: Record<string, string> = {
  MISSING: '#ef4444',
  LATE: '#f59e0b',
  ON_TIME: '#10b981',
}

const STATE_OPTIONS = [
  { value: 'ALL', label: '全部' },
  { value: 'MISSING', label: '缺交' },
  { value: 'LATE', label: '迟交' },
  { value: 'ON_TIME', label: '按期' },
]

export default function TimelinessPage() {
  const majors = useDictStore((s) => s.majors)
  const [range, setRange] = useState<[Dayjs, Dayjs]>([dayjs().startOf('month'), dayjs()])
  const [type, setType] = useState<string | undefined>()
  const [majorId, setMajorId] = useState<number | undefined>()
  const [state, setState] = useState<string>('ALL')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(PAGE_SIZE)

  const start = range[0].format('YYYY-MM-DD')
  const end = range[1].format('YYYY-MM-DD')

  const { data, isLoading } = useQuery({
    queryKey: ['report-timeliness', start, end, type, majorId],
    queryFn: () => reportApi.timeliness({ start, end, type, major_id: majorId }),
  })

  const { data: detail, isLoading: detailLoading } = useQuery({
    queryKey: ['report-timeliness-detail', start, end, type, majorId, state, page, pageSize],
    queryFn: () =>
      reportApi.timelinessDetail({
        start,
        end,
        type,
        major_id: majorId,
        state: state === 'ALL' ? undefined : (state as 'MISSING' | 'LATE' | 'ON_TIME'),
        page,
        page_size: pageSize,
      }),
  })

  const rate = data?.rate ?? null
  const rateColor =
    rate === null ? '#e5e7eb' : rate >= 90 ? '#10b981' : rate >= 70 ? '#f59e0b' : '#ef4444'

  const columns: ColumnsType<TimelinessDetailRow> = [
    {
      title: '子项目',
      dataIndex: 'sub_name',
      ellipsis: true,
      render: (v: string) => <span style={{ fontWeight: 500 }}>{v}</span>,
    },
    { title: '类型', dataIndex: 'type_label', width: 80 },
    {
      title: '应报周期',
      width: 215,
      render: (_, r) => (
        <span className="cell-datetime">
          {fmtDate(r.period_start)} ~ {fmtDate(r.period_end)}
        </span>
      ),
    },
    {
      title: '截止时间',
      dataIndex: 'deadline',
      width: 140,
      render: (v: string) => <span className="cell-datetime">{fmtDateTime(v)}</span>,
    },
    {
      title: '提交时间',
      dataIndex: 'submitted_at',
      width: 140,
      render: (v: string | null) => (v ? <span className="cell-datetime">{fmtDateTime(v)}</span> : '—'),
    },
    {
      title: '状态',
      dataIndex: 'state',
      width: 90,
      render: (v: string, r) => (
        <Tag color={STATE_COLOR[v]} style={{ color: '#fff', border: 'none' }}>
          {r.state_label}
        </Tag>
      ),
    },
    {
      title: '逾期',
      dataIndex: 'overdue_days',
      width: 90,
      align: 'right',
      render: (v: number, r) =>
        r.state === 'ON_TIME' ? (
          <span style={{ color: '#9ca3af' }}>—</span>
        ) : (
          <span style={{ color: '#ef4444', fontWeight: 600 }}>{v} 天</span>
        ),
    },
  ]

  return (
    <div className="page-container">
      <PageHeader
        title="报表及时率"
        subtitle="及时率 = 按期提交数 ÷ 应提交数；截止未到期的期次不计入分母；作废按缺交计"
      />

      <Space wrap style={{ marginBottom: 16 }}>
        <DatePicker.RangePicker
          value={range}
          allowClear={false}
          onChange={(v) => {
            if (v && v[0] && v[1]) {
              setRange([v[0], v[1]])
              setPage(1)
            }
          }}
        />
        <Select
          allowClear
          placeholder="报表类型"
          style={{ width: 120 }}
          options={Object.entries(REPORT_TYPE_LABEL).map(([v, l]) => ({ value: v, label: l }))}
          value={type}
          onChange={(v) => {
            setType(v)
            setPage(1)
          }}
        />
        <Select
          allowClear
          showSearch
          optionFilterProp="label"
          placeholder="大项目"
          style={{ width: 240 }}
          options={majors.map((m) => ({ value: m.id, label: m.name }))}
          value={majorId}
          onChange={(v) => {
            setMajorId(v)
            setPage(1)
          }}
        />
      </Space>

      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}>
          <Card size="small">
            <Statistic title="应提交" value={data?.should_submit ?? 0} loading={isLoading} />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="按期提交"
              value={data?.on_time ?? 0}
              valueStyle={{ color: '#10b981' }}
              loading={isLoading}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="迟交"
              value={data?.late ?? 0}
              valueStyle={{ color: '#f59e0b' }}
              loading={isLoading}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="缺交"
              value={data?.missing ?? 0}
              valueStyle={{ color: '#ef4444' }}
              loading={isLoading}
            />
          </Card>
        </Col>
      </Row>

      <Card size="small" style={{ marginBottom: 16 }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: '12px 0',
          }}
        >
          {rate === null ? (
            <span style={{ color: '#6b7280', fontSize: 16 }}>暂无数据</span>
          ) : (
            <Progress
              type="dashboard"
              percent={rate}
              size={180}
              strokeColor={rateColor}
              format={(p) => (
                <span style={{ fontSize: 24, fontWeight: 700, color: rateColor }}>{p}%</span>
              )}
            />
          )}
        </div>
      </Card>

      <Card
        title="逐期明细"
        size="small"
        style={{ marginBottom: 16 }}
        extra={
          <Segmented
            size="small"
            value={state}
            options={STATE_OPTIONS}
            onChange={(v) => {
              setState(String(v))
              setPage(1)
            }}
          />
        }
      >
        <DataTable<TimelinessDetailRow>
          rowKey={(r) => `${r.sub_id}-${r.type}-${r.period_start}`}
          density="compact"
          loading={detailLoading}
          columns={columns}
          dataSource={detail?.items ?? []}
          minTableWidth={1100}
          pagination={tablePagination({
            current: page,
            pageSize,
            total: detail?.total ?? 0,
            onChange: (p, ps) => {
              setPage(p)
              setPageSize(ps)
            },
            unit: '期',
          })}
          locale={{ emptyText: '该区间内没有应报期次' }}
        />
      </Card>

      <Collapse
        items={[
          {
            key: 'rule',
            label: '计算口径说明',
            children: (
              <div style={{ color: '#4b5563', lineHeight: 1.9 }}>
                <p>及时率 = 按期提交数 ÷ 应提交数。</p>
                <p>
                  分子：提交时间不晚于截止时间（日报当日 24:00，周报次周一 12:00，月报次月第 3
                  个工作日 18:00）；逾期补填计入「迟交」，不计入分子。
                </p>
                <p>
                  分母：统计区间内「应提交且截止时间已到」的期次。截止时间未到的当期不计入；
                  法定节假日免填日不计入；未开始 / 已结项的子项目不计入。
                </p>
                <p>已作废的报表该期重算为「缺交」，需重新提交。</p>
              </div>
            ),
          },
        ]}
      />
    </div>
  )
}
