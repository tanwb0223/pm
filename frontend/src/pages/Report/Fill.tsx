import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  App,
  Button,
  Card,
  Collapse,
  Form,
  Input,
  InputNumber,
  Radio,
  Result,
  Select,
  Space,
  Switch,
  Table,
  Tag,
} from 'antd'
import { ThunderboltOutlined } from '@ant-design/icons'

import PageHeader from '@/components/PageHeader'
import { reportApi, subApi } from '@/api'
import { useCan } from '@/stores/auth'
import { fmtDateTime } from '@/lib/constants'
import type { PendingReport } from '@/types'

const { TextArea } = Input

type ReportType = 'DAILY' | 'WEEKLY' | 'MONTHLY'

const TYPE_LABEL: Record<ReportType, string> = {
  DAILY: '日报',
  WEEKLY: '周报',
  MONTHLY: '月报',
}

/** 从待填清单带入的补填周期 */
interface PickedPeriod {
  period_start: string
  period_end: string
  deadline: string
}

interface FillFormValues {
  sub_id: number
  type: ReportType
  f_today?: string
  f_tomorrow?: string
  f_summary?: string
  f_completed?: string
  f_next?: string
  f_issues?: string
  f_suggestions?: string
  sync_progress?: boolean
  progress?: number
}

/**
 * 报表填报（守卫在外层）
 *
 * 填报页原本没有任何权限守卫：VIEWER 这类只读用户虽然侧边栏看不到
 * 「报表填报」，但手输 /report/fill 就能进入并看到「提交报表」按钮，
 * 点了才被后端 403 拒绝。这里按 report:fill 前置拦截。
 */
export default function ReportFillPage() {
  const can = useCan()
  if (!can('report:fill')) {
    return (
      <div className="page-container">
        <Result status="403" title="无权限" subTitle="需要 report:fill 权限才能填报报表" />
      </div>
    )
  }
  return <ReportFillPageInner />
}

