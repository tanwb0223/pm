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
import AttachmentPanel from '@/components/AttachmentPanel'
import PageHeader from '@/components/PageHeader'
import { ReportStatusTag } from '@/components/Tags'
import { exportApi, reportApi } from '@/api'
import { REPORT_TYPE_LABEL, fmtDate, fmtDateTime } from '@/lib/constants'
import { useDictStore } from '@/stores/dict'
import { useAuthStore, useCan } from '@/stores/auth'
import type { ReportItem } from '@/types'
import { tablePagination } from '@/lib/pagination'

interface ListParams {
  type?: string
  status?: string
  major_id?: number
  submitted_from?: string
  submitted_to?: string
  page: number
  page_size: number
}

/** 报表内容字段的中文标签（原来是直接显示 today / next_week 这类英文 key） */
const CONTENT_LABEL: Record<string, string> = {
  today: '今日进展',
  tomorrow: '明日计划',
  summary: '本周工作总结',
  completed: '完成情况',
  next_week: '下周计划',
  next_month: '下月计划',
  issues: '问题与风险',
  suggestions: '意见建议',
  attachments: '佐证材料',
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
    // 需求 3.6「按当前筛选条件导出」：大项目筛选原来漏传，导出的总是全量
    if (params.major_id) p.major_id = params.major_id
    if (params.submitted_from) p.start = params.submitted_from
    if (params.submitted_to) p.end = params.submitted_to
    void exportApi.reports(p)
    message.success('已开始导出')
  }

  // ---------------------------------------------------------------- 更正
  // 需求 3.4「交上去 3 天内填报人可以自己改，超过 3 天要学院负责人开修改权限」。
  // 早期后端有更正规则，但前端只有「查看 / 作废」，更正这条路走不通。
  const [correctTarget, setCorrectTarget] = useState<ReportItem | null>(null)
  const [correctable, setCorrectable] = useState<{
    can_correct: boolean
    reason: string
    days_left: number
  } | null>(null)
  const [correctForm] = Form.useForm()
  const correctMut = useMutation({
    mutationFn: async (values: Record<string, unknown>) => {
      if (!correctTarget) throw new Error('无更正对象')
      const content = { ...(correctTarget.content ?? {}) }
      for (const [k, v] of Object.entries(values)) {
        if (k === 'progress') continue
        content[k] = v
      }
      return reportApi.submit({
        sub_id: correctTarget.sub_id,
        type: correctTarget.type,
        period_start: correctTarget.period_start,
        progress: values.progress as number | undefined,
        content,
      })
    },
    onSuccess: () => {
      message.success('已更正')
      setCorrectTarget(null)
      correctForm.resetFields()
      void queryClient.invalidateQueries({ queryKey: ['report-list'] })
      void queryClient.invalidateQueries({ queryKey: ['report-pending'] })
      void queryClient.invalidateQueries({ queryKey: ['report-timeliness'] })
    },
  })

  /** 打开更正弹窗：先问后端这张报表现在能不能改（3 天内 / 或已开更正放行） */
  const openCorrect = async (r: ReportItem) => {
    try {
      const c = await reportApi.correctable(r.id)
      setCorrectable(c)
      if (!c.can_correct) {
        message.warning(c.reason || '该报表当前不可更正')
        return
      }
      setCorrectTarget(r)
      correctForm.resetFields()
      const initial: Record<string, unknown> = {
        progress: r.progress ?? undefined,
      }
      for (const f of correctFields(r)) {
        initial[f.key] = (r.content as Record<string, unknown> | undefined)?.[f.key]
      }
      correctForm.setFieldsValue(initial)
    } catch {
      /* 错误提示由拦截器统一弹出 */
    }
  }

  /** 更正表单字段：按报表类型给中文标签 */
  const correctFields = (r: ReportItem | null) => {
    if (!r) return []
    if (r.type === 'DAILY') {
      return [
        { key: 'today', label: '今日进展', required: true },
        { key: 'tomorrow', label: '明日计划', required: false },
        { key: 'issues', label: '问题与需协调事项', required: false },
      ]
    }
    if (r.type === 'WEEKLY') {
      return [
        { key: 'completed', label: '本周完成', required: true },
        { key: 'next_week', label: '下周计划', required: false },
        { key: 'issues', label: '问题与风险', required: false },
      ]
    }
    return [
      { key: 'completed', label: '本月完成', required: true },
      { key: 'next_month', label: '下月计划', required: false },
      { key: 'issues', label: '问题与风险', required: false },
      { key: 'suggestions', label: '意见建议', required: false },
    ]
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
      render: (v: string | null) => <span className="cell-datetime">{fmtDateTime(v)}</span>,
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
      width: 190,
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
          {can('report:fill') && r.status === 'SUBMITTED' && (
            <Button type="link" size="small" onClick={() => openCorrect(r)}>
              更正
            </Button>
          )}
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
        loading={isLoading}
        columns={columns}
        dataSource={data?.items ?? []}
        pagination={tablePagination({
          current: params.page,
          pageSize: params.page_size,
          total: data?.total ?? 0,
          onChange: (page, page_size) => setParams((p) => ({ ...p, page, page_size })),
        })}
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
              bordered
              style={{ marginTop: 16 }}
              title="报表内容"
            >
              {Object.entries(detail.content ?? {}).map(([k, v]) => (
                <Descriptions.Item key={k} label={CONTENT_LABEL[k] ?? k}>
                  {typeof v === 'string' && v ? (
                    <div style={{ whiteSpace: 'pre-wrap' }}>{v}</div>
                  ) : (
                    String(v ?? '—')
                  )}
                </Descriptions.Item>
              ))}
            </Descriptions>

            {/* 佐证材料（需求 3.4 把附件列为报表字段；后端支持 biz_type=REPORT，
                但前端此前没有任何入口，材料只能挂到子项目下，验收时对不上账） */}
            <div style={{ marginTop: 16 }}>
              <AttachmentPanel
                bizType="REPORT"
                bizId={detail.id}
                readOnly={!can('report:fill')}
              />
            </div>
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

      {/* 更正 Modal（需求 3.4：3 天内自己改；超期需学院负责人开更正放行） */}
      <Modal
        title={`更正报表（${correctTarget ? (REPORT_TYPE_LABEL[correctTarget.type] ?? '') : ''} ${correctTarget ? fmtDate(correctTarget.period_start) : ''}）`}
        open={correctTarget !== null}
        confirmLoading={correctMut.isPending}
        okText="提交更正"
        width={620}
        onCancel={() => {
          setCorrectTarget(null)
          correctForm.resetFields()
        }}
        onOk={() => correctForm.submit()}
        destroyOnHidden
      >
        {correctable && (
          <div
            style={{
              background: '#eff6ff',
              border: '1px solid #bfdbfe',
              borderRadius: 4,
              padding: '8px 12px',
              marginBottom: 12,
              fontSize: 13,
              color: '#1e40af',
            }}
          >
            自更正期内（剩余 {correctable.days_left} 天）。更正后及时率仍按第一次提交是否按期计算。
          </div>
        )}
        <Form form={correctForm} layout="vertical" onFinish={(v) => correctMut.mutate(v)}>
          {correctFields(correctTarget).map((f) => (
            <Form.Item
              key={f.key}
              name={f.key}
              label={f.label}
              rules={f.required ? [{ required: true, message: `请填写${f.label}` }] : undefined}
            >
              <Input.TextArea rows={2} maxLength={2000} />
            </Form.Item>
          ))}
          <Form.Item name="progress" label="进度（可选，填写会同步到子项目）">
            <Input type="number" min={0} max={100} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
