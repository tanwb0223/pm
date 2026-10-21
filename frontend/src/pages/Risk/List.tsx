import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  App,
  Button,
  Checkbox,
  DatePicker,
  Form,
  Input,
  Modal,
  Popconfirm,
  Radio,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
} from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import dayjs, { type Dayjs } from 'dayjs'

import DataTable from '@/components/DataTable'
import PageHeader from '@/components/PageHeader'
import { RiskLevelTag } from '@/components/Tags'
import { majorApi, riskApi, subApi, systemApi } from '@/api'
import { fmtDate } from '@/lib/constants'
import { useDictStore } from '@/stores/dict'
import { useAuthStore, useCan } from '@/stores/auth'
import type { RiskItem, Sub } from '@/types'
import { tablePagination } from '@/lib/pagination'

const LEVEL_OPTIONS = [
  { value: 'HIGH', label: '高' },
  { value: 'MEDIUM', label: '中' },
  { value: 'LOW', label: '低' },
]

interface RiskFormValues {
  major_id: number
  sub_id?: number | null
  level: 'HIGH' | 'MEDIUM' | 'LOW'
  description: string
  owner_id?: number | null
  plan_close_date: Dayjs
}

export default function RiskListPage() {
  const { message } = App.useApp()
  const queryClient = useQueryClient()
  const can = useCan()
  const majors = useDictStore((s) => s.majors)

  const [params, setParams] = useState<{
    major_id?: number
    level?: string
    status?: string
    only_overdue?: boolean
    page: number
    page_size: number
  }>({ page: 1, page_size: 20 })

  const [editing, setEditing] = useState<RiskItem | null>(null)
  const [modalOpen, setModalOpen] = useState(false)
  const [form] = Form.useForm<RiskFormValues>()
  const [formMajorId, setFormMajorId] = useState<number | null>(null)

  const { data, isLoading } = useQuery({
    queryKey: ['risk-list', params],
    queryFn: () => riskApi.list(params as Record<string, unknown>),
  })

  // 表单里选了大项目 → 拉取其子项目
  const { data: subPage } = useQuery({
    queryKey: ['risk-form-subs', formMajorId],
    queryFn: () => subApi.list({ major_id: formMajorId!, page: 1, page_size: 200 }),
    enabled: formMajorId !== null,
  })

  // 负责人下拉（不限权限的 picker 接口）
  const { data: pickUsers = [] } = useQuery({
    queryKey: ['pick-users'],
    queryFn: () => systemApi.pickUsers({ limit: 200 }),
  })

  const saveMut = useMutation({
    mutationFn: async (values: RiskFormValues) => {
      const payload = {
        major_id: values.major_id,
        sub_id: values.sub_id ?? null,
        level: values.level,
        description: values.description,
        owner_id: values.owner_id ?? null,
        plan_close_date: values.plan_close_date.format('YYYY-MM-DD'),
      }
      if (editing) return riskApi.update(editing.id, payload)
      return riskApi.create(payload)
    },
    onSuccess: () => {
      message.success(editing ? '风险已更新' : '风险已登记')
      setModalOpen(false)
      setEditing(null)
      form.resetFields()
      setFormMajorId(null)
      void queryClient.invalidateQueries({ queryKey: ['risk-list'] })
    },
  })

  const closeMut = useMutation({
    mutationFn: (id: number) => riskApi.close(id),
    onSuccess: () => {
      message.success('风险已闭环')
      void queryClient.invalidateQueries({ queryKey: ['risk-list'] })
    },
  })

  const removeMut = useMutation({
    mutationFn: (id: number) => riskApi.remove(id),
    onSuccess: () => {
      message.success('已删除')
      void queryClient.invalidateQueries({ queryKey: ['risk-list'] })
    },
  })

  const openCreate = () => {
    setEditing(null)
    setFormMajorId(null)
    form.resetFields()
    form.setFieldsValue({ level: 'MEDIUM' })
    setModalOpen(true)
  }

  const openEdit = (r: RiskItem) => {
    setEditing(r)
    setFormMajorId(r.major_id)
    form.setFieldsValue({
      major_id: r.major_id,
      sub_id: r.sub_id ?? undefined,
      level: r.level,
      description: r.description,
      owner_id: r.owner_id ?? undefined,
      plan_close_date: dayjs(r.plan_close_date),
    })
    setModalOpen(true)
  }

  const columns = useMemo(
    () => [
      {
        title: '风险描述',
        dataIndex: 'description',
        key: 'description',
        ellipsis: true,
        render: (v: string) => (
          <Tooltip title={v} placement="topLeft">
            {v}
          </Tooltip>
        ),
      },
      {
        title: '关联大项目',
        dataIndex: 'major_id',
        key: 'major_id',
        width: 200,
        ellipsis: true,
        render: (id: number) => majors.find((m) => m.id === id)?.name ?? `#${id}`,
      },
      {
        title: '等级',
        dataIndex: 'level',
        key: 'level',
        width: 70,
        render: (v: string) => <RiskLevelTag level={v} />,
      },
      {
        title: '负责人',
        dataIndex: 'owner_name',
        key: 'owner_name',
        width: 100,
        render: (v: string | null) => v ?? '—',
      },
      {
        title: '计划闭环日',
        dataIndex: 'plan_close_date',
        key: 'plan_close_date',
        width: 110,
        render: (v: string) => fmtDate(v),
      },
      {
        title: '实际闭环日',
        dataIndex: 'actual_close_date',
        key: 'actual_close_date',
        width: 110,
        render: (v: string | null) => (v ? fmtDate(v) : '—'),
      },
      {
        title: '状态',
        dataIndex: 'status',
        key: 'status',
        width: 90,
        render: (v: string) =>
          v === 'OPEN' ? <Tag color="red">未闭环</Tag> : <Tag color="green">已闭环</Tag>,
      },
      {
        title: '超期',
        dataIndex: 'overdue_days',
        key: 'overdue_days',
        width: 90,
        render: (v: number) =>
          v > 0 ? <Tag color="red">超{v}天</Tag> : <span style={{ color: '#9ca3af' }}>—</span>,
      },
      {
        title: '操作',
        key: 'act',
        width: 160,
        render: (_: unknown, r: RiskItem) =>
          can('risk:edit') ? (
            <Space size={4}>
              <Button type="link" size="small" onClick={() => openEdit(r)}>
                编辑
              </Button>
              {r.status === 'OPEN' && (
                <Popconfirm
                  title="确认闭环该风险？"
                  description="闭环后自动记录实际闭环日期"
                  onConfirm={() => closeMut.mutate(r.id)}
                >
                  <Button type="link" size="small">
                    闭环
                  </Button>
                </Popconfirm>
              )}
              <Popconfirm title="确认删除该风险记录？" onConfirm={() => removeMut.mutate(r.id)}>
                <Button type="link" size="small" danger>
                  删除
                </Button>
              </Popconfirm>
            </Space>
          ) : (
            '—'
          ),
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [majors, can],
  )

  const subOptions = (subPage?.items ?? []).map((s: Sub) => ({ value: s.id, label: s.name }))

  return (
    <div className="page-container">
      <PageHeader
        title="风险登记"
        subtitle="登记项目风险并跟踪闭环；风险超期会触发 AL-08 预警"
        extra={
          can('risk:edit') && (
            <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
              登记风险
            </Button>
          )
        }
      />

      <Space wrap style={{ marginBottom: 16 }}>
        <Select
          allowClear
          showSearch
          optionFilterProp="label"
          placeholder="大项目"
          style={{ width: 240 }}
          options={majors.map((m) => ({ value: m.id, label: m.name }))}
          onChange={(v) => setParams((p) => ({ ...p, major_id: v, page: 1 }))}
        />
        <Select
          allowClear
          placeholder="等级"
          style={{ width: 90 }}
          options={LEVEL_OPTIONS}
          onChange={(v) => setParams((p) => ({ ...p, level: v, page: 1 }))}
        />
        <Select
          allowClear
          placeholder="状态"
          style={{ width: 110 }}
          options={[
            { value: 'OPEN', label: '未闭环' },
            { value: 'CLOSED', label: '已闭环' },
          ]}
          onChange={(v) => setParams((p) => ({ ...p, status: v, page: 1 }))}
        />
        <Checkbox
          onChange={(e) => setParams((p) => ({ ...p, only_overdue: e.target.checked || undefined, page: 1 }))}
        >
          只看超期
        </Checkbox>
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

      <Modal
        title={editing ? '编辑风险' : '登记风险'}
        open={modalOpen}
        confirmLoading={saveMut.isPending}
        width={560}
        onCancel={() => {
          setModalOpen(false)
          setEditing(null)
          form.resetFields()
          setFormMajorId(null)
        }}
        onOk={async () => {
          const values = await form.validateFields()
          saveMut.mutate(values)
        }}
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="major_id"
            label="关联大项目"
            rules={[{ required: true, message: '请选择大项目' }]}
          >
            <Select
              showSearch
              optionFilterProp="label"
              placeholder="选择大项目"
              options={majors.map((m) => ({ value: m.id, label: m.name }))}
              onChange={(v: number) => {
                setFormMajorId(v)
                form.setFieldValue('sub_id', undefined)
              }}
            />
          </Form.Item>
          <Form.Item name="sub_id" label="关联子项目（可空）">
            <Select
              allowClear
              showSearch
              optionFilterProp="label"
              placeholder={formMajorId ? '选择子项目' : '先选择大项目'}
              options={subOptions}
              disabled={!formMajorId}
            />
          </Form.Item>
          <Form.Item name="level" label="风险等级" rules={[{ required: true }]}>
            <Radio.Group optionType="button" options={LEVEL_OPTIONS} />
          </Form.Item>
          <Form.Item
            name="description"
            label="风险描述"
            rules={[{ required: true, message: '请描述风险' }]}
          >
            <Input.TextArea rows={3} maxLength={2000} showCount placeholder="描述风险内容、影响范围" />
          </Form.Item>
          <Form.Item name="owner_id" label="负责人（可空）">
            <Select
              allowClear
              showSearch
              optionFilterProp="label"
              placeholder="选择负责人"
              options={pickUsers.map((u) => ({
                value: u.id,
                label: `${u.name}（${u.staff_no}${u.college_name ? ` / ${u.college_name}` : ''}）`,
              }))}
            />
          </Form.Item>
          <Form.Item
            name="plan_close_date"
            label="计划闭环日期"
            rules={[{ required: true, message: '请选择计划闭环日期' }]}
          >
            <DatePicker style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
