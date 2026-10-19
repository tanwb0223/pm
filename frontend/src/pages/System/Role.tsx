import { useEffect, useMemo, useState } from 'react'
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
  Tabs,
  Tag,
} from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import dayjs, { type Dayjs } from 'dayjs'
import type { ColumnsType } from 'antd/es/table'

import PageHeader from '@/components/PageHeader'
import { collegeApi, majorApi, systemApi } from '@/api'
import { useAuthStore } from '@/stores/auth'

/** 授权记录（局部类型，与 systemApi.grants 返回一致） */
interface GrantRow {
  id: number
  user_id: number
  user_name: string
  staff_no: string
  role_id: number
  role_code: string
  role_name: string
  scope_type: string
  scope_id: number | null
  valid_from: string | null
  valid_to: string | null
}

interface PermRow {
  id: number
  code: string
  name: string
  type: string
  parent_id: number | null
}

const SCOPE_LABEL: Record<string, string> = {
  ALL: '全校',
  MAJOR: '按大项目',
  COLLEGE: '按学院',
  SELF: '本人',
  CUSTOM: '自定义',
}

const PERM_TYPE_LABEL: Record<string, string> = {
  MENU: '菜单',
  PAGE: '页面',
  ACTION: '操作',
  MODULE: '模块',
}

/** 角色权限管理：角色 + 用户授权 + 权限配置三个 Tab */
export default function RolePage() {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const hasPerm = useAuthStore((s) => s.hasPerm)
  const canWrite = hasPerm('system:role')

  return (
    <div className="page-container">
      <PageHeader
        title="角色与授权"
        subtitle="7 个系统内置角色 + 最多 3 个自定义角色；授权 = 角色 + 数据范围（全校 / 大项目 / 学院 / 本人）"
      />
      <Tabs
        defaultActiveKey="roles"
        items={[
          { key: 'roles', label: '角色', children: <RoleTab canWrite={canWrite} /> },
          { key: 'grants', label: '用户授权', children: <GrantTab canWrite={canWrite} /> },
          { key: 'perms', label: '权限配置', children: <PermTab canWrite={canWrite} /> },
        ]}
      />
    </div>
  )
}

// ================================================================ Tab 1：角色
function RoleTab({ canWrite }: { canWrite: boolean }) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [form] = Form.useForm()

  const { data: roles, isLoading } = useQuery({ queryKey: ['roles'], queryFn: systemApi.roles })

  const createMut = useMutation({
    mutationFn: (values: { code: string; name: string; remark?: string }) =>
      systemApi.createRole(values),
    onSuccess: () => {
      message.success('角色已创建')
      setOpen(false)
      qc.invalidateQueries({ queryKey: ['roles'] })
    },
  })

  const columns: ColumnsType<{ id: number; code: string; name: string; is_system: boolean; permission_count: number }> =
    [
      { title: '编码', dataIndex: 'code', width: 200 },
      { title: '名称', dataIndex: 'name', width: 180 },
      {
        title: '类型',
        dataIndex: 'is_system',
        width: 110,
        render: (v: boolean) =>
          v ? <Tag color="blue">系统内置</Tag> : <Tag color="purple">自定义</Tag>,
      },
      { title: '权限数', dataIndex: 'permission_count', width: 100, align: 'center' },
    ]

  return (
    <>
      <div style={{ marginBottom: 16 }}>
        {canWrite && (
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
            新增自定义角色
          </Button>
        )}
        <span style={{ marginLeft: 12, color: '#8c8c8c', fontSize: 13 }}>
          一期自定义角色上限 3 个（不含系统内置）
        </span>
      </div>
      <Table rowKey="id" columns={columns} dataSource={roles ?? []} loading={isLoading} pagination={false} />

      <Modal
        title="新增自定义角色"
        open={open}
        onOk={async () => {
          const values = await form.validateFields()
          createMut.mutate(values)
        }}
        confirmLoading={createMut.isPending}
        onCancel={() => setOpen(false)}
        destroyOnHidden
      >
        <Form form={form} layout="vertical">
          <Form.Item
            label="角色编码"
            name="code"
            rules={[
              { required: true, message: '请输入编码' },
              { pattern: /^[A-Z][A-Z0-9_]*$/, message: '大写字母、数字、下划线' },
            ]}
          >
            <Input placeholder="如 SECOND_REVIEWER" />
          </Form.Item>
          <Form.Item
            label="角色名称"
            name="name"
            rules={[{ required: true, message: '请输入名称' }]}
          >
            <Input placeholder="如 复核员" />
          </Form.Item>
          <Form.Item label="备注" name="remark">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}

