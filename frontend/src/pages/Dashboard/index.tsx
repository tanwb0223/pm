import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Button, Card, Col, Empty, Row, Space, Tag } from 'antd'
import type { ColumnsType } from 'antd/es/table'

import { alertApi, reportApi, subApi } from '@/api'
import PageHeader from '@/components/PageHeader'
import Table from '@/components/Table'
import { AlertLevelTag, AlertStatusTag, AlertTypeTag } from '@/components/Tags'
import { REPORT_TYPE_LABEL, daysLeft, fmtDateTime } from '@/lib/constants'
import type { Alert, PendingReport } from '@/types'

/** 我的督办：待办预警 + 待填报表 + 关键数字 */
export default function DashboardPage() {
  const { data: todo, isLoading: todoLoading } = useQuery({
    queryKey: ['dashboard-todo'],
    queryFn: alertApi.todo,
    refetchInterval: 60 * 1000,
  })

  const { data: pendingReports, isLoading: pendingLoading } = useQuery({
    queryKey: ['pending-reports'],
    queryFn: reportApi.pending,
  })

  const { data: inProgress } = useQuery({
    queryKey: ['subs-in-progress-count'],
    queryFn: () => subApi.list({ status: 'IN_PROGRESS', page: 1, page_size: 1 }),
  })

  const pendingCount = pendingReports?.length ?? 0

  const alertColumns: ColumnsType<Alert> = [
    {
      title: '类型',
      dataIndex: 'alert_type',
      key: 'type',
      width: 110,
      render: (_v, r) => <AlertTypeTag type={r.alert_type} />,
    },
    { title: '等级', dataIndex: 'level', key: 'level', width: 90, render: (_v, r) => <AlertLevelTag level={r.level} /> },
    { title: '状态', dataIndex: 'status', key: 'status', width: 90, render: (_v, r) => <AlertStatusTag status={r.status} /> },
    {
      title: '触发时间',
      dataIndex: 'trigger_at',
      key: 'trigger_at',
      width: 150,
      render: (v: string) => <span className="cell-datetime">{fmtDateTime(v)}</span>,
    },
    {
      title: '处置时限',
      dataIndex: 'deadline_at',
      key: 'deadline_at',
      width: 120,
      render: (v: string | null) => {
        const left = daysLeft(v)
        if (left === null) return '—'
        if (left < 0) return <Tag color="red">超 {Math.abs(left)} 天</Tag>
        if (left === 0) return <Tag color="orange">今天到期</Tag>
        return <span>剩 {left} 天</span>
      },
    },
    {
      title: '操作',
      key: 'action',
      width: 90,
      render: (_v, r) => (
        <Link to="/alert/center">去处置</Link>
      ),
    },
  ]

  const reportColumns: ColumnsType<PendingReport> = [
    { title: '子项目', dataIndex: 'sub_name', key: 'sub_name' },
    {
      title: '类型',
      dataIndex: 'type',
      key: 'type',
      width: 90,
      render: (t: string) => REPORT_TYPE_LABEL[t] ?? t,
    },
    {
      title: '周期',
      key: 'period',
      width: 200,
      render: (_v, r) => `${r.period_start} ~ ${r.period_end}`,
    },
    {
      title: '截止时间',
      dataIndex: 'deadline',
      key: 'deadline',
      width: 170,
      render: (v: string, r) => {
        if (r.status === 'OVERDUE') {
          return <Tag color="red">逾期 {r.overdue_days} 天</Tag>
        }
        return (
          <Space direction="vertical" size={0}>
            <span className="cell-datetime">{fmtDateTime(v)}</span>
            <Tag color="blue">
              {r.days_left <= 0 ? '今日截止' : `还剩 ${r.days_left} 天`}
            </Tag>
          </Space>
        )
      },
    },
    {
      title: '操作',
      key: 'action',
      width: 90,
      render: (_v, r) => <Link to="/report/fill">去填报</Link>,
    },
  ]

  return (
    <div className="page-container">
      <PageHeader title="我的督办" subtitle="需要您关注和处理的事项" />

      {/* 指标卡 */}
      <Row gutter={16} style={{ marginBottom: 20 }}>
        <Col xs={24} sm={12} xl={6}>
          <div className="metric-card is-danger">
            <div className="metric-label">待处置预警</div>
            <div className="metric-value is-danger">
              {todoLoading ? '—' : (todo?.pending_alerts ?? 0)}
            </div>
          </div>
        </Col>
        <Col xs={24} sm={12} xl={6}>
          <div className="metric-card is-warning">
            <div className="metric-label">滞后子项目</div>
            <div className="metric-value is-warning">
              {todoLoading ? '—' : (todo?.lagging_subs ?? 0)}
            </div>
          </div>
        </Col>
        <Col xs={24} sm={12} xl={6}>
          <div className="metric-card">
            <div className="metric-label">待填报表</div>
            <div className="metric-value" style={{ color: '#4f46e5' }}>
              {pendingLoading ? '—' : pendingCount}
            </div>
          </div>
        </Col>
        <Col xs={24} sm={12} xl={6}>
          <div className="metric-card is-success">
            <div className="metric-label">进行中子项目</div>
            <div className="metric-value is-success">
              {inProgress ? (inProgress.total ?? 0) : '—'}
            </div>
          </div>
        </Col>
      </Row>

      {/* 待办预警 */}
      <Card title="待办预警" style={{ marginBottom: 16 }}>
        <Table<Alert>
          rowKey="id"
          columns={alertColumns}
          dataSource={(todo?.alerts ?? []).slice(0, 10)}
          loading={todoLoading}
          pagination={false}
          locale={{ emptyText: <Empty description="暂无待办" /> }}
        />      </Card>

      {/* 待填报表 */}
      <Card title="待填报表">
        <Table<PendingReport>
          rowKey={(r) => `${r.sub_id}-${r.type}-${r.period_start}`}
          columns={reportColumns}
          dataSource={pendingReports ?? []}
          loading={pendingLoading}
          pagination={false}
          locale={{ emptyText: <Empty description="暂无待办" /> }}
        />      </Card>
    </div>
  )
}
