import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Button,
  Checkbox,
  Dropdown,
  Form,
  Input,
  InputNumber,
  Modal,
  Progress,
  Select,
  Slider,
  Space,
  Switch,
  Table,
  Tabs,
  Tag,
  Tooltip,
  message,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { DownloadOutlined } from '@ant-design/icons'

import PageHeader from '@/components/PageHeader'
import AttachmentPanel from '@/components/AttachmentPanel'
import SubTrendDrawer from '@/components/SubTrendDrawer'
import { DeviationText, SubStatusTag } from '@/components/Tags'
import { exportApi, progressApi, subApi } from '@/api'
import { SUB_STATUS_LABEL, fmtDate, fmtDateTime } from '@/lib/constants'
import { useDictStore } from '@/stores/dict'
import type { ProgressRow } from '@/types'

/** 留痕记录（subApi.records 返回项） */
interface ProgressRecord {
  id: number
  progress: number
  note: string | null
  is_no_change: boolean
  is_rollback: boolean
  rollback_reason: string | null
  operator_id: number
  on_behalf_of: number | null
  created_at: string
}

/** Slider + InputNumber 联动控件（antd Form 自定义受控组件，Form 自动注入 value/onChange） */
function ProgressPicker({
  value,
  onChange,
  disabled,
}: {
  value?: number
  onChange?: (v: number) => void
  disabled?: boolean
}) {
  return (
    <Space direction="vertical" style={{ width: '100%' }} size={4}>
      <Slider value={value ?? 0} onChange={(v) => onChange?.(v)} disabled={disabled} />
      <Space>
        <InputNumber
          min={0}
          max={100}
          value={value}
          onChange={(v) => onChange?.(v ?? 0)}
          disabled={disabled}
          style={{ width: 120 }}
        />
        <span style={{ color: '#6b7280', fontSize: 12 }}>单位：%</span>
      </Space>
    </Space>
  )
}

/** 行展开：该子项目的进度留痕 */
function RecordList({ subId }: { subId: number }) {
  const { data, isLoading } = useQuery({
    queryKey: ['progress-records', subId],
    queryFn: () => subApi.records(subId),
  })

  const cols: ColumnsType<ProgressRecord> = [
    { title: '时间', dataIndex: 'created_at', width: 140, render: (v: string) => fmtDateTime(v) },
    { title: '进度', dataIndex: 'progress', width: 70, render: (v: number) => `${v}%` },
    {
      title: '类型',
      width: 90,
      render: (_, r) =>
        r.is_no_change ? (
          <Tag>无变化</Tag>
        ) : r.is_rollback ? (
          <Tag color="orange">回调</Tag>
        ) : (
          <Tag color="green">正常</Tag>
        ),
    },
    { title: '说明', dataIndex: 'note', render: (v: string | null) => v ?? '—' },
    { title: '回调原因', dataIndex: 'rollback_reason', render: (v: string | null) => v ?? '—' },
  ]

  return (
    <Table<ProgressRecord>
      rowKey="id"
      size="small"
      loading={isLoading}
      columns={cols}
      dataSource={data ?? []}
      pagination={false}
    />
  )
}

type SortKey = 'deviation' | 'update' | 'progress'

interface Filters {
  major_id?: number
  college_id?: number
  status?: string
  keyword?: string
  sort: SortKey
  only_abnormal: boolean
}

const PAGE_SIZE = 50

