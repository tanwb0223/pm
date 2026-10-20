import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Button, Input, Space, Table, Tag, Tooltip, Result, Typography } from 'antd'
import { SearchOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'

import PageHeader from '@/components/PageHeader'
import { systemApi } from '@/api'
import { fmtDateTime } from '@/lib/constants'
import { useAuthStore, useCan } from '@/stores/auth'

interface AuditRow {
  id: number
  operator_id: number | null
  action: string
  target_type: string | null
  target_id: number | null
  detail: Record<string, unknown> | null
  ip: string | null
  created_at: string
}

const ACTION_COLOR: Record<string, string> = {
  POST: 'green',
  PUT: 'blue',
  PATCH: 'blue',
  DELETE: 'red',
}

/** 操作日志（只读） */
export default function AuditPage() {
  const can = useCan()

  const [action, setAction] = useState('')
  const [targetType, setTargetType] = useState('')
  const [operatorId, setOperatorId] = useState<number | undefined>()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)

  const allowed = can('system:audit')

  const { data, isLoading } = useQuery({
    queryKey: ['audit', { action, targetType, operatorId, page, pageSize }],
    queryFn: () =>
      systemApi.auditLogs({
        action: action || undefined,
        target_type: targetType || undefined,
        operator_id: operatorId,
        page,
        page_size: pageSize,
      }),
    enabled: allowed,
  })

  const columns: ColumnsType<AuditRow> = [
    {
      title: '时间',
      dataIndex: 'created_at',
      width: 160,
      render: (v: string) => fmtDateTime(v),
    },
    {
      title: '操作人',
      dataIndex: 'operator_id',
      width: 90,
      render: (v: number | null) => (v === null ? <Tag>系统</Tag> : `#${v}`),
    },
    {
      title: '动作',
      dataIndex: 'action',
      width: 180,
      render: (v: string) => {
        const method = v.split('_').pop() ?? ''
        const prefix = v.slice(0, v.length - method.length - 1)
        return (
          <Space size={4}>
            <Tag color={ACTION_COLOR[method] ?? 'default'}>{method}</Tag>
            <span style={{ fontSize: 13 }}>{prefix}</span>
          </Space>
        )
      },
    },
    {
      title: '目标',
      key: 'target',
      width: 160,
      render: (_, row) =>
        row.target_type ? (
          <span>
            {row.target_type}
            {row.target_id !== null && (
              <span style={{ color: '#8c8c8c' }}> #{row.target_id}</span>
            )}
          </span>
        ) : (
          '—'
        ),
    },
    {
      title: '详情',
      key: 'detail',
      render: (_, row) => {
        if (!row.detail) return '—'
        const text = JSON.stringify(row.detail)
        const short = text.length > 60 ? text.slice(0, 60) + '…' : text
        return (
          <Tooltip title={<Typography.Text copyable style={{ color: '#fff' }}>{text}</Typography.Text>}>
            <code style={{ fontSize: 12 }}>{short}</code>
          </Tooltip>
        )
      },
    },
    { title: 'IP', dataIndex: 'ip', width: 130, render: (v: string | null) => v ?? '—' },
  ]

  if (!allowed) {
    return (
      <div className="page-container">
        <Result status="403" title="无权限" subTitle="需要 system:audit 权限才能查看操作日志" />
      </div>
    )
  }

  return (
    <div className="page-container">
      <PageHeader
        title="操作日志"
        subtitle="记录所有写操作（新增/修改/删除），含 4xx 失败请求，便于排查越权尝试"
      />

      <Space style={{ marginBottom: 16 }} wrap>
        <Input
          allowClear
          prefix={<SearchOutlined />}
          placeholder="动作（如 PROJECT_POST）"
          style={{ width: 200 }}
          value={action}
          onChange={(e) => {
            setAction(e.target.value)
            setPage(1)
          }}
        />
        <Input
          allowClear
          placeholder="目标类型（如 majors）"
          style={{ width: 180 }}
          value={targetType}
          onChange={(e) => {
            setTargetType(e.target.value)
            setPage(1)
          }}
        />
        <Input
          allowClear
          type="number"
          placeholder="操作人 ID"
          style={{ width: 130 }}
          value={operatorId ?? ''}
          onChange={(e) => {
            const v = e.target.value ? Number(e.target.value) : undefined
            setOperatorId(v)
            setPage(1)
          }}
        />
      </Space>

      <Table<AuditRow>
        rowKey="id"
        columns={columns}
        dataSource={(data?.items ?? []) as unknown as AuditRow[]}
        loading={isLoading}
        pagination={{
          current: page,
          pageSize,
          total: data?.total ?? 0,
          showSizeChanger: true,
          showTotal: (t) => `共 ${t} 条`,
          onChange: (p, ps) => {
            setPage(p)
            setPageSize(ps)
          },
        }}
        size="middle"
      />
    </div>
  )
}
