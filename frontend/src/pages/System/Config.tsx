import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, App, Button, Input, InputNumber, Modal, Result, Space, Table, Tag } from 'antd'
import { SearchOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'

import PageHeader from '@/components/PageHeader'
import { systemApi } from '@/api'
import { useAuthStore, useCan } from '@/stores/auth'

interface ConfigRow {
  key: string
  value: string
  value_type: string
  remark: string | null
}

/** 系统参数配置（守卫在外层，避免 Hook 数量变化） */
export default function ConfigPage() {
  const can = useCan()
  if (!can('system:config')) {
    return (
      <div className="page-container">
        <Result status="403" title="无权限" subTitle="需要 system:config 权限才能查看系统参数" />
      </div>
    )
  }
  return <ConfigPageInner />
}

function ConfigPageInner() {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const canWrite = true

  const [search, setSearch] = useState('')
  const [editing, setEditing] = useState<ConfigRow | null>(null)
  const [value, setValue] = useState<string | number>('')

  const { data, isLoading } = useQuery({ queryKey: ['configs'], queryFn: systemApi.configs })

  const rows = useMemo(() => {
    const list = data ?? []
    if (!search) return list
    const kw = search.toLowerCase()
    return list.filter(
      (r) => r.key.toLowerCase().includes(kw) || (r.remark ?? '').toLowerCase().includes(kw),
    )
  }, [data, search])

  const saveMut = useMutation({
    mutationFn: () => systemApi.setConfig(editing!.key, String(value)),
    onSuccess: () => {
      message.success('已保存并即时生效')
      setEditing(null)
      qc.invalidateQueries({ queryKey: ['configs'] })
    },
  })

  const openEdit = (row: ConfigRow) => {
    setEditing(row)
    setValue(row.value_type === 'NUMBER' || row.value_type === 'INT' ? Number(row.value) : row.value)
  }

  const columns: ColumnsType<ConfigRow> = [
    {
      title: '参数键',
      dataIndex: 'key',
      width: 300,
      render: (v: string) => <code style={{ fontSize: 13 }}>{v}</code>,
    },
    {
      title: '当前值',
      dataIndex: 'value',
      width: 180,
      render: (v: string) => <span style={{ fontWeight: 600 }}>{v}</span>,
    },
    {
      title: '类型',
      dataIndex: 'value_type',
      width: 100,
      render: (v: string) => <Tag>{v}</Tag>,
    },
    { title: '说明', dataIndex: 'remark', render: (v: string | null) => v ?? '—' },
    {
      title: '操作',
      key: 'op',
      width: 90,
      render: (_, row) =>
        canWrite && (
          <Button type="link" size="small" onClick={() => openEdit(row)}>
            修改
          </Button>
        ),
    },
  ]

  return (
    <div className="page-container">
      <PageHeader title="系统参数配置" />

      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="参数修改后立即生效，影响预警阈值、报表时限、汇总口径等"
        description="常见参数：alert.AL-01.deviation（滞后阈值）、alert.AL-03.days（停滞天数）、report.correct_days（报表更正期）、report.daily.enabled（是否启用日报）"
      />

      <Space style={{ marginBottom: 16 }}>
        <Input
          allowClear
          prefix={<SearchOutlined />}
          placeholder="按参数键或说明过滤"
          style={{ width: 300 }}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <span style={{ color: '#8c8c8c', fontSize: 13 }}>共 {rows.length} 项</span>
      </Space>

      <Table rowKey="key" columns={columns} dataSource={rows} loading={isLoading} pagination={false} size="middle" />

      <Modal
        title="修改参数"
        open={editing !== null}
        onOk={() => saveMut.mutate()}
        confirmLoading={saveMut.isPending}
        onCancel={() => setEditing(null)}
        destroyOnHidden
      >
        {editing && (
          <Space direction="vertical" style={{ width: '100%' }} size={16}>
            <div>
              <div style={{ color: '#8c8c8c', marginBottom: 4 }}>参数键</div>
              <code style={{ fontSize: 14 }}>{editing.key}</code>
            </div>
            <div>
              <div style={{ color: '#8c8c8c', marginBottom: 4 }}>参数值</div>
              {editing.value_type === 'NUMBER' || editing.value_type === 'INT' ? (
                <InputNumber
                  style={{ width: '100%' }}
                  value={typeof value === 'number' ? value : Number(value)}
                  onChange={(v) => setValue(v ?? '')}
                />
              ) : editing.value === 'true' || editing.value === 'false' ? (
                <Space>
                  <Button
                    type={value === 'true' ? 'primary' : 'default'}
                    onClick={() => setValue('true')}
                  >
                    true
                  </Button>
                  <Button
                    type={value === 'false' ? 'primary' : 'default'}
                    onClick={() => setValue('false')}
                  >
                    false
                  </Button>
                </Space>
              ) : (
                <Input value={String(value)} onChange={(e) => setValue(e.target.value)} />
              )}
            </div>
            {editing.remark && (
              <div>
                <div style={{ color: '#8c8c8c', marginBottom: 4 }}>说明</div>
                <div>{editing.remark}</div>
              </div>
            )}
          </Space>
        )}
      </Modal>
    </div>
  )
}
