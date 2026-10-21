import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Button,
  Form,
  Input,
  Modal,
  Popconfirm,
  Result,
  Select,
  Space,
  Table,
  Tag,
  App,
} from 'antd'
import { PlusOutlined, SearchOutlined, UploadOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'

import DataTable from '@/components/DataTable'
import PageHeader from '@/components/PageHeader'
import ImportModal from '@/components/ImportModal'
import { systemApi, collegeApi } from '@/api'
import { fmtDateTime } from '@/lib/constants'
import { useAuthStore, useCan } from '@/stores/auth'
import type { UserItem } from '@/types'
import { tablePagination } from '@/lib/pagination'

/** 用户管理（权限守卫在外层，内层只在有权限时挂载，避免 Hook 数量变化） */
export default function UserPage() {
  const can = useCan()
  if (!can('system:user')) {
    return (
      <div className="page-container">
        <Result status="403" title="无权限" subTitle="需要 system:user 权限才能查看用户列表" />
      </div>
    )
  }
  return <UserPageInner />
}

function UserPageInner() {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const can = useCan()

  const [keyword, setKeyword] = useState('')
  const [importOpen, setImportOpen] = useState(false)
  const [collegeId, setCollegeId] = useState<number | undefined>()
  const [status, setStatus] = useState<string | undefined>()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)

  const [editing, setEditing] = useState<UserItem | null>(null)
  const [creating, setCreating] = useState(false)
  const [form] = Form.useForm()

  const canView = true
  const canWrite = can('system:user')

  const { data: colleges } = useQuery({
    queryKey: ['colleges'],
    queryFn: collegeApi.list,
    enabled: canView,
  })

  const { data, isLoading } = useQuery({
    queryKey: ['users', { keyword, collegeId, status, page, pageSize }],
    queryFn: () =>
      systemApi.users({
        keyword: keyword || undefined,
        college_id: collegeId,
        status,
        page,
        page_size: pageSize,
      }),
    // 无权限时不发请求：否则会弹一次 403 错误提示
    enabled: canView,
  })


  const invalidate = () => qc.invalidateQueries({ queryKey: ['users'] })

  const saveMut = useMutation({
    mutationFn: async (values: Record<string, unknown>) => {
      if (editing) {
        await systemApi.updateUser(editing.id, values)
      } else {
        await systemApi.createUser(values)
      }
    },
    onSuccess: () => {
      message.success(editing ? '已保存' : '用户已创建')
      setEditing(null)
      setCreating(false)
      invalidate()
    },
  })

  const disableMut = useMutation({
    mutationFn: (id: number) => systemApi.disableUser(id),
    onSuccess: () => {
      message.success('已停用')
      invalidate()
    },
  })

  const columns: ColumnsType<UserItem> = useMemo(
    () => [
      { title: '工号', dataIndex: 'staff_no', width: 120 },
      { title: '姓名', dataIndex: 'name', width: 120 },
      {
        title: '学院',
        dataIndex: 'college_name',
        width: 160,
        render: (v: string | null) => v ?? '—',
      },
      { title: '手机', dataIndex: 'phone', width: 130, render: (v) => v ?? '—' },
      { title: '邮箱', dataIndex: 'email', width: 200, render: (v) => v ?? '—' },
      {
        title: '状态',
        dataIndex: 'status',
        width: 90,
        render: (v: string) =>
          v === 'ACTIVE' ? <Tag color="green">启用</Tag> : <Tag>停用</Tag>,
      },
      {
        title: '最后登录',
        dataIndex: 'last_login_at',
        width: 150,
        render: (v: string | null) => fmtDateTime(v),
      },
      {
        title: '操作',
        key: 'op',
        width: 140,
        render: (_, record) => (
          <Space>
            <Button type="link" size="small" onClick={() => openEdit(record)}>
              编辑
            </Button>
            {can('system:user') && record.status === 'ACTIVE' && (
              <Popconfirm
                title="确认停用该用户？"
                description="停用后该用户无法登录"
                onConfirm={() => disableMut.mutate(record.id)}
              >
                <Button type="link" size="small" danger>
                  停用
                </Button>
              </Popconfirm>
            )}
          </Space>
        ),
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [disableMut],
  )

  const openEdit = (u: UserItem) => {
    setEditing(u)
    form.setFieldsValue({
      staff_no: u.staff_no,
      name: u.name,
      college_id: u.college_id ?? undefined,
      phone: u.phone,
      email: u.email,
    })
  }

  const openCreate = () => {
    setCreating(true)
    form.resetFields()
  }

  const submit = async () => {
    const values = await form.validateFields()
    const payload: Record<string, unknown> = {
      staff_no: values.staff_no,
      name: values.name,
      college_id: values.college_id ?? null,
      phone: values.phone || null,
      email: values.email || null,
    }
    if (values.password) {
      payload.password = values.password
    }
    if (!editing) payload.status = 'ACTIVE'
    saveMut.mutate(payload)
  }

  return (
    <div className="page-container">
      <PageHeader
        title="用户管理"
        subtitle="系统用户账号；第三方登录（SSO/钉钉等）首次登录时按自动建号规则创建"
        extra={
          can('system:user') && (
            <Space>
              <Button icon={<UploadOutlined />} onClick={() => setImportOpen(true)}>
                批量导入
              </Button>
              <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
                新增用户
              </Button>
            </Space>
          )
        }
      />

      <Space style={{ marginBottom: 16 }} wrap>
        <Input
          allowClear
          prefix={<SearchOutlined />}
          placeholder="工号 / 姓名"
          style={{ width: 220 }}
          onPressEnter={(e) => {
            setKeyword((e.target as HTMLInputElement).value)
            setPage(1)
          }}
          onChange={(e) => {
            if (!e.target.value) {
              setKeyword('')
              setPage(1)
            }
          }}
        />
        <Select
          allowClear
          placeholder="学院"
          style={{ width: 180 }}
          options={(colleges ?? []).map((c) => ({ value: c.id, label: c.name }))}
          value={collegeId}
          onChange={(v) => {
            setCollegeId(v)
            setPage(1)
          }}
        />
        <Select
          allowClear
          placeholder="状态"
          style={{ width: 120 }}
          options={[
            { value: 'ACTIVE', label: '启用' },
            { value: 'DISABLED', label: '停用' },
          ]}
          value={status}
          onChange={(v) => {
            setStatus(v)
            setPage(1)
          }}
        />
      </Space>

      <DataTable<UserItem>
        rowKey="id"
        columns={columns}
        dataSource={data?.items ?? []}
        loading={isLoading}
        pagination={tablePagination({
          current: page,
          pageSize,
          total: data?.total ?? 0,
          onChange: (p, ps) => {
            setPage(p)
            setPageSize(ps)
          },
        })}
      />

      <Modal
        title={editing ? '编辑用户' : '新增用户'}
        open={creating || editing !== null}
        onOk={submit}
        confirmLoading={saveMut.isPending}
        onCancel={() => {
          setCreating(false)
          setEditing(null)
        }}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" autoComplete="off">
          <Form.Item
            label="工号"
            name="staff_no"
            rules={editing ? [] : [{ required: true, message: '请输入工号' }]}
          >
            <Input disabled={editing !== null} placeholder="如 T001 或教职工号" />
          </Form.Item>
          <Form.Item
            label="姓名"
            name="name"
            rules={[{ required: true, message: '请输入姓名' }]}
          >
            <Input />
          </Form.Item>
          <Form.Item label="学院" name="college_id">
            <Select
              allowClear
              placeholder="选择学院"
              options={(colleges ?? []).map((c) => ({ value: c.id, label: c.name }))}
            />
          </Form.Item>
          <Form.Item label="手机" name="phone">
            <Input />
          </Form.Item>
          <Form.Item label="邮箱" name="email">
            <Input />
          </Form.Item>
          <Form.Item
            label="密码"
            name="password"
            extra={editing ? '留空表示不修改密码' : '不填则该账号只能通过第三方登录'}
          >
            <Input.Password
              placeholder={editing ? '留空表示不修改密码' : '至少 6 位，可留空'}
              autoComplete="new-password"
            />
          </Form.Item>
        </Form>
      </Modal>

      <ImportModal
        open={importOpen}
        kind="user"
        onClose={() => setImportOpen(false)}
        onSuccess={() => {
          qc.invalidateQueries({ queryKey: ['users'] })
          qc.invalidateQueries({ queryKey: ['colleges'] })
        }}
      />
    </div>
  )
}
