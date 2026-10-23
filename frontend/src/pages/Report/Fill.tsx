import { useEffect, useMemo, useState } from 'react'
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
  Modal,
  Radio,
  Result,
  Select,
  Space,
  Switch,
  Tag,
  Tooltip,
} from 'antd'
import { ThunderboltOutlined } from '@ant-design/icons'

import DataTable from '@/components/DataTable'
import PageHeader from '@/components/PageHeader'
import { reportApi, subApi } from '@/api'
import { useCan } from '@/stores/auth'
import { fmtDateTime } from '@/lib/constants'
import type { PendingReport } from '@/types'
import { PAGE_SIZE, tablePagination } from '@/lib/pagination'

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
  // 开启逾期补填需要 report:grant（学院负责人及以上）
  const canGrant = useCan()('report:grant')

  const [type, setType] = useState<ReportType>('WEEKLY')
  const [subId, setSubId] = useState<number | null>(null)
  const [syncProgress, setSyncProgress] = useState(false)
  const [picked, setPicked] = useState<PickedPeriod | null>(null)
  // 填报弹窗：逾期与正常填报共用，靠 picked 区分模式
  const [fillOpen, setFillOpen] = useState(false)

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

  // 待填清单：含已逾期与临近截止两类，回溯到子项目计划开始
  const [onlyOverdue, setOnlyOverdue] = useState(false)
  // 历史逾期可能很多（回溯到计划开始），默认只看最近 90 天，可切换全部
  const [showAllHistory, setShowAllHistory] = useState(false)
  const [pendingPage, setPendingPage] = useState(1)
  const [pendingPageSize, setPendingPageSize] = useState(PAGE_SIZE)
  const { data: pending = [], isLoading: pendingLoading } = useQuery({
    queryKey: ['report-pending'],
    queryFn: reportApi.pending,
  })

  const pendingShown = useMemo(() => {
    let list = pending
    if (!showAllHistory) {
      const cutoff = new Date()
      cutoff.setDate(cutoff.getDate() - 90)
      const cut = cutoff.toISOString().slice(0, 10)
      list = list.filter((p) => p.period_start >= cut || p.status === 'UPCOMING')
    }
    if (onlyOverdue) list = list.filter((p) => p.status === 'OVERDUE')
    return list
  }, [pending, onlyOverdue, showAllHistory])

  const overdueCount = useMemo(
    () => pending.filter((p) => p.status === 'OVERDUE').length,
    [pending],
  )
  const hiddenHistory = pending.length - pendingShown.length

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
      message.success(picked ? '补填已提交' : '报表已提交')
      form.resetFields()
      setType('WEEKLY')
      setSubId(null)
      setSyncProgress(false)
      setPicked(null)
      setFillOpen(false)
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
    setFillOpen(true)
  }

  /** 打开「正常填报」（当前周期）弹窗 */
  const openNormalFill = () => {
    setPicked(null)
    form.resetFields()
    setSyncProgress(false)
    setSubId(null)
    setType(enabledTypes[0] ?? 'WEEKLY')
    form.setFieldsValue({ type: enabledTypes[0] ?? 'WEEKLY' })
    setFillOpen(true)
  }

  // 开启补填放行（文档 4.3：超 3 天需学院负责人开）
  const [graceOpen, setGraceOpen] = useState(false)
  const [graceForm] = Form.useForm()
  const openGrace = (row?: PendingReport) => {
    graceForm.setFieldsValue({
      sub_id: row?.sub_id,
      type: row?.type ?? 'WEEKLY',
      period_start: row?.period_start,
      reason: '',
      days: 7,
    })
    setGraceOpen(true)
  }
  const grantMut = useMutation({
    mutationFn: (v: {
      sub_id: number
      type: string
      period_start?: string | null
      reason: string
      days: number
    }) => reportApi.grantGrace(v),
    onSuccess: (r) => {
      message.success(`已开启补填，有效期至 ${r.expires_on}`)
      setGraceOpen(false)
      graceForm.resetFields()
      void queryClient.invalidateQueries({ queryKey: ['report-graces'] })
    },
    onError: (e: unknown) => {
      const d = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      message.error(d || '开启失败')
    },
  })

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
      width: 200,
      render: (_: unknown, r: PendingReport) => `${r.period_start} ~ ${r.period_end}`,
    },
    {
      title: '状态',
      key: 'status',
      width: 110,
      render: (_: unknown, r: PendingReport) =>
        r.status === 'OVERDUE' ? (
          <Tag color="red">逾期 {r.overdue_days} 天</Tag>
        ) : (
          <Tag color="blue">
            {r.days_left <= 0 ? '今日截止' : `还剩 ${r.days_left} 天`}
          </Tag>
        ),
    },
    {
      title: '截止时间',
      dataIndex: 'deadline',
      key: 'deadline',
      width: 150,
      render: (v: string) => <span className="cell-datetime">{fmtDateTime(v)}</span>,
    },
    {
      title: '操作',
      key: 'act',
      width: 100,
      render: (_: unknown, r: PendingReport) => (
        <Button
          type="link"
          size="small"
          onClick={() => pickPending(r)}
        >
          {r.status === 'OVERDUE' ? '补填' : '填报'}
        </Button>
      ),
    },
  ]

  return (
    <div className="page-container">
      <PageHeader
        title="报表填报"
        subtitle="按周期提交日报 / 周报 / 月报；周报可先自动生成初稿再修改"
        extra={
          <Button type="primary" onClick={openNormalFill}>
            填报本期报表
          </Button>
        }
      />

      {/* 待填清单：逾期与临近截止分开显示，点行内按钮直接弹窗填报 */}
      <Card
            title={
              <Space size={8}>
                <span>待填报表（{pendingShown.length}）</span>
                {overdueCount > 0 && <Tag color="red">逾期 {overdueCount}</Tag>}
                {hiddenHistory > 0 && (
                  <Tag color="default">已折叠 {hiddenHistory} 条</Tag>
                )}
              </Space>
            }
            size="small"
            extra={
              <Space size={8}>
                <Tooltip title="默认只看最近 90 天">
                  <Space size={4}>
                    <span style={{ color: 'var(--text-secondary)', fontSize: 12 }}>全部</span>
                    <Switch
                      size="small"
                      checked={showAllHistory}
                      onChange={setShowAllHistory}
                    />
                  </Space>
                </Tooltip>
                <Space size={4}>
                  <span style={{ color: 'var(--text-secondary)', fontSize: 12 }}>只看逾期</span>
                  <Switch size="small" checked={onlyOverdue} onChange={setOnlyOverdue} />
                </Space>
                {canGrant && (
                  <Button size="small" onClick={() => openGrace()}>
                    开启补填
                  </Button>
                )}
              </Space>
            }
          >
            <DataTable<PendingReport>
              rowKey={(r) => `${r.sub_id}-${r.type}-${r.period_start}`}
              density="compact"
              loading={pendingLoading}
              columns={pendingColumns}
              dataSource={pendingShown}
              minTableWidth={720}
              pagination={tablePagination({
                current: pendingPage,
                pageSize: pendingPageSize,
                total: pendingShown.length,
                onChange: (p, ps) => {
                  setPendingPage(p)
                  setPendingPageSize(ps)
                },
              })}
              locale={{ emptyText: '暂无待填报表' }}
            />
          </Card>

      {/* 填报弹窗：正常填报与补填共用，靠 picked 区分模式 */}
      <Modal
        open={fillOpen}
        width={720}
        title={
          <Space size={8}>
            <span>{picked ? '补填报表' : '填报报表'}</span>
            {picked ? <Tag color="orange">补填</Tag> : <Tag color="green">本期</Tag>}
          </Space>
        }        okText={picked ? '提交补填' : '提交报表'}
        cancelText="取消"
        confirmLoading={submitMut.isPending}
        onOk={handleSubmit}
        onCancel={() => {
          setFillOpen(false)
          form.resetFields()
          setPicked(null)
          setSyncProgress(false)
        }}
        destroyOnClose
      >
        <Form form={form} layout="vertical" preserve={false}>
          <Space size={16} wrap align="start">
            <Form.Item
              name="sub_id"
              label="子项目"
              rules={[{ required: true, message: '请选择子项目' }]}
              style={{ minWidth: 320 }}
            >
              <Select
                showSearch
                optionFilterProp="label"
                placeholder="选择进行中的子项目"
                options={subOptions}
                disabled={!!picked}
                onChange={(v: number) => setSubId(v)}
              />
            </Form.Item>
            <Form.Item name="type" label="报表类型" initialValue="WEEKLY">
              <Radio.Group
                optionType="button"
                options={typeOptions}
                disabled={!!picked}
                onChange={(e) => setType(e.target.value as ReportType)}
              />
            </Form.Item>
          </Space>

          {picked ? (
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 16 }}
              message={`补填 ${picked.period_start} ~ ${picked.period_end}（截止 ${fmtDateTime(picked.deadline)}）`}
              description="补填按该周期记录，并同步更新子项目进度"
            />
          ) : period ? (
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 16 }}
              message={`本期 ${period.period_start} ~ ${period.period_end}（截止 ${fmtDateTime(period.deadline)}）`}
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
        </Form>
      </Modal>

      {/* 开启补填放行 */}
      <Modal
        open={graceOpen}
        title="开启逾期补填"
        okText="确认开启"
        cancelText="取消"
        confirmLoading={grantMut.isPending}
        onOk={async () => {
          const v = await graceForm.validateFields()
          grantMut.mutate(v)
        }}
        onCancel={() => setGraceOpen(false)}
        destroyOnClose
      >
        <p style={{ color: 'var(--text-secondary)', marginBottom: 16 }}>
          超过补填期的报表，开启后填报人可在有效期内补交。留空周期表示放行该类型的所有历史周期。
        </p>
        <Form form={graceForm} layout="vertical" preserve={false}>
          <Form.Item
            label="子项目"
            name="sub_id"
            rules={[{ required: true, message: '请选择子项目' }]}
          >
            <Select
              showSearch
              optionFilterProp="label"
              placeholder="选择子项目"
              options={subOptions}
            />
          </Form.Item>
          <Form.Item
            label="报表类型"
            name="type"
            rules={[{ required: true, message: '请选择类型' }]}
          >
            <Select
              options={enabledTypes.map((t) => ({
                value: t,
                label: TYPE_LABEL[t] ?? t,
              }))}
            />
          </Form.Item>
          <Form.Item
            label="周期起始日"
            name="period_start"
            extra="留空表示该类型的所有历史周期都可补填"
          >
            <Input placeholder="如 2026-09-21，留空为全部" />
          </Form.Item>
          <Form.Item
            label="开启理由"
            name="reason"
            rules={[{ required: true, message: '必须填写理由' }]}
          >
            <TextArea rows={2} placeholder="如：教师出差未及时填报，补录" maxLength={500} />
          </Form.Item>
          <Form.Item
            label="有效期（天）"
            name="days"
            rules={[{ required: true, message: '请填写有效期' }]}
          >
            <InputNumber min={1} max={90} style={{ width: 140 }} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
