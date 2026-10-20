import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  App,
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import { PlusOutlined, UploadOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'

import PageHeader from '@/components/PageHeader'
import ImportModal from '@/components/ImportModal'
import { collegeApi } from '@/api'
import { useAuthStore } from '@/stores/auth'
import type { College } from '@/types'

interface OrgFormValues {
  name: string
  code?: string | null
  parent_id?: number | null
  sort?: number
}

/**
 * 组织管理：学校 / 二级学院两级组织
 *
 * 这里是所有基础数据的源头——用户归属、大项目参与学院、子项目归属都引用它，
 * 首次上线必须先在这里录入学院，否则后面任何地方都选不到学院。
 */
export default function OrgPage() {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const hasPerm = useAuthStore((s) => s.hasPerm)
  const canWrite = hasPerm('system:org')

  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<College | null>(null)
  const [importOpen, setImportOpen] = useState(false)
  const [form] = Form.useForm<OrgFormValues>()

  const { data, isLoading } = useQuery({ queryKey: ['colleges'], queryFn: collegeApi.list })

  const colleges = data ?? []
  const nameById = useMemo(() => new Map(colleges.map((c) => [c.id, c.name])), [colleges])
  const childCount = useMemo(() => {
    const map = new Map<number, number>()
    for (const c of colleges) {
      if (c.parent_id) map.set(c.parent_id, (map.get(c.parent_id) ?? 0) + 1)
    }
    return map
  }, [colleges])

  const invalidate = () => qc.invalidateQueries({ queryKey: ['colleges'] })

  const saveMut = useMutation({
    mutationFn: async (values: OrgFormValues) => {
      const payload: Partial<College> = {
        name: values.name.trim(),
        code: values.code?.trim() || null,
        parent_id: values.parent_id ?? null,
        sort: values.sort ?? 0,
      }
      if (editing) {
        await collegeApi.update(editing.id, payload)
      } else {
        await collegeApi.create(payload)
      }
    },
    onSuccess: () => {
      message.success(editing ? '已保存' : '学院已创建')
      closeForm()
      invalidate()
    },
  })

  const removeMut = useMutation({
    mutationFn: (id: number) => collegeApi.remove(id),
    onSuccess: () => {
      message.success('已删除')
      invalidate()
    },
  })

  const openCreate = () => {
    setEditing(null)
    form.resetFields()
    const maxSort = colleges.reduce((m, c) => Math.max(m, c.sort ?? 0), 0)
    form.setFieldsValue({ sort: maxSort + 10 })
    setFormOpen(true)
  }

  const openEdit = (row: College) => {
    setEditing(row)
    form.resetFields()
    form.setFieldsValue({
      name: row.name,
      code: row.code,
      parent_id: row.parent_id,
      sort: row.sort,
    })
    setFormOpen(true)
  }

  const closeForm = () => {
    setFormOpen(false)
    setEditing(null)
    form.resetFields()
  }

  /** 自己 + 所有下级 id：不能挂到自己或自己的后代下面（后端也会拦截环） */
  const forbiddenParentIds = useMemo(() => {
    const bad = new Set<number>()
    if (!editing) return bad
    bad.add(editing.id)
    let grew = true
    while (grew) {
      grew = false
      for (const c of colleges) {
        if (c.parent_id !== null && bad.has(c.parent_id) && !bad.has(c.id)) {
          bad.add(c.id)
          grew = true
        }
      }
    }
    return bad
  }, [colleges, editing])

  const parentOptions = colleges
    .filter((c) => !forbiddenParentIds.has(c.id))
    .map((c) => ({ value: c.id, label: `${c.name}${c.code ? `（${c.code}）` : ''}` }))

  const columns: ColumnsType<College> = [
    {
      title: '学院 / 组织名称',
      dataIndex: 'name',
      render: (v: string, row) => (
        <Space>
          <span>{v}</span>
          {row.parent_id === null && <Tag color="blue">校级</Tag>}
        </Space>
      ),
    },
    {
      title: '编码',
      dataIndex: 'code',
      width: 140,
      render: (v: string | null) => (v ? <code style={{ fontSize: 13 }}>{v}</code> : '—'),
    },
    {
      title: '上级组织',
      dataIndex: 'parent_id',
      width: 180,
      render: (v: number | null) => (v ? (nameById.get(v) ?? `#${v}`) : '—'),
    },
    {
      title: '下级',
      key: 'children',
      width: 80,
      align: 'center',
      render: (_, row) => childCount.get(row.id) ?? 0,
    },
    { title: '排序', dataIndex: 'sort', width: 80, align: 'center' },
    {
      title: '操作',
      key: 'op',
      width: 150,
      render: (_, row) =>
        canWrite && (
          <Space>
            <Button type="link" size="small" onClick={() => openEdit(row)}>
              编辑
            </Button>
            <Popconfirm
              title="确认删除该学院？"
              description="已经有用户、参与项目或子项目的学院不能删除"
              onConfirm={() => removeMut.mutate(row.id)}
            >
              <Button type="link" size="small" danger>
                删除
              </Button>
            </Popconfirm>
          </Space>
        ),
    },
  ]

  return (
    <div className="page-container">
      <PageHeader
        title="组织管理"
        subtitle="维护学校与二级学院两级组织；用户归属、项目分工、子项目归属都从这里取数，请先录入学院"
        extra={
          canWrite && (
            <Space>
              <Button icon={<UploadOutlined />} onClick={() => setImportOpen(true)}>
                批量导入
              </Button>
              <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
                新增学院
              </Button>
            </Space>
          )
        }
      />

      {colleges.length === 0 && !isLoading && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message="系统中还没有任何学院"
          description="学院是基础数据：没有学院，用户无法归属学院，大项目无法配置参与学院，子项目也建不出来。请先「新增学院」，或下载模板后「批量导入」。"
        />
      )}

      <Table<College>
        rowKey="id"
        loading={isLoading}
        columns={columns}
        dataSource={colleges}
        pagination={false}
        footer={() => (
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
            共 {colleges.length} 个组织；已有用户、参与项目或子项目的学院不能删除
          </Typography.Text>
        )}
      />

      <Modal
        title={editing ? '编辑学院' : '新增学院'}
        open={formOpen}
        confirmLoading={saveMut.isPending}
        onOk={() => form.submit()}
        onCancel={closeForm}
        destroyOnHidden
      >
        <Form<OrgFormValues> form={form} layout="vertical" onFinish={(v) => saveMut.mutate(v)}>
          <Form.Item
            label="学院 / 组织名称"
            name="name"
            rules={[{ required: true, message: '请输入学院名称' }]}
          >
            <Input placeholder="如：物流管理学院" maxLength={128} />
          </Form.Item>
          <Form.Item
            label="编码"
            name="code"
            extra="可留空；填写后须唯一，批量导入时按编码匹配已有学院"
          >
            <Input placeholder="如：WL" maxLength={32} />
          </Form.Item>
          <Form.Item label="上级组织" name="parent_id" extra="留空表示学校本级；也可挂到某个学院下面">
            <Select
              allowClear
              showSearch
              optionFilterProp="label"
              placeholder="留空 = 学校本级"
              options={parentOptions}
            />
          </Form.Item>
          <Form.Item label="排序" name="sort" extra="数字小的排前面">
            <InputNumber style={{ width: '100%' }} min={0} max={9999} />
          </Form.Item>
        </Form>
      </Modal>

      <ImportModal
        open={importOpen}
        kind="college"
        onClose={() => setImportOpen(false)}
        onSuccess={invalidate}
      />
    </div>
  )
}
