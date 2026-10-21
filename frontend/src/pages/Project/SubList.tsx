import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { PlusOutlined } from '@ant-design/icons'
import {
  Button,
  DatePicker,
  Form,
  Input,
  InputNumber,
  message,
  Modal,
  Popconfirm,
  Progress,
  Select,
  Space,
  Table,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import dayjs, { type Dayjs } from 'dayjs'

import DataTable from '@/components/DataTable'
import PageHeader from '@/components/PageHeader'
import { SubStatusTag } from '@/components/Tags'
import { majorApi, subApi, systemApi } from '@/api'
import { SUB_STATUS_LABEL, fmtDate } from '@/lib/constants'
import { PAGE_SIZE, tablePagination } from '@/lib/pagination'
import { useDictStore } from '@/stores/dict'
import { useCan } from '@/stores/auth'
import type { Sub } from '@/types'

interface SubFormValues {
  major_id: number
  college_id: number
  name: string
  owner_id?: number
  plan_start?: Dayjs
  plan_end?: Dayjs
  weight?: number
  status?: string
}

/** 人员下拉 */
function OwnerSelect({
  collegeId,
  value,
  onChange,
}: {
  collegeId?: number
  value?: number
  onChange?: (v: number | undefined) => void
}) {
  const [keyword, setKeyword] = useState('')
  const { data: users, isFetching } = useQuery({
    queryKey: ['pick-users', keyword, collegeId],
    queryFn: () =>
      systemPickUsers(keyword, collegeId),
    staleTime: 60_000,
  })
  return (
    <Select
      showSearch
      allowClear
      value={value}
      placeholder="搜索姓名或工号"
      loading={isFetching}
      defaultActiveFirstOption={false}
      filterOption={false}
      onSearch={setKeyword}
      onChange={(v) => onChange?.(v)}
      notFoundContent={isFetching ? '搜索中…' : '无匹配人员'}
      options={(users ?? []).map((u) => ({
        value: u.id,
        label: `${u.name}（${u.staff_no}）`,
      }))}
    />
  )
}

async function systemPickUsers(keyword: string, collegeId?: number) {
  return systemApi.pickUsers({
    keyword: keyword || undefined,
    college_id: collegeId,
  })
}

/** 允许迁移的状态选项（与后端 _ALLOWED_TRANSITIONS 一致） */
function allowedNextStatuses(current: string): { value: string; label: string }[] {
  const map: Record<string, string[]> = {
    NOT_STARTED: ['NOT_STARTED', 'IN_PROGRESS', 'ABORTED'],
    IN_PROGRESS: ['IN_PROGRESS', 'ABORTED', 'CLOSED'],
    ABORTED: ['ABORTED', 'IN_PROGRESS'],
    CLOSED: ['CLOSED'],
  }
  return (map[current] ?? [current]).map((s) => ({ value: s, label: SUB_STATUS_LABEL[s] ?? s }))
}

export default function SubListPage() {
  const queryClient = useQueryClient()
  const majors = useDictStore((s) => s.majors)
  const can = useCan()

  // 新建/编辑/删除子项目需要 project:sub:edit
  // 原来这三个按钮无权限判断，只读用户也能看到（后端会 403）
  const canEdit = can('project:sub:edit')

  // ---------------------------------------------------------------- 筛选
  const [fMajor, setFMajor] = useState<number | undefined>()
  const [fCollege, setFCollege] = useState<number | undefined>()
  const [fStatus, setFStatus] = useState<string | undefined>()
  const [fKeyword, setFKeyword] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(PAGE_SIZE)

  const queryParams = useMemo(
    () => ({
      major_id: fMajor,
      college_id: fCollege,
      status: fStatus,
      keyword: fKeyword || undefined,
      page,
      page_size: pageSize,
    }),
    [fMajor, fCollege, fStatus, fKeyword, page],
  )

  const { data, isLoading } = useQuery({
    queryKey: ['subs', queryParams],
    queryFn: () => subApi.list(queryParams),
  })

  // 学院字典
  const { data: allColleges } = useQuery({
    queryKey: ['colleges'],
    queryFn: async () => {
      const { collegeApi } = await import('@/api')
      return collegeApi.list()
    },
    staleTime: 5 * 60_000,
  })

  // ---------------------------------------------------------------- 新建弹窗
  const [createOpen, setCreateOpen] = useState(false)
  const [createForm] = Form.useForm<SubFormValues>()
  const createMajorId = Form.useWatch('major_id', createForm)

  // 选了大项目后，只允许选已参与该项目的学院
  const { data: createMajorDetail } = useQuery({
    queryKey: ['major', createMajorId],
    queryFn: () => majorApi.detail(createMajorId as number),
    enabled: createMajorId !== undefined,
  })
  const createCollegeOptions = useMemo(
    () =>
      (createMajorDetail?.colleges ?? []).map((c) => ({
        value: c.college_id,
        label: c.college_name,
      })),
    [createMajorDetail],
  )

  const createMutation = useMutation({
    mutationFn: (values: SubFormValues) => {
      const payload: Record<string, unknown> = {
        major_id: values.major_id,
        college_id: values.college_id,
        name: values.name,
        owner_id: values.owner_id ?? null,
        plan_start: values.plan_start?.format('YYYY-MM-DD') ?? null,
        plan_end: values.plan_end?.format('YYYY-MM-DD') ?? null,
        weight: values.weight ?? null,
      }
      return subApi.create(payload)
    },
    onSuccess: () => {
      message.success('子项目已创建')
      setCreateOpen(false)
      createForm.resetFields()
      void queryClient.invalidateQueries({ queryKey: ['subs'] })
      void queryClient.invalidateQueries({ queryKey: ['major-subs'] })
      void queryClient.invalidateQueries({ queryKey: ['majors'] })
    },
  })

  // ---------------------------------------------------------------- 编辑弹窗
  const [editing, setEditing] = useState<Sub | null>(null)
  const [editForm] = Form.useForm<SubFormValues>()

  const editMutation = useMutation({
    mutationFn: (values: SubFormValues) => {
      if (!editing) return Promise.reject(new Error('无编辑对象'))
      const payload: Record<string, unknown> = {
        name: values.name,
        owner_id: values.owner_id ?? null,
        plan_start: values.plan_start?.format('YYYY-MM-DD') ?? null,
        plan_end: values.plan_end?.format('YYYY-MM-DD') ?? null,
        weight: values.weight ?? null,
        status: values.status,
      }
      return subApi.update(editing.id, payload)
    },
    onSuccess: () => {
      message.success('保存成功')
      setEditing(null)
      void queryClient.invalidateQueries({ queryKey: ['subs'] })
      void queryClient.invalidateQueries({ queryKey: ['major-subs'] })
    },
  })

  const removeMutation = useMutation({
    mutationFn: (id: number) => subApi.remove(id),
    onSuccess: () => {
      message.success('已删除')
      void queryClient.invalidateQueries({ queryKey: ['subs'] })
      void queryClient.invalidateQueries({ queryKey: ['major-subs'] })
      void queryClient.invalidateQueries({ queryKey: ['majors'] })
    },
  })

  const openEdit = (record: Sub) => {
    setEditing(record)
    editForm.resetFields()
    editForm.setFieldsValue({
      name: record.name,
      owner_id: record.owner_id ?? undefined,
      plan_start: record.plan_start ? dayjs(record.plan_start) : undefined,
      plan_end: record.plan_end ? dayjs(record.plan_end) : undefined,
      weight: record.weight ?? undefined,
      status: record.status,
    })
  }

  // 编辑时状态变更提示（CLOSED 需 100%）
  const handleEditStatusChange = (v: string) => {
    if (editing && v === 'CLOSED' && editing.current_progress < 100) {
      message.warning('结项需进度达到 100%，否则保存会被拒绝')
    }
  }

  // ---------------------------------------------------------------- 列
  const columns: ColumnsType<Sub> = [
    {
      title: '子项目名称',
      dataIndex: 'name',
      ellipsis: true,
      render: (_, record: Sub) => <span style={{ fontWeight: 500 }}>{record.name}</span>,
    },
    {
      title: '所属大项目',
      dataIndex: 'major_name',
      ellipsis: true,
      width: 200,
    },
    { title: '学院', dataIndex: 'college_name', width: 130 },
    { title: '负责人', dataIndex: 'owner_name', width: 95 },
    { title: '计划开始', dataIndex: 'plan_start', width: 105, render: (v: string | null) => fmtDate(v) },
    { title: '计划结束', dataIndex: 'plan_end', width: 105, render: (v: string | null) => fmtDate(v) },
    { title: '权重', dataIndex: 'weight', width: 70, align: 'center', render: (v: number | null) => v ?? '—' },
    {
      title: '进度',
      dataIndex: 'current_progress',
      width: 140,
      render: (v: number) => <Progress percent={v} size="small" />,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (v: string) => <SubStatusTag status={v} />,
    },
    {
      title: '最近更新',
      dataIndex: 'last_update_at',
      width: 105,
      render: (v: string | null) => fmtDate(v),
    },
    {
      title: '操作',
      key: 'action',
      width: 110,
      render: (_, record: Sub) =>
        canEdit ? (
          <Space size={4}>
            <Button type="link" size="small" onClick={() => openEdit(record)}>
              编辑
            </Button>
            <Popconfirm
              title="确认删除该子项目？"
              description="已结项的子项目不能删除"
              onConfirm={() => removeMutation.mutate(record.id)}
            >
              <Button type="link" size="small" danger>
                删除
              </Button>
            </Popconfirm>
          </Space>
        ) : (
          <span style={{ color: '#9ca3af' }}>—</span>
        ),
    },
  ]

  return (
    <div className="page-container">
      <PageHeader
        title="子项目列表"
        subtitle="进度填报与预警的最小单元，须同时归属大项目和学院"
        extra={
          canEdit && (
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={() => {
                createForm.resetFields()
                setCreateOpen(true)
              }}
            >
              新建子项目
            </Button>
          )
        }
      />

      {/* 筛选 */}
      <Space wrap style={{ marginBottom: 16 }}>
        <Select
          allowClear
          showSearch
          optionFilterProp="label"
          placeholder="所属大项目"
          style={{ width: 240 }}
          value={fMajor}
          onChange={(v) => {
            setFMajor(v)
            setPage(1)
          }}
          options={majors.map((m) => ({ value: m.id, label: m.name }))}
        />
        <Select
          allowClear
          showSearch
          optionFilterProp="label"
          placeholder="学院"
          style={{ width: 160 }}
          value={fCollege}
          onChange={(v) => {
            setFCollege(v)
            setPage(1)
          }}
          options={(allColleges ?? []).map((c) => ({ value: c.id, label: c.name }))}
        />
        <Select
          allowClear
          placeholder="状态"
          style={{ width: 110 }}
          value={fStatus}
          onChange={(v) => {
            setFStatus(v)
            setPage(1)
          }}
          options={Object.entries(SUB_STATUS_LABEL).map(([v, l]) => ({ value: v, label: l }))}
        />
        <Input
          allowClear
          placeholder="子项目名称"
          style={{ width: 180 }}
          value={fKeyword}
          onChange={(e) => {
            setFKeyword(e.target.value)
            setPage(1)
          }}
        />
      </Space>

      <DataTable<Sub>
        rowKey="id"
        loading={isLoading}
        columns={columns}
        dataSource={data?.items ?? []}
        pagination={tablePagination({
          current: page,
          pageSize,
          total: data?.total ?? 0,
          onChange: (p, ps) => {
            setPage(p)
            setPageSize(ps)
          },
        })}
        minTableWidth={1300}
      />

      {/* 新建弹窗 */}
      <Modal
        title="新建子项目"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        confirmLoading={createMutation.isPending}
        onOk={() => createForm.submit()}
        destroyOnHidden
      >
        <Form<SubFormValues>
          form={createForm}
          layout="vertical"
          onFinish={(v) => createMutation.mutate(v)}
        >
          <Form.Item
            name="major_id"
            label="所属大项目"
            rules={[{ required: true, message: '请选择大项目' }]}
          >
            <Select
              showSearch
              placeholder="选择大项目"
              optionFilterProp="label"
              options={majors.map((m) => ({ value: m.id, label: m.name }))}
              onChange={() => createForm.setFieldValue('college_id', undefined)}
            />
          </Form.Item>

          <Form.Item
            name="college_id"
            label="学院"
            rules={[{ required: true, message: '请选择学院' }]}
            extra={
              createMajorId
                ? createCollegeOptions.length === 0
                  ? '该项目尚未配置参与学院，请先在大项目详情中添加'
                  : undefined
                : '请先选择大项目，学院须为该项目已参与的学院'
            }
          >
            <Select
              showSearch
              placeholder={createMajorId ? '选择学院' : '先选择大项目'}
              disabled={!createMajorId}
              optionFilterProp="label"
              options={createCollegeOptions}
              notFoundContent="该大项目暂无参与学院"
            />
          </Form.Item>

          <Form.Item
            name="name"
            label="子项目名称"
            rules={[{ required: true, message: '请输入子项目名称' }]}
          >
            <Input maxLength={255} placeholder="如：实训场地改造" />
          </Form.Item>

          <Form.Item name="owner_id" label="负责人">
            <OwnerSelect collegeId={undefined} />
          </Form.Item>

          <Space style={{ display: 'flex', gap: 12 }}>
            <Form.Item name="plan_start" label="计划开始" style={{ flex: 1 }}>
              <DatePicker style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item
              name="plan_end"
              label="计划结束"
              style={{ flex: 1 }}
              rules={[
                ({ getFieldValue }) => ({
                  validator: (_, v: Dayjs | undefined) => {
                    const start = getFieldValue('plan_start') as Dayjs | undefined
                    if (v && start && v.isBefore(start)) {
                      return Promise.reject(new Error('结束日期不能早于开始日期'))
                    }
                    return Promise.resolve()
                  },
                }),
              ]}
            >
              <DatePicker style={{ width: '100%' }} />
            </Form.Item>
          </Space>

          <Form.Item name="weight" label="权重" extra="汇总时按权重加权；不填按等权">
            <InputNumber min={0} style={{ width: 160 }} placeholder="如 3" />
          </Form.Item>
        </Form>
      </Modal>

      {/* 编辑弹窗 */}
      <Modal
        title={`编辑子项目：${editing?.name ?? ''}`}
        open={editing !== null}
        onCancel={() => setEditing(null)}
        confirmLoading={editMutation.isPending}
        onOk={() => editForm.submit()}
        destroyOnHidden
      >
        <Form<SubFormValues>
          form={editForm}
          layout="vertical"
          onFinish={(v) => editMutation.mutate(v)}
        >
          <Form.Item label="所属大项目 / 学院">
            <Input
              disabled
              value={`${editing?.major_name ?? ''} / ${editing?.college_name ?? ''}`}
            />
          </Form.Item>

          <Form.Item
            name="name"
            label="子项目名称"
            rules={[{ required: true, message: '请输入子项目名称' }]}
          >
            <Input maxLength={255} />
          </Form.Item>

          <Form.Item name="owner_id" label="负责人">
            <OwnerSelect collegeId={editing?.college_id} />
          </Form.Item>

          <Space style={{ display: 'flex', gap: 12 }}>
            <Form.Item name="plan_start" label="计划开始" style={{ flex: 1 }}>
              <DatePicker style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="plan_end" label="计划结束" style={{ flex: 1 }}>
              <DatePicker style={{ width: '100%' }} />
            </Form.Item>
          </Space>

          <Form.Item name="weight" label="权重">
            <InputNumber min={0} style={{ width: 160 }} />
          </Form.Item>

          <Form.Item name="status" label="状态">
            <Select
              options={editing ? allowedNextStatuses(editing.status) : []}
              onChange={handleEditStatusChange}
            />
          </Form.Item>

          {editing && (
            <div
              style={{
                background: '#f5f7ff',
                border: '1px solid #f3f4f6',
                borderRadius: 4,
                padding: '8px 12px',
                fontSize: 13,
                color: '#4b5563',
              }}
            >
              当前进度：{editing.current_progress}%
              {editing.status === 'CLOSED' && (
                <span style={{ color: '#6b7280', marginLeft: 8 }}>（已结项，终态不可再变更）</span>
              )}
            </div>
          )}
        </Form>
      </Modal>
    </div>
  )
}
