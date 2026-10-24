import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { DownOutlined, DownloadOutlined, PlusOutlined } from '@ant-design/icons'
import {
  Button,
  Checkbox,
  DatePicker,
  Dropdown,
  Form,
  Input,
  message,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tooltip,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import dayjs, { type Dayjs } from 'dayjs'

import DataTable from '@/components/DataTable'
import PageHeader from '@/components/PageHeader'
import { MajorStatusTag } from '@/components/Tags'
import { exportApi, majorApi, systemApi } from '@/api'
import { MAJOR_STATUS_LABEL, MAJOR_TYPES, fmtDate } from '@/lib/constants'
import { PAGE_SIZE, tablePagination } from '@/lib/pagination'
import { useAuthStore, useCan } from '@/stores/auth'
import type { Major } from '@/types'

interface MajorFormValues {
  name: string
  type: string
  partner_company?: string
  build_period?: string
  owner_id?: number
  original_start?: Dayjs
  original_end?: Dayjs
  /** 项目状态：只允许 ACTIVE → CLOSED/ABORTED（需求 6.1 第 6 项） */
  status?: 'ACTIVE' | 'CLOSED' | 'ABORTED'
  /** 日报开关：null=跟随全局（需求 6.1 第 5 项：按项目类型开关，试点项目打开） */
  daily_report_enabled?: boolean | null
  /** 变更原因（计划/负责人等变更留痕用） */
  change_reason?: string
}

/** 人员下拉选项（远程搜索） */
function OwnerSelect({
  value,
  onChange,
  placeholder,
}: {
  value?: number
  onChange?: (v: number | undefined) => void
  placeholder?: string
}) {
  const [keyword, setKeyword] = useState('')
  const { data: users, isFetching } = useQuery({
    queryKey: ['pick-users', keyword],
    queryFn: () => systemApi.pickUsers(keyword ? { keyword } : undefined),
    staleTime: 60_000,
  })

  return (
    <Select
      showSearch
      allowClear
      value={value}
      placeholder={placeholder ?? '搜索姓名或工号'}
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

export default function MajorListPage() {
  const queryClient = useQueryClient()
  const can = useCan()
  const canEdit = can('project:major:edit')

  // ---------------------------------------------------------------- 筛选
  const [keyword, setKeyword] = useState('')
  const [type, setType] = useState<string | undefined>()
  const [status, setStatus] = useState<string | undefined>()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(PAGE_SIZE)

  const queryParams = useMemo(
    () => ({
      keyword: keyword || undefined,
      type,
      status,
      page,
      page_size: pageSize,
    }),
    [keyword, type, status, page],
  )

  const { data, isLoading } = useQuery({
    queryKey: ['majors', queryParams],
    queryFn: () => majorApi.list(queryParams),
  })

  // ---------------------------------------------------------------- 弹窗
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<Major | null>(null)
  const [form] = Form.useForm<MajorFormValues>()

  const openCreate = () => {
    setEditing(null)
    form.resetFields()
    form.setFieldsValue({ type: '其他' })
    setModalOpen(true)
  }

  const openEdit = (record: Major) => {
    setEditing(record)
    form.resetFields()
    form.setFieldsValue({
      name: record.name,
      type: record.type,
      partner_company: record.partner_company ?? undefined,
      build_period: record.build_period ?? undefined,
      owner_id: record.owner_id ?? undefined,
      // 原始计划锁定，不回显到表单
      status: record.status,
      daily_report_enabled: record.daily_report_enabled ?? undefined,
    })
    setModalOpen(true)
  }

  const saveMutation = useMutation({
    mutationFn: async (values: MajorFormValues): Promise<unknown> => {
      const payload: Record<string, unknown> = {
        name: values.name,
        type: values.type,
        partner_company: values.partner_company || null,
        build_period: values.build_period || null,
        owner_id: values.owner_id ?? null,
        change_reason: values.change_reason || null,
      }
      if (!editing) {
        // 原始计划仅新建时提交
        payload.original_start = values.original_start?.format('YYYY-MM-DD') ?? null
        payload.original_end = values.original_end?.format('YYYY-MM-DD') ?? null
      } else {
        // 状态只在编辑时提交（新建默认 ACTIVE）
        if (values.status) payload.status = values.status
        // 日报开关：null 表示跟随全局；仅编辑时可改
        payload.daily_report_enabled = values.daily_report_enabled ?? null
      }
      return editing ? majorApi.update(editing.id, payload) : majorApi.create(payload)
    },
    onSuccess: () => {
      message.success(editing ? '保存成功' : '创建成功')
      setModalOpen(false)
      void queryClient.invalidateQueries({ queryKey: ['majors'] })
      void queryClient.invalidateQueries({ queryKey: ['majors-brief'] })
      void queryClient.invalidateQueries({ queryKey: ['major'] })
    },
  })

  // ---------------------------------------------------------------- 结项异议退回
  // 需求 6.3 第 27 项：大项目负责人若对结项有异议，可在结项后 7 天内提出并退回。
  const [objectionTarget, setObjectionTarget] = useState<Major | null>(null)
  const [objectionForm] = Form.useForm<{ reason: string }>()
  const objectionMutation = useMutation({
    mutationFn: ({ id, reason }: { id: number; reason: string }) =>
      majorApi.objection(id, reason),
    onSuccess: () => {
      message.success('已提出异议，项目已退回「在建」')
      setObjectionTarget(null)
      objectionForm.resetFields()
      void queryClient.invalidateQueries({ queryKey: ['majors'] })
      void queryClient.invalidateQueries({ queryKey: ['major'] })
    },
  })

  /** 结项后 7 天内允许提异议 */
  const canObject = (m: Major): boolean => {
    if (m.status !== 'CLOSED') return false
    if (!m.close_objection_at) {
      // 没有异议记录时，用「结项时间」近似：这里以后端返回的 close 时间不可得，
      // 保守起见只要不是已提过异议就允许（后端会按 7 天再校验一次）
      return true
    }
    return false
  }

  const removeMutation = useMutation({
    mutationFn: (id: number) => majorApi.remove(id),
    onSuccess: () => {
      message.success('已删除')
      void queryClient.invalidateQueries({ queryKey: ['majors'] })
      void queryClient.invalidateQueries({ queryKey: ['majors-brief'] })
    },
  })

  // ---------------------------------------------------------------- 导出
  const [exportOpen, setExportOpen] = useState(false)
  const [exportFmt, setExportFmt] = useState<'xlsx' | 'pdf'>('xlsx')
  const [pickedFields, setPickedFields] = useState<string[]>([])

  const { data: fieldDefs } = useQuery({
    queryKey: ['export-fields'],
    queryFn: exportApi.fields,
    staleTime: 10 * 60_000,
    enabled: exportOpen,
  })

  // 打开导出弹窗时默认全选
  useEffect(() => {
    if (exportOpen && fieldDefs && pickedFields.length === 0) {
      setPickedFields(fieldDefs.major.map((f) => f.key))
    }
  }, [exportOpen, fieldDefs, pickedFields.length])

  const exportMutation = useMutation({
    mutationFn: (params: { fmt: 'xlsx' | 'pdf'; fields?: string }) =>
      exportApi.majors({
        fmt: params.fmt,
        fields: params.fields,
        // 带上当前筛选条件
        keyword: keyword || undefined,
        type: type || undefined,
        status: status || undefined,
      }),
    onSuccess: () => {
      message.success('导出已开始，请查看浏览器下载')
      setExportOpen(false)
    },
  })

  // ---------------------------------------------------------------- 列
  const columns: ColumnsType<Major> = [
    {
      title: '项目名称',
      dataIndex: 'name',
      ellipsis: true,
      render: (_, record: Major) => (
        <Link to={`/project/majors/${record.id}`} style={{ fontWeight: 500 }}>
          {record.name}
        </Link>
      ),
    },
    { title: '类型', dataIndex: 'type', width: 168, ellipsis: true },
    { title: '合作企业', dataIndex: 'partner_company', width: 140, ellipsis: true },
    { title: '总负责人', dataIndex: 'owner_name', width: 100 },
    { title: '参与学院', dataIndex: 'college_count', width: 90, align: 'center' },
    { title: '子项目', dataIndex: 'sub_count', width: 80, align: 'center' },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (v: string) => <MajorStatusTag status={v} />,
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      width: 110,
      render: (v: string) => fmtDate(v),
    },
    {
      title: '操作',
      key: 'action',
      width: 150,
      render: (_, record: Major) =>
        canEdit ? (
          <Space size={4}>
            <Button type="link" size="small" onClick={() => openEdit(record)}>
              编辑
            </Button>
            {canObject(record) && (
              <Tooltip title="结项后 7 天内可提出异议并退回">
                <Button
                  type="link"
                  size="small"
                  onClick={() => {
                    setObjectionTarget(record)
                    objectionForm.resetFields()
                  }}
                >
                  提异议
                </Button>
              </Tooltip>
            )}
            <Popconfirm
              title="确认删除该项目？"
              description="项目下有子项目时无法删除"
              onConfirm={() => removeMutation.mutate(record.id)}
            >
              <Button type="link" size="small" danger>
                删除
              </Button>
            </Popconfirm>
          </Space>
        ) : null,
    },
  ]

  return (
    <div className="page-container">
      <PageHeader
        title="大项目列表"
        subtitle="学校立项的产教融合项目，及其参与学院与子项目分解"
        extra={
          <Space>
            <Dropdown
              menu={{
                items: [
                  { key: 'xlsx', label: '导出 Excel（可选字段）' },
                  { key: 'pdf', label: '导出 PDF（可选字段）' },
                ],
                onClick: ({ key }) => {
                  setExportFmt(key as 'xlsx' | 'pdf')
                  setExportOpen(true)
                },
              }}
            >
              <Button icon={<DownloadOutlined />}>
                <Space size={4}>
                  导出
                  <DownOutlined style={{ fontSize: 10 }} />
                </Space>
              </Button>
            </Dropdown>
            {canEdit && (
              <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
                新建项目
              </Button>
            )}
          </Space>
        }
      />

      {/* 筛选区 */}
      <Space wrap style={{ marginBottom: 16 }}>
        <Input
          allowClear
          placeholder="项目名称"
          style={{ width: 220 }}
          value={keyword}
          onChange={(e) => {
            setKeyword(e.target.value)
            setPage(1)
          }}
          onPressEnter={() => queryClient.invalidateQueries({ queryKey: ['majors'] })}
        />
        <Select
          allowClear
          placeholder="项目类型"
          style={{ width: 170 }}
          value={type}
          onChange={(v) => {
            setType(v)
            setPage(1)
          }}
          options={MAJOR_TYPES.map((t) => ({ value: t, label: t }))}
        />
        <Select
          allowClear
          placeholder="项目状态"
          style={{ width: 120 }}
          value={status}
          onChange={(v) => {
            setStatus(v)
            setPage(1)
          }}
          options={Object.entries(MAJOR_STATUS_LABEL).map(([v, l]) => ({ value: v, label: l }))}
        />
      </Space>

      <DataTable<Major>
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
        minTableWidth={1100}
      />

      {/* 新建 / 编辑 */}
      <Modal
        title={editing ? '编辑项目' : '新建项目'}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        confirmLoading={saveMutation.isPending}
        onOk={() => form.submit()}
        destroyOnHidden
      >
        <Form<MajorFormValues>
          form={form}
          layout="vertical"
          onFinish={(v) => saveMutation.mutate(v)}
        >
          <Form.Item
            name="name"
            label="项目名称"
            rules={[{ required: true, message: '请输入项目名称' }]}
          >
            <Input placeholder="如：智慧物流产教融合实训基地建设项目" maxLength={255} />
          </Form.Item>

          <Form.Item name="type" label="项目类型" rules={[{ required: true }]}>
            <Select options={MAJOR_TYPES.map((t) => ({ value: t, label: t }))} />
          </Form.Item>

          <Space style={{ display: 'flex', gap: 12 }}>
            <Form.Item name="partner_company" label="合作企业" style={{ flex: 1, minWidth: 220 }}>
              <Input placeholder="企业名称" maxLength={255} />
            </Form.Item>
            <Form.Item name="build_period" label="建设周期" style={{ width: 140 }}>
              <Input placeholder="如 2026-2028" maxLength={64} />
            </Form.Item>
          </Space>

          <Form.Item name="owner_id" label="总负责人">
            <OwnerSelect />
          </Form.Item>

          {editing && (
            <>
              <Space style={{ display: 'flex', gap: 12 }}>
                <Form.Item
                  name="status"
                  label="项目状态"
                  style={{ flex: 1 }}
                  extra="结项需所有子项目已结项/中止；结项后只能看不能改"
                >
                  <Select
                    options={[
                      // 只允许单向前进：ACTIVE → CLOSED/ABORTED，已结项不可回退
                      { value: 'ACTIVE', label: '在建', disabled: editing.status !== 'ACTIVE' },
                      { value: 'CLOSED', label: '已结项', disabled: editing.status !== 'ACTIVE' },
                      { value: 'ABORTED', label: '已中止', disabled: editing.status !== 'ACTIVE' },
                    ]}
                  />
                </Form.Item>
                <Form.Item
                  name="daily_report_enabled"
                  label="本项目日报"
                  style={{ flex: 1 }}
                  extra="按项目类型开关（需求 6.1 第 5 项），默认跟随全局"
                >
                  <Select
                    allowClear
                    placeholder="跟随全局"
                    options={[
                      { value: true, label: '开启（试点项目）' },
                      { value: false, label: '关闭' },
                    ]}
                  />
                </Form.Item>
              </Space>
              <Form.Item name="change_reason" label="变更原因（选填）">
                <Input placeholder="改动负责人/计划/状态时建议填写，便于台账追溯" maxLength={200} />
              </Form.Item>
            </>
          )}

          {!editing && (
            <Space style={{ display: 'flex', gap: 12 }}>
              <Form.Item
                name="original_start"
                label="计划开始"
                style={{ flex: 1 }}
                rules={[
                  {
                    validator: (_, v: Dayjs | undefined) => {
                      const end = form.getFieldValue('original_end') as Dayjs | undefined
                      if (v && end && v.isAfter(end)) {
                        return Promise.reject(new Error('开始日期不能晚于结束日期'))
                      }
                      return Promise.resolve()
                    },
                  },
                ]}
              >
                <DatePicker style={{ width: '100%' }} placeholder="立项时锁定" />
              </Form.Item>
              <Form.Item name="original_end" label="计划结束" style={{ flex: 1 }}>
                <DatePicker style={{ width: '100%' }} />
              </Form.Item>
            </Space>
          )}

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
              原始计划：{fmtDate(editing.original_start)} ~ {fmtDate(editing.original_end)}
              <span style={{ color: '#6b7280', marginLeft: 8 }}>（立项时锁定，变更需走变更登记）</span>
            </div>
          )}
        </Form>
      </Modal>

      {/* 结项异议弹窗（需求 6.3 第 27 项） */}
      <Modal
        title={`提出结项异议 — ${objectionTarget?.name ?? ''}`}
        open={!!objectionTarget}
        onCancel={() => setObjectionTarget(null)}
        confirmLoading={objectionMutation.isPending}
        okText="确认退回"
        cancelText="取消"
        onOk={() => objectionForm.submit()}
        destroyOnHidden
      >
        <Form
          form={objectionForm}
          layout="vertical"
          onFinish={(v) =>
            objectionMutation.mutate({ id: objectionTarget!.id, reason: v.reason })
          }
        >
          <Form.Item
            name="reason"
            label="异议理由（必填）"
            rules={[{ required: true, message: '请填写异议理由' }]}
          >
            <Input.TextArea
              rows={3}
              maxLength={500}
              placeholder="例如：设备未验收、材料不齐，建议退回补充"
            />
          </Form.Item>
        </Form>
      </Modal>

      {/* 导出字段勾选 */}
      <Modal
        title={`导出${exportFmt === 'pdf' ? ' PDF' : ' Excel'} — 选择字段`}
        open={exportOpen}
        onCancel={() => setExportOpen(false)}
        onOk={() =>
          exportMutation.mutate({
            fmt: exportFmt,
            fields: pickedFields.length ? pickedFields.join(',') : undefined,
          })
        }
        okText="开始导出"
        confirmLoading={exportMutation.isPending}
        okButtonProps={{ disabled: pickedFields.length === 0 }}
        width={560}
      >
        <div style={{ marginBottom: 12, color: '#6b7280', fontSize: 13 }}>
          勾选需要导出的列；当前筛选条件（关键词/类型/状态）会一并生效。
        </div>
        <div style={{ marginBottom: 12 }}>
          <Space>
            <Button size="small" onClick={() => setPickedFields(fieldDefs?.major.map((f) => f.key) ?? [])}>
              全选
            </Button>
            <Button size="small" onClick={() => setPickedFields([])}>
              清空
            </Button>
            <span style={{ color: '#6b7280', fontSize: 12 }}>
              已选 {pickedFields.length} / {fieldDefs?.major.length ?? 0} 项
            </span>
          </Space>
        </div>
        <Checkbox.Group
          value={pickedFields}
          onChange={(v) => setPickedFields(v as string[])}
          style={{ width: '100%' }}
        >
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(3, 1fr)',
              gap: '8px 12px',
            }}
          >
            {(fieldDefs?.major ?? []).map((f) => (
              <Checkbox key={f.key} value={f.key}>
                {f.label}
              </Checkbox>
            ))}
          </div>
        </Checkbox.Group>
      </Modal>
    </div>
  )
}
