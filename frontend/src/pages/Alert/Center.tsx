import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Badge,
  Button,
  Card,
  Checkbox,
  Descriptions,
  Drawer,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Spin,
  Tag,
  Timeline,
  Tooltip,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { ScanOutlined } from '@ant-design/icons'

import DataTable from '@/components/DataTable'
import Table from '@/components/Table'
import PageHeader from '@/components/PageHeader'
import { AlertLevelTag, AlertStatusTag, AlertTypeTag } from '@/components/Tags'
import { alertApi, systemApi } from '@/api'
import { ALERT_LEVEL_LABEL, ALERT_STATUS_LABEL, ALERT_TYPE_LABEL, daysLeft, fmtDateTime } from '@/lib/constants'
import { useDictStore } from '@/stores/dict'
import { useCan } from '@/stores/auth'
import type { Alert, AlertGroup } from '@/types'
import { tablePagination } from '@/lib/pagination'

/** 处置动作的中文名（时间线展示用） */
const ACTION_LABEL: Record<string, string> = {
  CLAIM: '认领',
  TRANSFER: '转交',
  CLOSE: '关闭',
  IGNORE: '忽略',
  ESCALATE: '超时升级',
  MEASURE: '填写处置措施',
}

const ACTION_COLOR: Record<string, string> = {
  CLAIM: 'blue',
  TRANSFER: 'purple',
  CLOSE: 'green',
  IGNORE: 'gray',
  ESCALATE: 'red',
  MEASURE: 'orange',
}

/** 不可关闭/忽略的预警类型（只能补齐数据自动关闭） */
const IMMUTABLE_TYPES = new Set(['AL-09', 'AL-10'])

/** 转交选人接口返回项 */
interface PickUser {
  id: number
  name: string
  staff_no: string
  college_id: number | null
  college_name: string | null
}

interface Filters {
  level?: string
  status?: string
  alert_type?: string
  major_id?: number
  college_id?: number
  only_mine: boolean
}