function ReportFillPageInner() {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const [form] = Form.useForm<FillFormValues>()
  const formCardRef = useRef<HTMLDivElement>(null)

  const [type, setType] = useState<ReportType>('WEEKLY')
  const [subId, setSubId] = useState<number | null>(null)
  const [syncProgress, setSyncProgress] = useState(false)
  const [picked, setPicked] = useState<PickedPeriod | null>(null)

  // 填报选项：日报受 report.daily.enabled 控制，关闭时不出现在类型选择里
  const { data: options } = useQuery({
    queryKey: ['report-options'],
    queryFn: reportApi.options,
    staleTime: 60_000,
  })
  const enabledTypes = useMemo(
    () => (options?.types ?? ['WEEKLY', 'MONTHLY']) as ReportType[],
    [options],
  )
  const typeOptions = enabledTypes.map((t) => ({ value: t, label: TYPE_LABEL[t] }))

  // 兜底：若当前选中的类型已被管理员关闭（如刚关掉日报），自动回落到第一个可用类型
  useEffect(() => {
    if (options && !enabledTypes.includes(type)) {
      const fallback = enabledTypes[0] ?? 'WEEKLY'
      setType(fallback)
      form.setFieldsValue({ type: fallback })
    }
  }, [options, enabledTypes, type, form])

  // 待填清单
  const { data: pending = [], isLoading: pendingLoading } = useQuery({
    queryKey: ['report-pending'],
    queryFn: reportApi.pending,
  })

  // 进行中的子项目
  const { data: subPage } = useQuery({
    queryKey: ['subs-active-fill'],
    queryFn: () => subApi.list({ page: 1, page_size: 200, status: 'IN_PROGRESS' }),
  })

  // 本期周期与截止时间
  const { data: period } = useQuery({
    queryKey: ['report-period', type],
    queryFn: () => reportApi.period(type),
    enabled: !!type,
  })

  // 周报初稿
  const draftMut = useMutation({
    mutationFn: (sid: number) => reportApi.weeklyDraft(sid),
    onSuccess: (d) => {
      form.setFieldsValue({
        f_summary: d.summary,
        f_next: d.next_week || form.getFieldValue('f_next'),
      })
      const delta = d.progress_delta >= 0 ? `+${d.progress_delta}` : String(d.progress_delta)
      message.success(`初稿已生成（当前进度 ${d.progress}%，本期变化 ${delta}），请核对后提交`)
    },
  })

  const submitMut = useMutation({
    mutationFn: (payload: Parameters<typeof reportApi.submit>[0]) => reportApi.submit(payload),
    onSuccess: () => {
      message.success('报表已提交')
      form.resetFields()
      setType('WEEKLY')
      setSubId(null)
      setSyncProgress(false)
      setPicked(null)
      void queryClient.invalidateQueries({ queryKey: ['report-pending'] })
      void queryClient.invalidateQueries({ queryKey: ['report-list'] })
    },
  })

  const subOptions = (subPage?.items ?? []).map((s) => ({
    value: s.id,
    label: `${s.name}（${s.major_name ?? '—'} / ${s.college_name ?? '—'}）`,
  }))

  const pickPending = (row: PendingReport) => {
    setType(row.type)
    setSubId(row.sub_id)
    setPicked({
      period_start: row.period_start,
      period_end: row.period_end,
      deadline: row.deadline,
    })
    form.setFieldsValue({ sub_id: row.sub_id, type: row.type })
    formCardRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  const handleSubmit = async () => {
    const values = await form.validateFields()
    const rtype = values.type
    let content: Record<string, unknown>
    if (rtype === 'DAILY') {
      content = { today: values.f_today, tomorrow: values.f_tomorrow, issues: values.f_issues }
    } else if (rtype === 'WEEKLY') {
      content = { summary: values.f_summary, next_week: values.f_next, issues: values.f_issues }
    } else {
      content = {
        completed: values.f_completed,
        next_month: values.f_next,
        issues: values.f_issues,
        suggestions: values.f_suggestions,
      }
    }
    submitMut.mutate({
      sub_id: values.sub_id,
      type: rtype,
      period_start: picked?.period_start,
      progress: values.sync_progress ? values.progress : undefined,
      content,
    })
  }

  const pendingColumns = [
    {
      title: '子项目',
      dataIndex: 'sub_name',
      key: 'sub_name',
      ellipsis: true,
    },
    {
      title: '类型',
      dataIndex: 'type_label',
      key: 'type_label',
      width: 90,
      render: (v: string) => <Tag>{v}</Tag>,
    },
    {
      title: '周期',
      key: 'period',
      width: 210,
      render: (_: unknown, r: PendingReport) => `${r.period_start} ~ ${r.period_end}`,
    },
    {
      title: '截止时间',
      dataIndex: 'deadline',
      key: 'deadline',
      width: 160,
      render: (v: string, r: PendingReport) => (
        <Space direction="vertical" size={0}>
          <span>{fmtDateTime(v)}</span>
          {r.overdue_days > 0 && <Tag color="red">逾期{r.overdue_days}天</Tag>}
        </Space>
      ),
    },
    {
      title: '操作',
      key: 'act',
      width: 100,
      render: (_: unknown, r: PendingReport) => (
        <Button type="link" size="small" onClick={() => pickPending(r)}>
          立即填报
        </Button>
      ),
    },
  ]

  return (
    <div className="page-container">
      <PageHeader title="报表填报" subtitle="按周期提交日报 / 周报 / 月报；周报可先自动生成初稿再修改" />

      <Card title={`待填报表（${pending.length}）`} size="small" style={{ marginBottom: 16 }}>
        <Table
          rowKey={(r) => `${r.sub_id}-${r.type}-${r.period_start}`}
          size="small"
          loading={pendingLoading}
          columns={pendingColumns}
          dataSource={pending}
          pagination={false}
          locale={{ emptyText: '暂无待填报表' }}
        />
      </Card>

      <div ref={formCardRef}>
        <Card title="填报表单" size="small">
          <Form
            form={form}
            layout="vertical"
            style={{ maxWidth: 720 }}
            onFinish={handleSubmit}
          >
            <Space size={16} wrap align="start">
              <Form.Item
                name="sub_id"
                label="子项目"
                rules={[{ required: true, message: '请选择子项目' }]}
                style={{ minWidth: 360 }}
              >
                <Select
                  showSearch
                  optionFilterProp="label"
                  placeholder="选择进行中的子项目"
                  options={subOptions}
                  onChange={(v: number) => setSubId(v)}
                />
              </Form.Item>
              <Form.Item name="type" label="报表类型" initialValue="WEEKLY">
                <Radio.Group
                  optionType="button"
                  options={typeOptions}
                  onChange={(e) => setType(e.target.value as ReportType)}
                />
              </Form.Item>
            </Space>

            {picked ? (
              <Alert
                type="warning"
                showIcon
                style={{ marginBottom: 16 }}
                message={`补填期：${picked.period_start} ~ ${picked.period_end}，截止 ${fmtDateTime(picked.deadline)}`}
              />
            ) : period ? (
              <Alert
                type="info"
                showIcon
                style={{ marginBottom: 16 }}
                message={`本期：${period.period_start} ~ ${period.period_end}，截止 ${fmtDateTime(period.deadline)}`}
              />
            ) : null}

            {type === 'WEEKLY' && subId != null && (
              <Button
                icon={<ThunderboltOutlined />}
                loading={draftMut.isPending}
                onClick={() => draftMut.mutate(subId)}
                style={{ marginBottom: 16 }}
              >
                生成周报初稿
              </Button>
            )}

            {type === 'DAILY' && (
              <>
                <Form.Item
                  name="f_today"
                  label="今天做了什么"
                  rules={[{ required: true, message: '请填写今天做了什么' }]}
                >
                  <TextArea rows={3} placeholder="简述今天完成的进展" />
                </Form.Item>
                <Form.Item name="f_tomorrow" label="明天计划">
                  <TextArea rows={2} />
                </Form.Item>
                <Form.Item name="f_issues" label="问题">
                  <TextArea rows={2} placeholder="有什么问题需要协调（可空）" />
                </Form.Item>
              </>
            )}

            {type === 'WEEKLY' && (
              <>
                <Form.Item
                  name="f_summary"
                  label="本周进展"
                  rules={[{ required: true, message: '请填写本周进展' }]}
                >
                  <TextArea rows={4} placeholder="本周完成情况，可点击「生成周报初稿」自动拼进度记录" />
                </Form.Item>
                <Form.Item name="f_next" label="下周计划">
                  <TextArea rows={2} placeholder="自动带出上期周报的下周计划" />
                </Form.Item>
                <Form.Item name="f_issues" label="问题与风险">
                  <TextArea rows={2} />
                </Form.Item>
              </>
            )}

            {type === 'MONTHLY' && (
              <>
                <Form.Item
                  name="f_completed"
                  label="本月完成"
                  rules={[{ required: true, message: '请填写本月完成情况' }]}
                >
                  <TextArea rows={4} />
                </Form.Item>
                <Form.Item name="f_next" label="下月计划">
                  <TextArea rows={2} />
                </Form.Item>
                <Form.Item name="f_issues" label="问题与风险">
                  <TextArea rows={2} />
                </Form.Item>
                <Form.Item name="f_suggestions" label="意见建议">
                  <TextArea rows={2} />
                </Form.Item>
              </>
            )}

            <Collapse
              ghost
              style={{ marginBottom: 8 }}
              items={[
                {
                  key: 'progress',
                  label: '同步更新进度（可选）',
                  children: (
                    <Space align="center" wrap>
                      <Form.Item name="sync_progress" valuePropName="checked" noStyle>
                        <Switch
                          checkedChildren="同步"
                          unCheckedChildren="不同步"
                          onChange={(v) => setSyncProgress(v as boolean)}
                        />
                      </Form.Item>
                      {syncProgress && (
                        <Form.Item
                          name="progress"
                          label="最新进度（%）"
                          rules={[{ required: true, message: '请填写进度' }]}
                          style={{ marginBottom: 0, marginTop: 8 }}
                        >
                          <InputNumber min={0} max={100} style={{ width: 140 }} />
                        </Form.Item>
                      )}
                    </Space>
                  ),
                },
              ]}
            />

            <Button type="primary" htmlType="submit" loading={submitMut.isPending}>
              提交报表
            </Button>
          </Form>
        </Card>
      </div>
    </div>
  )
}
