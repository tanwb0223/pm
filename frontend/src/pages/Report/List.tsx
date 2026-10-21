import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  App,
  Badge,
  Button,
  DatePicker,
  Descriptions,
  Drawer,
  Dropdown,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
} from 'antd'
import { ExportOutlined, EyeOutlined } from '@ant-design/icons'
import type { Dayjs } from 'dayjs'

import DataTable from '@/components/DataTable'
import PageHeader from '@/components/PageHeader'
import { ReportStatusTag } from '@/components/Tags'
import { exportApi, reportApi } from '@/api'
import { REPORT_TYPE_LABEL, fmtDate, fmtDateTime } from '@/lib/constants'
import { useDictStore } from '@/stores/dict'
import { useAuthStore, useCan } from '@/stores/auth'
import type { ReportItem } from '@/types'

interface ListParams {
  type?: string
  status?: string
  major_id?: number
  submitted_from?: string
  submitted_to?: string
  page: number
  page_size: number
}

export default function ReportListPage() {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const can = useCan()
  const majors = useDictStore((s) => s.majors)

  const [params, setParams] = useState<ListParams>({ page: 1, page_size: 20 })
  const [range, setRange] = useState<[Dayjs | null, Dayjs | null] | null>(null)
  const [detailOpen, setDetailOpen] = useState(false)
  const [detail, setDetail] = useState<ReportItem | null>(null)
  const [voidTarget, setVoidTarget] = useState<ReportItem | null>(null)
  const [form] = Form.useForm()

  const { data, isLoading } = useQuery({
    queryKey: ['report-list', params],
    queryFn: () => reportApi.list(params as unknown as Record<string, unknown>),
  })

  const voidMut = useMutation({
    mutationFn: ({ id, reason }: { id: number; reason: string }) => reportApi.void(id, reason),
    onSuccess: () => {
      message.success('报表已作废')
      setVoidTarget(null)
      form.resetFields()
      void queryClient.invalidateQueries({ queryKey: ['report-list'] })
      void queryClient.invalidateQueries({ queryKey: ['report-pending'] })
    },
  })

  const doExport = (fmt: 'xlsx' | 'pdf') => {
    const p: Record<string, unknown> = { fmt }
    if (params.type) p.type = params.type
    if (params.submitted_from) p.start = params.submitted_from
    if (params.submitted_to) p.end = params.submitted_to
    void exportApi.reports(p)
  }

  const columns = [
    { title: '子项目', dataIndex: 'sub_name', key: 'sub_name', ellipsis: true },
    {
      title: '类型',
      dataIndex: 'type',
      key: 'type',
      width: 80,
      render: (v: string) => <Tag>{REPORT_TYPE_LABEL[v] ?? v}</Tag>,
    },
    {
      title: '周期',
      key: 'period',
      width: 200,
      render: (_: unknown, r: ReportItem) => `${fmtDate(r.period_start)} ~ ${fmtDate(r.period_end)}`,
    },
    {
      title: '进度',
      dataIndex: 'progress',
      key: 'progress',
      width: 70,
      align: 'center' as const,
      render: (v: number | null) => (v === null ? '—' : `${v}%`),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 90,
      render: (v: string) => <ReportStatusTag status={v} />,
    },
    {
      title: '提交时间',
      dataIndex: 'submitted_at',
      key: 'submitted_at',
      width: 150,
      render: (v: string | null) => fmtDateTime(v),
    },
    {
      title: '更正',
      dataIndex: 'correct_count',
      key: 'correct_count',
      width: 70,
      align: 'center' as const,
      render: (v: number) =>
        v > 0 ? (
          <Tooltip title={`已更正 ${v} 次`}>
            <Badge count={v} color="blue" />
          </Tooltip>
        ) : (
          <span style={{ color: '#9ca3af' }}>0</span>
        ),
    },
    {
      title: '作废原因',
      dataIndex: 'void_reason',
      key: 'void_reason',
      width: 110,
      ellipsis: true,
      render: (v: string | null) =>
        v ? (
          <Tooltip title={v}>
            <Tag color="red">已作废</Tag>
          </Tooltip>
        ) : (
          '—'
        ),
    },
    {
      title: '操作',
      key: 'act',
      width: 130,
      render: (_: unknown, r: ReportItem) => (
        <Space size={4}>
          <Button
            type="link"
            size="small"
            icon={<EyeOutlined />}
            onClick={async () => {
              const d = await reportApi.detail(r.id)
              setDetail(d)
              setDetailOpen(true)
            }}
          >
            查看
          </Button>
          {can('report:void') && r.status === 'SUBMITTED' && (
            <Button type="link" size="small" danger onClick={() => setVoidTarget(r)}>
              作废
            </Button>
          )}
        </Space>
      ),
    },
  ]

  return (
    <div className="page-container">
      <PageHeader
        title="报表列表"
        subtitle="已提交的日报 / 周报 / 月报；同期重复提交视为更正"
        extra={
          <Dropdown
            menu={{
              items: [
                { key: 'xlsx', label: '导出 Excel' },
                { key: 'pdf', label: '导出 PDF' },
              ],
              onClick: ({ key }) => doExport(key as 'xlsx' | 'pdf'),
            }}
          >
            <Button icon={<ExportOutlined />}>导出</Button>
          </Dropdown>
        }
      />

      <Space wrap style={{ marginBottom: 16 }}>
        <Select
          allowClear
          placeholder="类型"
          style={{ width: 110 }}
          options={Object.entries(REPORT_TYPE_LABEL).map(([v, l]) => ({ value: v, label: l }))}
          onChange={(v) => setParams((p) => ({ ...p, type: v, page: 1 }))}
        />
        <Select
          allowClear
          placeholder="状态"
          style={{ width: 110 }}
          options={[
            { value: 'SUBMITTED', label: '已提交' },
            { value: 'VOIDED', label: '已作废' },
          ]}
          onChange={(v) => setParams((p) => ({ ...p, status: v, page: 1 }))}
        />
        <Select
          allowClear
          showSearch
          optionFilterProp="label"
          placeholder="大项目"
          style={{ width: 240 }}
          options={majors.map((m) => ({ value: m.id, label: m.name }))}
          onChange={(v) => setParams((p) => ({ ...p, major_id: v, page: 1 }))}
        />
        <DatePicker.RangePicker
          value={range}
          onChange={(v) => {
            setRange(v)
            setParams((p) => ({
              ...p,
              submitted_from: v?.[0] ? v[0].format('YYYY-MM-DD') : undefined,
              submitted_to: v?.[1] ? v[1].format('YYYY-MM-DD') : undefined,
              page: 1,
            }))
          }}
        />
      </Space>

      <DataTable
        rowKey="id"
        size="small"
        loading={isLoading}
        columns={columns}
        dataSource={data?.items ?? []}
        pagination={{
          current: params.page,
          pageSize: params.page_size,
          total: data?.total ?? 0,
          showSizeChanger: true,
          showTotal: (t) => `共 ${t} 条`,
          onChange: (page, page_size) => setParams((p) => ({ ...p, page, page_size })),
        }}
      />

      {/* 详情抽屉 */}
      <Drawer
        title={`报表详情（${detail ? REPORT_TYPE_LABEL[detail.type] ?? detail.type : ''}）`}
        width={560}
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
      >
        {detail && (
          <>
            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label="子项目">{detail.sub_name ?? detail.sub_id}</Descriptions.Item>
              <Descriptions.Item label="类型">{REPORT_TYPE_LABEL[detail.type]}</Descriptions.Item>
              <Descriptions.Item label="周期">
                {fmtDate(detail.period_start)} ~ {fmtDate(detail.period_end)}
              </Descriptions.Item>
              <Descriptions.Item label="进度">
                {detail.progress === null ? '—' : `${detail.progress}%`}
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                <ReportStatusTag status={detail.status} />
              </Descriptions.Item>
              <Descriptions.Item label="提交时间">{fmtDateTime(detail.submitted_at)}</Descriptions.Item>
              <Descriptions.Item label="更正次数">{detail.correct_count}</Descriptions.Item>
              {detail.void_reason && (
                <Descriptions.Item label="作废原因">{detail.void_reason}</Descriptions.Item>
              )}
            </Descriptions>
            <Descriptions
              column={1}
              size="small"
              bordered
              style={{ marginTop: 16 }}
              title="报表内容"
            >
              {Object.entries(detail.content ?? {}).map(([k, v]) => (
                <Descriptions.Item key={k} label={k}>
                  {typeof v === 'string' && v ? (
                    <div style={{ whiteSpace: 'pre-wrap' }}>{v}</div>
                  ) : (
                    String(v ?? '—')
                  )}
                </Descriptions.Item>
              ))}
            </Descriptions>
          </>
        )}
      </Drawer>

      {/* 作废 Modal */}
      <Modal
        title={`作废报表（${voidTarget ? (REPORT_TYPE_LABEL[voidTarget.type] ?? '') : ''} ${voidTarget ? fmtDate(voidTarget.period_start) : ''}）`}
        open={voidTarget !== null}
        confirmLoading={voidMut.isPending}
        okText="确认作废"
        okButtonProps={{ danger: true }}
        onCancel={() => {
          setVoidTarget(null)
          form.resetFields()
        }}
        onOk={async () => {
          const { reason } = await form.validateFields()
          if (voidTarget) voidMut.mutate({ id: voidTarget.id, reason })
        }}
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="reason"
            label="作废理由（必填，将记入操作日志）"
            rules={[{ required: true, message: '请填写作废理由' }]}
          >
            <Input.TextArea rows={3} maxLength={500} showCount placeholder="例如：重复提交 / 内容有误需重填" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