export default function AlertCenterPage() {
  const queryClient = useQueryClient()
  const majors = useDictStore((s) => s.majors)
  const colleges = useDictStore((s) => s.colleges)
  const can = useCan()

  // 处置按钮按权限点显示：认领 alert:claim / 关闭与忽略 alert:close / 转交 alert:transfer
  // 原来三个按钮无权限判断，只读用户也能看到并点击（后端会 403，但界面上不该出现）
  const canClaim = can('alert:claim')
  const canClose = can('alert:close')
  const canTransfer = can('alert:transfer')

  const [filters, setFilters] = useState<Filters>({ only_mine: false })
  const [page, setPage] = useState(1)
  const PAGE_SIZE = 20

  // 处置弹层
  const [closeAlert, setCloseAlert] = useState<Alert | null>(null)
  const [ignoreAlert, setIgnoreAlert] = useState<Alert | null>(null)
  const [transferAlert, setTransferAlert] = useState<Alert | null>(null)
  // 详情抽屉：展示触发详情 + 处置时间线 + 填写处置措施
  const [detailId, setDetailId] = useState<number | null>(null)
  const [measureText, setMeasureText] = useState('')
  const [userOptions, setUserOptions] = useState<PickUser[]>([])
  const [closeForm] = Form.useForm()
  const [ignoreForm] = Form.useForm()
  const [transferForm] = Form.useForm()

  const params = useMemo(
    () => ({
      page,
      page_size: PAGE_SIZE,
      level: filters.level,
      status: filters.status,
      alert_type: filters.alert_type,
      major_id: filters.major_id,
      college_id: filters.college_id,
      only_mine: filters.only_mine || undefined,
    }),
    [filters, page],
  )

  const { data, isFetching } = useQuery({
    queryKey: ['alerts', params],
    queryFn: () => alertApi.list(params),
    placeholderData: (prev) => prev,
  })

  const { data: grouped } = useQuery({ queryKey: ['alerts-grouped'], queryFn: alertApi.grouped })

  // 详情：预警信息 + 触发详情 + 处置时间线
  const { data: detail, isFetching: detailLoading } = useQuery({
    queryKey: ['alert-detail', detailId],
    queryFn: () => alertApi.detail(detailId as number),
    enabled: detailId !== null,
  })

  const measureMutation = useMutation({
    mutationFn: ({ id, text }: { id: number; text: string }) => alertApi.measure(id, text),
    onSuccess: () => {
      message.success('处置措施已记录')
      setMeasureText('')
      queryClient.invalidateQueries({ queryKey: ['alert-detail', detailId] })
      invalidate()
    },
  })

  const patchFilters = (patch: Partial<Filters>) => {
    setFilters((f) => ({ ...f, ...patch }))
    setPage(1)
  }

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['alerts'] })
    queryClient.invalidateQueries({ queryKey: ['alerts-grouped'] })
    queryClient.invalidateQueries({ queryKey: ['alerts-stats'] })
    // 待办角标与「我的督办」卡片用的是两个不同的 key（历史上不统一），
    // 这里两个都失效，避免处理完预警后看板数字不刷新。
    queryClient.invalidateQueries({ queryKey: ['todo'] })
    queryClient.invalidateQueries({ queryKey: ['dashboard-todo'] })
    queryClient.invalidateQueries({ queryKey: ['messages-unread'] })
  }

  const scanMutation = useMutation({
    mutationFn: alertApi.scan,
    onSuccess: (r) => {
      message.success(`新建 ${r.created} 条，合并 ${r.merged} 条`)
      invalidate()
    },
  })

  const claimMutation = useMutation({
    mutationFn: (id: number) => alertApi.claim(id),
    onSuccess: () => {
      message.success('已认领')
      invalidate()
    },
  })

  const closeMutation = useMutation({
    mutationFn: ({ id, reason }: { id: number; reason?: string }) => alertApi.close(id, reason),
    onSuccess: () => {
      message.success('已关闭')
      setCloseAlert(null)
      closeForm.resetFields()
      invalidate()
    },
  })

  const ignoreMutation = useMutation({
    mutationFn: ({ id, reason }: { id: number; reason: string }) => alertApi.ignore(id, reason),
    onSuccess: () => {
      message.success('已忽略')
      setIgnoreAlert(null)
      ignoreForm.resetFields()
      invalidate()
    },
  })

  const transferMutation = useMutation({
    mutationFn: ({ id, to }: { id: number; to: number }) => alertApi.transfer(id, to),
    onSuccess: () => {
      message.success('已转交')
      setTransferAlert(null)
      transferForm.resetFields()
      invalidate()
    },
  })

  /** 剩余天数 Tag */
  const deadlineCell = (a: Alert) => {
    if (!a.deadline_at) return '—'
    const left = daysLeft(a.deadline_at)
    const overdue = left !== null && left < 0
    return (
      <Space size={6} wrap={false}>
        <span className="cell-datetime">{fmtDateTime(a.deadline_at)}</span>
        {left !== null && (
          <Tag color={overdue ? 'red' : left <= 1 ? 'orange' : 'default'} style={{ marginRight: 0 }}>
            {overdue ? `超期 ${Math.abs(left)} 天` : `剩 ${left} 天`}
          </Tag>
        )}
      </Space>
    )
  }

  const columns: ColumnsType<Alert> = [
    { title: '类型', dataIndex: 'alert_type', width: 110, render: (_, r) => <AlertTypeTag type={r.alert_type} /> },
    { title: '等级', dataIndex: 'level', width: 80, render: (_, r) => <AlertLevelTag level={r.level} /> },
    { title: '状态', dataIndex: 'status', width: 90, render: (_, r) => <AlertStatusTag status={r.status} /> },
    {
      title: '子项目',
      dataIndex: 'sub_name',
      ellipsis: true,
      render: (v: string | null) => v ?? '—',
    },
    {
      title: '触发时间',
      dataIndex: 'trigger_at',
      width: 150,
      render: (v: string) => <span className="cell-datetime">{fmtDateTime(v)}</span>,
    },
    { title: '处置截止', dataIndex: 'deadline_at', width: 230, render: (_, r) => deadlineCell(r) },
    {
      title: '合并',
      dataIndex: 'merged_count',
      width: 70,
      render: (v: number) =>
        v > 1 ? (
          <Tooltip title={`同一问题 ${v} 次触发，已合并展示`}>
            <Badge count={v} size="small" color="orange" />
          </Tooltip>
        ) : (
          <span style={{ color: '#e5e7eb' }}>—</span>
        ),
    },
    {
      title: '升级',
      dataIndex: 'escalated_at',
      width: 80,
      render: (v: string | null) => (v ? <Tag color="purple">已升级</Tag> : '—'),
    },
    {
      title: '操作',
      width: 210,
      render: (_, r) => {
        const immutable = IMMUTABLE_TYPES.has(r.alert_type)
        return (
          <Space size={0} wrap>
            {r.status === 'PENDING' && canClaim && (
              <Button
                type="link"
                size="small"
                loading={claimMutation.isPending && claimMutation.variables === r.id}
                onClick={() => claimMutation.mutate(r.id)}
              >
                认领
              </Button>
            )}
            {r.status === 'CLAIMED' && (
              <>
                {canClose && (
                  <Tooltip title={immutable ? '补齐数据后自动关闭' : ''}>
                    <Button
                      type="link"
                      size="small"
                      disabled={immutable}
                      onClick={() => {
                        setCloseAlert(r)
                        closeForm.resetFields()
                      }}
                    >
                      关闭
                    </Button>
                  </Tooltip>
                )}
                {canClose && (
                  <Tooltip title={immutable ? '不能忽略' : ''}>
                    <Button
                      type="link"
                      size="small"
                      disabled={immutable}
                      onClick={() => {
                        setIgnoreAlert(r)
                        ignoreForm.resetFields()
                      }}
                    >
                      忽略
                    </Button>
                  </Tooltip>
                )}
                {canTransfer && (
                  <Button
                    type="link"
                    size="small"
                    onClick={() => {
                      setTransferAlert(r)
                      transferForm.resetFields()
                    }}
                  >
                    转交
                  </Button>
                )}
              </>
            )}
            <Button type="link" size="small" onClick={() => setDetailId(r.id)}>
              详情
            </Button>
            {(r.status === 'CLOSED' || r.status === 'IGNORED') && (
              <span style={{ color: '#9ca3af' }}>{r.close_reason ?? '—'}</span>
            )}
          </Space>
        )
      },
    },
  ]

  const groupColumns: ColumnsType<AlertGroup> = [
    { title: '大项目', dataIndex: 'major_name', ellipsis: true },
    { title: '学院', dataIndex: 'college_name', width: 150 },
    {
      title: '红色',
      dataIndex: 'red',
      width: 70,
      render: (v: number) => (v ? <b style={{ color: '#ef4444' }}>{v}</b> : '—'),
    },
    {
      title: '橙色',
      dataIndex: 'orange',
      width: 70,
      render: (v: number) => (v ? <b style={{ color: '#f59e0b' }}>{v}</b> : '—'),
    },
    { title: '黄色', dataIndex: 'yellow', width: 70, render: (v: number) => v || '—' },
    { title: '合计', dataIndex: 'total', width: 70, render: (v: number) => <b>{v}</b> },
  ]

  return (
    <div className="page-container">
      <PageHeader
        title="预警中心"
        subtitle="红色邮件+站内实时提醒；橙色进每日摘要；黄色仅提示不进待办"
        extra={
          // 扫描接口要求 system:config，普通用户不该看到这个按钮
          can('system:config') && (
            <Button
              type="primary"
              icon={<ScanOutlined />}
              loading={scanMutation.isPending}
              onClick={() => scanMutation.mutate()}
            >
              执行扫描
            </Button>
          )
        }
      />

      {/* 折叠聚合：按大项目→学院，避免预警泛滥（文档 3.3） */}
      {grouped && (
        <Card size="small" style={{ marginBottom: 16 }}>
          <Space size={24} wrap style={{ marginBottom: 12 }}>
            <span>
              总计 <b style={{ fontSize: 18 }}>{grouped.total}</b>
            </span>
            <span>
              <Tag color="red">红 {grouped.red}</Tag>
              <Tag color="orange">橙 {grouped.orange}</Tag>
              <Tag color="gold">黄 {grouped.yellow}</Tag>
            </span>
          </Space>
          <Table<AlertGroup>
            rowKey={(r) => `${r.major_id ?? 0}-${r.college_id ?? 0}`}
            density="compact"
            columns={groupColumns}
            dataSource={grouped.groups}
            pagination={false}
            rowClassName={() => 'clickable-row'}
            onRow={(r) => ({
              // 聚合行是「大项目 → 学院」两级，点击要同时带上学院，
              // 否则点「物流管理学院 8 条」会把该项目下所有学院的预警都列出来。
              onClick: () =>
                patchFilters({
                  major_id: r.major_id ?? undefined,
                  college_id: r.college_id ?? undefined,
                }),
              style: { cursor: 'pointer' },
            })}
          />
        </Card>
      )}

      <Space wrap style={{ marginBottom: 16 }}>
        <Select
          allowClear
          placeholder="等级"
          style={{ width: 110 }}
          value={filters.level}
          options={Object.entries(ALERT_LEVEL_LABEL).map(([value, label]) => ({ value, label }))}
          onChange={(v) => patchFilters({ level: v })}
        />
        <Select
          allowClear
          placeholder="状态"
          style={{ width: 120 }}
          value={filters.status}
          options={Object.entries(ALERT_STATUS_LABEL).map(([value, label]) => ({ value, label }))}
          onChange={(v) => patchFilters({ status: v })}
        />
        <Select
          allowClear
          showSearch
          optionFilterProp="label"
          placeholder="类型"
          style={{ width: 140 }}
          value={filters.alert_type}
          options={Object.entries(ALERT_TYPE_LABEL).map(([value, label]) => ({ value, label }))}
          onChange={(v) => patchFilters({ alert_type: v })}
        />
        <Select
          allowClear
          showSearch
          optionFilterProp="label"
          placeholder="大项目"
          style={{ width: 220 }}
          value={filters.major_id}
          options={majors.map((m) => ({ value: m.id, label: m.name }))}
          onChange={(v) => patchFilters({ major_id: v })}
        />
        <Select
          allowClear
          showSearch
          optionFilterProp="label"
          placeholder="学院"
          style={{ width: 180 }}
          value={filters.college_id}
          options={colleges.map((c) => ({ value: c.id, label: c.name }))}
          onChange={(v) => patchFilters({ college_id: v })}
        />
        <Checkbox
          checked={filters.only_mine}
          onChange={(e) => patchFilters({ only_mine: e.target.checked })}
        >
          只看我相关的
        </Checkbox>
      </Space>

      <DataTable<Alert>
        rowKey="id"
        loading={isFetching}
        columns={columns}
        dataSource={data?.items ?? []}
        minTableWidth={1320}
        pagination={tablePagination({
          current: page,
          pageSize: PAGE_SIZE,
          total: data?.total ?? 0,
          onChange: (p) => setPage(p),
        })}
      />

      {/* 关闭：理由可选 */}
      <Modal
        title={`关闭预警 — ${closeAlert ? ALERT_TYPE_LABEL[closeAlert.alert_type] ?? closeAlert.alert_type : ''}`}
        open={!!closeAlert}
        onCancel={() => setCloseAlert(null)}
        onOk={() => closeForm.submit()}
        confirmLoading={closeMutation.isPending}
        okText="确认关闭"
        cancelText="取消"
      >
        <Form form={closeForm} layout="vertical" onFinish={(v) => closeMutation.mutate({ id: closeAlert!.id, reason: v.reason })}>
          <Form.Item name="reason" label="关闭理由（选填）">
            <Input.TextArea rows={2} maxLength={500} placeholder="例如：进度已追上" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 忽略：理由必填 */}
      <Modal
        title={`忽略预警 — ${ignoreAlert ? ALERT_TYPE_LABEL[ignoreAlert.alert_type] ?? ignoreAlert.alert_type : ''}`}
        open={!!ignoreAlert}
        onCancel={() => setIgnoreAlert(null)}
        onOk={() => ignoreForm.submit()}
        confirmLoading={ignoreMutation.isPending}
        okText="确认忽略"
        cancelText="取消"
      >
        <Form form={ignoreForm} layout="vertical" onFinish={(v) => ignoreMutation.mutate({ id: ignoreAlert!.id, reason: v.reason })}>
          <Form.Item
            name="reason"
            label="忽略理由（必填）"
            rules={[{ required: true, message: '忽略预警必须填写理由' }]}
          >
            <Input.TextArea rows={2} maxLength={500} placeholder="例如：误报、寒暑假暂停" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 转交：选接收人（pickUsers 无需 system:user 权限） */}
      <Modal
        title={`转交预警 — ${transferAlert ? ALERT_TYPE_LABEL[transferAlert.alert_type] ?? transferAlert.alert_type : ''}`}
        open={!!transferAlert}
        onCancel={() => setTransferAlert(null)}
        onOk={() => transferForm.submit()}
        confirmLoading={transferMutation.isPending}
        okText="确认转交"
        cancelText="取消"
      >
        <Form form={transferForm} layout="vertical" onFinish={(v) => transferMutation.mutate({ id: transferAlert!.id, to: v.to_user_id })}>
          <Form.Item
            name="to_user_id"
            label="接收人"
            rules={[{ required: true, message: '请选择接收人' }]}
          >
            <Select
              showSearch
              optionFilterProp="label"
              placeholder="输入姓名或工号搜索"
              options={userOptions.map((u) => ({
                value: u.id,
                label: `${u.name}（${u.staff_no}${u.college_name ? ' · ' + u.college_name : ''}）`,
              }))}
              onSearch={(kw) => {
                systemApi
                  .pickUsers(kw ? { keyword: kw, limit: 20 } : { limit: 20 })
                  .then((list) => setUserOptions(list))
                  .catch(() => setUserOptions([]))
              }}
              onFocus={() => {
                systemApi
                  .pickUsers({ limit: 20 })
                  .then((list) => setUserOptions(list))
                  .catch(() => setUserOptions([]))
              }}
              notFoundContent="无匹配用户"
            />
          </Form.Item>
        </Form>
      </Modal>

      {/* 详情抽屉：触发详情 + 处置时间线 + 填写处置措施
          早期 candidate.detail 从不落库、alert_action 前端也从不读取，
          处置人只看到「有预警」看不到「为什么」，也查不到谁处理过。 */}
      <Drawer
        title={
          detail
            ? `预警详情 — ${ALERT_TYPE_LABEL[detail.alert_type] ?? detail.alert_type}`
            : '预警详情'
        }
        width={560}
        open={detailId !== null}
        onClose={() => {
          setDetailId(null)
          setMeasureText('')
        }}
      >
        {detailLoading && !detail ? (
          <Spin />
        ) : detail ? (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label="等级">
                <AlertLevelTag level={detail.level} />
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                <AlertStatusTag status={detail.status} />
              </Descriptions.Item>
              <Descriptions.Item label="子项目">{detail.sub_name ?? '—'}</Descriptions.Item>
              <Descriptions.Item label="触发时间">{fmtDateTime(detail.trigger_at)}</Descriptions.Item>
              <Descriptions.Item label="处置截止">
                {detail.deadline_at ? fmtDateTime(detail.deadline_at) : '—'}
              </Descriptions.Item>
              <Descriptions.Item label="合并次数">{detail.merged_count}</Descriptions.Item>
              {detail.close_reason && (
                <Descriptions.Item label="关闭/忽略理由">{detail.close_reason}</Descriptions.Item>
              )}
            </Descriptions>

            {/* 触发详情：规则算出来的连续天数/阈值/风险描述等 */}
            {detail.detail && Object.keys(detail.detail).length > 0 && (
              <Card size="small" title="触发详情">
                <Descriptions column={1} size="small">
                  {Object.entries(detail.detail).map(([k, v]) => (
                    <Descriptions.Item key={k} label={k}>
                      {typeof v === 'object' ? JSON.stringify(v) : String(v)}
                    </Descriptions.Item>
                  ))}
                </Descriptions>
              </Card>
            )}

            {/* 填写处置措施（需求 3.3(4)：已认领 → 填写处置措施、关闭、转交） */}
            {detail.status === 'CLAIMED' && canClaim && (
              <Card size="small" title="填写处置措施">
                <Space direction="vertical" size={8} style={{ width: '100%' }}>
                  <Input.TextArea
                    rows={3}
                    maxLength={1000}
                    value={measureText}
                    onChange={(e) => setMeasureText(e.target.value)}
                    placeholder="例如：已联系企业对接人，设备 11 月 5 日到校"
                  />
                  <Button
                    type="primary"
                    disabled={!measureText.trim()}
                    loading={measureMutation.isPending}
                    onClick={() =>
                      measureMutation.mutate({ id: detail.id, text: measureText.trim() })
                    }
                  >
                    记录处置措施
                  </Button>
                </Space>
              </Card>
            )}

            <Card size="small" title="处置时间线">
              {detail.actions.length === 0 ? (
                <span style={{ color: '#9ca3af' }}>暂无处置记录</span>
              ) : (
                <Timeline
                  items={detail.actions.map((a) => ({
                    color: ACTION_COLOR[a.action] ?? 'blue',
                    children: (
                      <Space direction="vertical" size={2}>
                        <Space size={8}>
                          <Tag color={ACTION_COLOR[a.action] ?? 'blue'} style={{ margin: 0 }}>
                            {ACTION_LABEL[a.action] ?? a.action}
                          </Tag>
                          <span>{a.operator_name ?? '系统'}</span>
                          <span style={{ color: '#9ca3af', fontSize: 12 }}>
                            {fmtDateTime(a.created_at)}
                          </span>
                        </Space>
                        {a.reason && (
                          <span style={{ color: '#4b5563', fontSize: 13 }}>{a.reason}</span>
                        )}
                      </Space>
                    ),
                  }))}
                />
              )}
            </Card>
          </Space>
        ) : (
          <span style={{ color: '#9ca3af' }}>加载失败</span>
        )}
      </Drawer>
    </div>
  )
}