// ================================================================ Tab 2：用户授权
function GrantTab({ canWrite }: { canWrite: boolean }) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [filterUser, setFilterUser] = useState<number | undefined>()
  const [open, setOpen] = useState(false)
  const [scopeType, setScopeType] = useState<string>('SELF')
  const [form] = Form.useForm()

  const { data: usersData } = useQuery({
    queryKey: ['users-all'],
    queryFn: () => systemApi.users({ page: 1, page_size: 500 }),
  })
  const users = usersData?.items ?? []

  const { data: roles } = useQuery({ queryKey: ['roles'], queryFn: systemApi.roles })
  const { data: colleges } = useQuery({ queryKey: ['colleges'], queryFn: collegeApi.list })
  const { data: majorsData } = useQuery({
    queryKey: ['majors-brief'],
    queryFn: () => majorApi.list({ page: 1, page_size: 200 }),
  })
  const majors = majorsData?.items ?? []

  const { data: grants, isLoading } = useQuery({
    queryKey: ['grants', filterUser],
    queryFn: () => systemApi.grants(filterUser),
  })

  const removeMut = useMutation({
    mutationFn: (id: number) => systemApi.removeGrant(id),
    onSuccess: () => {
      message.success('已撤销授权')
      qc.invalidateQueries({ queryKey: ['grants'] })
    },
  })

  const addMut = useMutation({
    mutationFn: (values: Record<string, unknown>) => systemApi.addGrant(values),
    onSuccess: () => {
      message.success('授权成功')
      setOpen(false)
      form.resetFields()
      setScopeType('SELF')
      qc.invalidateQueries({ queryKey: ['grants'] })
    },
  })

  const scopeName = (row: GrantRow): string => {
    if (row.scope_type === 'COLLEGE' && row.scope_id) {
      return colleges?.find((c) => c.id === row.scope_id)?.name ?? `#${row.scope_id}`
    }
    if (row.scope_type === 'MAJOR' && row.scope_id) {
      const m = majors.find((x) => x.id === row.scope_id)
      return m ? m.name : `#${row.scope_id}`
    }
    return ''
  }

  const columns: ColumnsType<GrantRow> = [
    { title: '用户', dataIndex: 'user_name', width: 120 },
    { title: '工号', dataIndex: 'staff_no', width: 110 },
    { title: '角色', dataIndex: 'role_name', width: 160 },
    {
      title: '数据范围',
      key: 'scope',
      width: 220,
      render: (_, row) => (
        <span>
          <Tag>{SCOPE_LABEL[row.scope_type] ?? row.scope_type}</Tag>
          {(row.scope_type === 'MAJOR' || row.scope_type === 'COLLEGE') && (
            <span style={{ fontSize: 13 }}>{scopeName(row)}</span>
          )}
        </span>
      ),
    },
    {
      title: '有效期',
      key: 'valid',
      width: 200,
      render: (_, row) =>
        row.valid_from || row.valid_to
          ? `${row.valid_from ?? '…'} ~ ${row.valid_to ?? '…'}`
          : '长期',
    },
    {
      title: '操作',
      key: 'op',
      width: 90,
      render: (_, row) =>
        canWrite && (
          <Popconfirm title="确认撤销该授权？" onConfirm={() => removeMut.mutate(row.id)}>
            <Button type="link" size="small" danger>
              撤销
            </Button>
          </Popconfirm>
        ),
    },
  ]

  const userOptions = users.map((u) => ({
    value: u.id,
    label: `${u.name}（${u.staff_no}）`,
  }))

  return (
    <>
      <Space style={{ marginBottom: 16 }}>
        <Select
          showSearch
          allowClear
          placeholder="按用户过滤"
          style={{ width: 240 }}
          options={userOptions}
          optionFilterProp="label"
          value={filterUser}
          onChange={(v) => setFilterUser(v)}
        />
        {canWrite && (
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
            新增授权
          </Button>
        )}
      </Space>

      <Table rowKey="id" columns={columns} dataSource={grants ?? []} loading={isLoading} pagination={false} />

      <Modal
        title="新增授权"
        open={open}
        onOk={async () => {
          const values = await form.validateFields()
          const payload: Record<string, unknown> = {
            user_id: values.user_id,
            role_id: values.role_id,
            scope_type: values.scope_type,
          }
          if (values.scope_type === 'MAJOR' || values.scope_type === 'COLLEGE') {
            payload.scope_id = values.scope_id
          }
          if (values.valid_range?.[0]) {
            payload.valid_from = (values.valid_range[0] as Dayjs).format('YYYY-MM-DD')
            payload.valid_to = (values.valid_range[1] as Dayjs).format('YYYY-MM-DD')
          }
          addMut.mutate(payload)
        }}
        confirmLoading={addMut.isPending}
        onCancel={() => {
          setOpen(false)
          setScopeType('SELF')
        }}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" initialValues={{ scope_type: 'SELF' }}>
          <Form.Item label="用户" name="user_id" rules={[{ required: true }]}>
            <Select showSearch placeholder="搜索姓名或工号" options={userOptions} optionFilterProp="label" />
          </Form.Item>
          <Form.Item label="角色" name="role_id" rules={[{ required: true }]}>
            <Select
              placeholder="选择角色"
              options={(roles ?? []).map((r) => ({ value: r.id, label: r.name }))}
            />
          </Form.Item>
          <Form.Item label="数据范围" name="scope_type" rules={[{ required: true }]}>
            <Radio.Group
              onChange={(e) => setScopeType(e.target.value)}
              options={[
                { value: 'ALL', label: '全校' },
                { value: 'MAJOR', label: '按大项目' },
                { value: 'COLLEGE', label: '按学院' },
                { value: 'SELF', label: '本人' },
              ]}
              optionType="button"
            />
          </Form.Item>
          {scopeType === 'MAJOR' && (
            <Form.Item label="大项目" name="scope_id" rules={[{ required: true, message: '请选择大项目' }]}>
              <Select
                showSearch
                placeholder="选择大项目"
                optionFilterProp="label"
                options={majors.map((m) => ({ value: m.id, label: m.name }))}
              />
            </Form.Item>
          )}
          {scopeType === 'COLLEGE' && (
            <Form.Item label="学院" name="scope_id" rules={[{ required: true, message: '请选择学院' }]}>
              <Select
                showSearch
                placeholder="选择学院"
                optionFilterProp="label"
                options={(colleges ?? []).map((c) => ({ value: c.id, label: c.name }))}
              />
            </Form.Item>
          )}
          <Form.Item label="有效期（可空 = 长期）" name="valid_range">
            <DatePicker.RangePicker style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}