export default function ProgressTablePage() {
  const queryClient = useQueryClient()
  const colleges = useDictStore((s) => s.colleges)
  const majors = useDictStore((s) => s.majors)

  const [filters, setFilters] = useState<Filters>({ sort: 'deviation', only_abnormal: false })
  const [page, setPage] = useState(1)

  // 填报 Modal
  const [fillSub, setFillSub] = useState<ProgressRow | null>(null)
  const [trendSub, setTrendSub] = useState<ProgressRow | null>(null)
  const [form] = Form.useForm()
  const noChange = !!Form.useWatch('noChange', form)

  const params = useMemo(
    () => ({
      page,
      page_size: PAGE_SIZE,
      sort: filters.sort,
      only_abnormal: filters.only_abnormal || undefined,
      major_id: filters.major_id,
      college_id: filters.college_id,
      status: filters.status,
      keyword: filters.keyword || undefined,
    }),
    [filters, page],
  )

  const { data, isFetching } = useQuery({
    queryKey: ['progress-table', params],
    queryFn: () => progressApi.table(params),
    placeholderData: (prev) => prev,
  })

  const patchFilters = (patch: Partial<Filters>) => {
    setFilters((f) => ({ ...f, ...patch }))
    setPage(1)
  }

  const openFill = (sub: ProgressRow) => {
    setFillSub(sub)
    form.setFieldsValue({
      progress: sub.current_progress,
      noChange: false,
      // 自动带出上次说明（文档 3.2(1)），改几个字即可提交
      note: sub.last_note ?? undefined,
      rollback_reason: undefined,
    })
  }

  const openTrend = (sub: ProgressRow) => {
    setTrendSub(sub)
  }

  const fillMutation = useMutation({
    mutationFn: (values: {
      progress: number
      noChange?: boolean
      note?: string
      rollback_reason?: string
    }) => {
      const sub = fillSub!
      return subApi.updateProgress(sub.id, {
        progress: values.noChange ? sub.current_progress : values.progress,
        note: values.note,
        no_change: values.noChange,
        rollback_reason: values.rollback_reason,
      })
    },
    onSuccess: () => {
      message.success('进度已更新')
      setFillSub(null)
      form.resetFields()
      queryClient.invalidateQueries({ queryKey: ['progress-table'] })
      queryClient.invalidateQueries({ queryKey: ['progress-records'] })
    },
  })

  const onExport = ({ key }: { key: string }) => {
    exportApi.progress({
      fmt: key,
      sort: filters.sort,
      only_abnormal: filters.only_abnormal || undefined,
      major_id: filters.major_id,
      college_id: filters.college_id,
      status: filters.status,
      keyword: filters.keyword || undefined,
    })
  }

  const columns: ColumnsType<ProgressRow> = [
    {
      title: '子项目',
      dataIndex: 'name',
      width: 180,
      render: (_, r) => <b>{r.name}</b>,
    },
    { title: '大项目', dataIndex: 'major_name', ellipsis: true, width: 170 },
    { title: '学院', dataIndex: 'college_name', width: 110 },
    { title: '负责人', dataIndex: 'owner_name', width: 90, render: (v: string | null) => v ?? '—' },
    {
      title: '计划起止',
      width: 120,
      render: (_, r) =>
        r.has_plan ? (
          <span style={{ fontSize: 12, lineHeight: 1.5 }}>
            {fmtDate(r.plan_start)}
            <br />~ {fmtDate(r.plan_end)}
          </span>
        ) : (
          <span style={{ color: '#9ca3af' }}>未设置</span>
        ),
    },
    {
      title: '时间进度',
      dataIndex: 'time_progress',
      width: 90,
      render: (v: number | null) =>
        v === null ? <span style={{ color: '#9ca3af' }}>—</span> : `${v}%`,
    },
    {
      title: '实际进度',
      dataIndex: 'current_progress',
      width: 130,
      render: (v: number) => (
        <Space size={6} style={{ width: '100%' }}>
          <Progress
            percent={v}
            size="small"
            showInfo={false}
            style={{ width: 56, marginBottom: 0 }}
            strokeColor={v >= 100 ? '#10b981' : v >= 60 ? '#4f46e5' : v >= 30 ? '#d48806' : '#ef4444'}
          />
          <span style={{ fontVariantNumeric: 'tabular-nums', minWidth: 32, textAlign: 'right' }}>
            {v}%
          </span>
        </Space>
      ),
    },
    {
      title: '偏差',
      dataIndex: 'deviation',
      width: 70,
      render: (_, r) => <DeviationText value={r.deviation} />,
    },
    { title: '状态', dataIndex: 'status', width: 90, render: (_, r) => <SubStatusTag status={r.status} /> },
    {
      title: '最近更新',
      dataIndex: 'last_update_at',
      width: 110,
      render: (_, r) => {
        const d = fmtDate(r.last_update_at)
        return r.is_stagnant ? (
          <Tooltip title="超过21天未更新">
            <span className="stale-value">{d}</span>
          </Tooltip>
        ) : (
          d
        )
      },
    },
    {
      title: '操作',
      width: 130,
      render: (_, r) => (
        <Space size={0}>
          <Button type="link" size="small" onClick={() => openFill(r)}>
            填报
          </Button>
          <Button type="link" size="small" onClick={() => openTrend(r)}>
            趋势
          </Button>
        </Space>
      ),
    },
  ]

  return (
    <div className="page-container">
      <PageHeader
        title="进度总表"
        subtitle="默认按偏差从低到高排序，最滞前的排在前面；黄色预警不进待办"
        extra={
          <Space>
            <span style={{ color: '#4b5563' }}>只看异常</span>
            <Switch
              checked={filters.only_abnormal}
              onChange={(v) => patchFilters({ only_abnormal: v })}
            />
            <Dropdown
              menu={{
                items: [
                  { key: 'xlsx', label: 'Excel (.xlsx)' },
                  { key: 'pdf', label: 'PDF' },
                ],
                onClick: onExport,
              }}
            >
              <Button icon={<DownloadOutlined />}>导出</Button>
            </Dropdown>
          </Space>
        }
      />

      <Space wrap style={{ marginBottom: 16 }}>
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
          style={{ width: 160 }}
          value={filters.college_id}
          options={colleges.map((c) => ({ value: c.id, label: c.name }))}
          onChange={(v) => patchFilters({ college_id: v })}
        />
        <Select
          allowClear
          placeholder="状态"
          style={{ width: 120 }}
          value={filters.status}
          options={Object.entries(SUB_STATUS_LABEL).map(([value, label]) => ({ value, label }))}
          onChange={(v) => patchFilters({ status: v })}
        />
        <Input.Search
          allowClear
          placeholder="搜索子项目名称"
          style={{ width: 220 }}
          onSearch={(v) => patchFilters({ keyword: v || undefined })}
        />
        <Select
          value={filters.sort}
          style={{ width: 130 }}
          options={[
            { value: 'deviation', label: '按偏差' },
            { value: 'update', label: '按更新时间' },
            { value: 'progress', label: '按进度' },
          ]}
          onChange={(v) => patchFilters({ sort: v })}
        />
      </Space>

      <Table<ProgressRow>
        className="progress-table"
        rowKey="id"
        loading={isFetching}
        columns={columns}
        dataSource={data?.items ?? []}
        scroll={{ x: 1240 }}
        expandable={{
          expandedRowRender: (r) => (
            <Tabs
              size="small"
              items={[
                {
                  key: 'records',
                  label: '进度留痕',
                  children: <RecordList subId={r.id} />,
                },
                {
                  key: 'attachments',
                  label: '附件材料',
                  children: (
                    <AttachmentPanel bizType="SUB" bizId={r.id} />
                  ),
                },
              ]}
            />
          ),
        }}
        pagination={{
          current: page,
          pageSize: PAGE_SIZE,
          total: data?.total ?? 0,
          showSizeChanger: false,
          showTotal: (t) => `共 ${t} 条`,
          onChange: (p) => setPage(p),
        }}
      />

      <Modal
        title={`填报进度 — ${fillSub?.name ?? ''}`}
        open={!!fillSub}
        onCancel={() => setFillSub(null)}
        onOk={() => form.submit()}
        confirmLoading={fillMutation.isPending}
        okText="提交"
        cancelText="取消"
        width={480}
      >
        <Form form={form} layout="vertical" onFinish={(v) => fillMutation.mutate(v)}>
          <div style={{ marginBottom: 12 }}>
            当前进度：<b>{fillSub?.current_progress ?? 0}%</b>
            {fillSub?.time_progress != null && (
              <span style={{ color: '#6b7280' }}>
                　时间进度 {fillSub.time_progress}%　偏差{' '}
                <DeviationText value={fillSub?.deviation ?? null} />
              </span>
            )}
          </div>

          <Form.Item name="noChange" valuePropName="checked" style={{ marginBottom: 12 }}>
            <Checkbox>本周期无变化（只留下记录，不改变进度数值）</Checkbox>
          </Form.Item>

          <Form.Item name="progress" label="新进度" style={{ marginBottom: 12 }}>
            <ProgressPicker disabled={noChange} />
          </Form.Item>

          <Form.Item name="note" label="说明" style={{ marginBottom: 12 }}>
            <Input.TextArea rows={2} maxLength={500} placeholder="一句话说明（选填）" />
          </Form.Item>

          <Form.Item
            name="rollback_reason"
            label="回调原因"
            dependencies={['progress', 'noChange']}
            extra={noChange ? undefined : '新进度低于当前值时必填'}
            rules={[
              ({ getFieldValue }) => ({
                validator(_, value) {
                  if (getFieldValue('noChange')) return Promise.resolve()
                  const p = getFieldValue('progress')
                  if (
                    typeof p === 'number' &&
                    p < (fillSub?.current_progress ?? 0) &&
                    !String(value ?? '').trim()
                  ) {
                    return Promise.reject(new Error('进度比当前值低（回调），必须填写原因'))
                  }
                  return Promise.resolve()
                },
              }),
            ]}
          >
            <Input.TextArea rows={2} maxLength={500} placeholder="为什么回调？" disabled={noChange} />
          </Form.Item>
        </Form>
      </Modal>

      {/* 进度趋势曲线 */}
      <SubTrendDrawer
        subId={trendSub?.id ?? null}
        subName={trendSub?.name}
        open={trendSub !== null}
        onClose={() => setTrendSub(null)}
      />
    </div>
  )
}
