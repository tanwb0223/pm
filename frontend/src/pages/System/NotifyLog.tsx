import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Input, Result, Select, Space, Tag, Typography } from 'antd'
import { SearchOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'

import DataTable from '@/components/DataTable'
import PageHeader from '@/components/PageHeader'
import { systemApi } from '@/api'
import { fmtDateTime } from '@/lib/constants'
import { useCan } from '@/stores/auth'
import { PAGE_SIZE, tablePagination } from '@/lib/pagination'

interface NotifyRow {
  id: number
  biz_type: string
  biz_id: number | null
  channel: string
  receiver_id: number
  receiver_name: string | null
  status: string
  error: string | null
  sent_at: string
}

const CHANNEL_LABEL: Record<string, string> = {
  EMAIL: '邮件',
  SMS: '短信',
  INNER: '站内',
}

const CHANNEL_COLOR: Record<string, string> = {
  EMAIL: 'blue',
  SMS: 'purple',
  INNER: 'default',
}

const STATUS_LABEL: Record<string, string> = {
  SUCCESS: '成功',
  FAILED: '失败',
  SKIPPED: '跳过',
  PENDING: '待发',
  SENDING: '发送中',
}

const STATUS_COLOR: Record<string, string> = {
  SUCCESS: 'green',
  FAILED: 'red',
  SKIPPED: 'gold',
  PENDING: 'default',
  SENDING: 'processing',
}

/** 通知投递日志（只读，运维排查用） */
export default function NotifyLogPage() {
  const can = useCan()
  const allowed = can('system:audit')

  const [channel, setChannel] = useState<string | undefined>()
  const [status, setStatus] = useState<string | undefined>()
  const [receiverId, setReceiverId] = useState<number | undefined>()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(PAGE_SIZE)

  const { data, isLoading } = useQuery({
    queryKey: ['notify-logs', { channel, status, receiverId, page, pageSize }],
    queryFn: () =>
      systemApi.notifyLogs({
        channel,
        status,
        receiver_id: receiverId,
        page,
        page_size: pageSize,
      }),
    enabled: allowed,
  })

  const columns: ColumnsType<NotifyRow> = [
    {
      title: '时间',
      dataIndex: 'sent_at',
      width: 160,
      render: (v: string) => fmtDateTime(v),
    },
    {
      title: '通道',
      dataIndex: 'channel',
      width: 90,
      render: (v: string) => (
        <Tag color={CHANNEL_COLOR[v] ?? 'default'}>{CHANNEL_LABEL[v] ?? v}</Tag>
      ),
    },
    {
      title: '接收人',
      key: 'receiver',
      width: 140,
      render: (_, r) => (
        <span>
          {r.receiver_name ?? `#${r.receiver_id}`}
          <span style={{ color: '#9ca3af', fontSize: 12 }}> #{r.receiver_id}</span>
        </span>
      ),
    },
    {
      title: '业务',
      key: 'biz',
      width: 140,
      render: (_, r) => (
        <span>
          {r.biz_type}
          {r.biz_id !== null && <span style={{ color: '#9ca3af' }}> #{r.biz_id}</span>}
        </span>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: (v: string) => <Tag color={STATUS_COLOR[v] ?? 'default'}>{STATUS_LABEL[v] ?? v}</Tag>,
    },
    {
      title: '失败/跳过原因',
      dataIndex: 'error',
      render: (v: string | null) =>
        v ? <Typography.Text type="secondary" style={{ fontSize: 12 }}>{v}</Typography.Text> : '—',
    },
  ]

  if (!allowed) {
    return (
      <div className="page-container">
        <Result status="403" title="无权限" subTitle="需要 system:audit 权限才能查看通知日志" />
      </div>
    )
  }

  return (
    <div className="page-container">
      <PageHeader
        title="通知日志"
        subtitle="邮件 / 短信 / 站内消息的投递记录；失败记录由后台每 10 分钟自动重投"
      />

      <Space style={{ marginBottom: 16 }} wrap>
        <Select
          allowClear
          placeholder="通道"
          style={{ width: 120 }}
          value={channel}
          onChange={(v) => {
            setChannel(v)
            setPage(1)
          }}
          options={[
            { value: 'EMAIL', label: '邮件' },
            { value: 'SMS', label: '短信' },
            { value: 'INNER', label: '站内' },
          ]}
        />
        <Select
          allowClear
          placeholder="状态"
          style={{ width: 120 }}
          value={status}
          onChange={(v) => {
            setStatus(v)
            setPage(1)
          }}
          options={[
            { value: 'SUCCESS', label: '成功' },
            { value: 'FAILED', label: '失败' },
            { value: 'SKIPPED', label: '跳过' },
            { value: 'PENDING', label: '待发' },
          ]}
        />
        <Input
          allowClear
          type="number"
          prefix={<SearchOutlined />}
          placeholder="接收人 ID"
          style={{ width: 150 }}
          value={receiverId ?? ''}
          onChange={(e) => {
            setReceiverId(e.target.value ? Number(e.target.value) : undefined)
            setPage(1)
          }}
        />
      </Space>

      <DataTable<NotifyRow>
        rowKey="id"
        columns={columns}
        dataSource={data?.items ?? []}
        loading={isLoading}
        minTableWidth={900}
        pagination={tablePagination({
          current: page,
          pageSize,
          total: data?.total ?? 0,
          onChange: (p, ps) => {
            setPage(p)
            setPageSize(ps)
          },
        })}
      />
    </div>
  )
}