// ================================================================ Tab 3：权限配置
function PermTab({ canWrite }: { canWrite: boolean }) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [roleId, setRoleId] = useState<number | undefined>()
  const [checked, setChecked] = useState<Set<number>>(new Set())

  const { data: roles } = useQuery({ queryKey: ['roles'], queryFn: systemApi.roles })
  const selectableRoles = (roles ?? []).filter((r) => r.code !== 'SYS_ADMIN')

  const { data: perms } = useQuery({ queryKey: ['permissions'], queryFn: systemApi.permissions })

  // 选中角色后拉取其现有权限，回显勾选；切换角色时随 queryKey 变化重新拉取
  const { data: rolePerms, isLoading: isLoadingRolePerms } = useQuery({
    queryKey: ['role-perms', roleId],
    queryFn: () => systemApi.getRolePermissions(roleId!),
    enabled: roleId !== undefined,
  })

  // 回显：permission_ids 变化时重置勾选
  useEffect(() => {
    if (roleId === undefined) {
      setChecked(new Set())
      return
    }
    if (rolePerms && rolePerms.role_id === roleId) {
      setChecked(new Set(rolePerms.permission_ids))
    }
  }, [roleId, rolePerms])

  const saveMut = useMutation({
    mutationFn: () => systemApi.setRolePermissions(roleId!, Array.from(checked)),
    onSuccess: () => {
      message.success(`已保存 ${checked.size} 个权限点`)
      qc.invalidateQueries({ queryKey: ['roles'] })
      qc.invalidateQueries({ queryKey: ['role-perms', roleId] })
    },
  })

  // 按 type 分组
  const grouped = useMemo(() => {
    const map = new Map<string, PermRow[]>()
    for (const p of perms ?? []) {
      const list = map.get(p.type) ?? []
      list.push(p)
      map.set(p.type, list)
    }
    return Array.from(map.entries())
  }, [perms])

  const columns: ColumnsType<PermRow> = [
    {
      title: '',
      key: 'check',
      width: 50,
      render: (_, row) => (
        <Checkbox
          checked={checked.has(row.id)}
          disabled={!canWrite}
          onChange={(e) => {
            const next = new Set(checked)
            if (e.target.checked) next.add(row.id)
            else next.delete(row.id)
            setChecked(next)
          }}
        />
      ),
    },
    { title: '编码', dataIndex: 'code', width: 240 },
    { title: '名称', dataIndex: 'name', width: 180 },
    {
      title: '类型',
      dataIndex: 'type',
      width: 100,
      render: (v: string) => <Tag>{PERM_TYPE_LABEL[v] ?? v}</Tag>,
    },
  ]

  return (
    <>
      <Space style={{ marginBottom: 16 }}>
        <Select
          placeholder="选择角色"
          style={{ width: 240 }}
          options={selectableRoles.map((r) => ({
            value: r.id,
            label: `${r.name}${r.is_system ? '（系统内置）' : ''}`,
          }))}
          value={roleId}
          onChange={(v) => setRoleId(v)}
        />
        {canWrite && (
          <Button
            type="primary"
            disabled={!roleId}
            loading={saveMut.isPending}
            onClick={() => {
              if (!roleId) return
              const role = roles?.find((r) => r.id === roleId)
              if (role?.code === 'SYS_ADMIN') {
                message.error('系统管理员权限不可修改')
                return
              }
              saveMut.mutate()
            }}
          >
            保存权限
          </Button>
        )}
        <span style={{ color: '#8c8c8c', fontSize: 13 }}>
          提示：保存为全量覆盖；SYS_ADMIN 角色不可修改
        </span>
        {roleId !== undefined && isLoadingRolePerms && <span>加载权限中…</span>}
      </Space>

      {roleId === undefined ? (
        <div style={{ color: '#8c8c8c', padding: '24px 0', textAlign: 'center' }}>
          请先选择一个角色
        </div>
      ) : (
        grouped.map(([type, list]) => (
          <div key={type} style={{ marginBottom: 24 }}>
            <div style={{ fontWeight: 600, marginBottom: 8 }}>{PERM_TYPE_LABEL[type] ?? type}</div>
            <Table rowKey="id" columns={columns} dataSource={list} pagination={false} size="small" />
          </div>
        ))
      )}
    </>
  )
}
