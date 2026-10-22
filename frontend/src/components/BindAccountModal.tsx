import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Modal, Space, Table, Tag, Tooltip, message } from 'antd'
import { DisconnectOutlined, LinkOutlined } from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'

import { authAdminApi, authApi } from '@/api'

interface Props {
  open: boolean
  onClose: () => void
}

interface IdentityRow {
  provider: string
  display_name: string
  subject: string
  last_login_at: string | null
}

/**
 * 账号绑定弹窗
 *
 * 让已登录用户把第三方身份（钉钉/企业微信/飞书/CAS/SSO 等）绑到当前账号，
 * 之后可直接用第三方登录，不必每次输密码。
 *
 * 后端接口本来就有（/auth/me/identities、/auth/bind/{kind}/start），
 * 但此前前端没有任何入口，等于该能力对用户不可见。
 */
export default function BindAccountModal({ open, onClose }: Props) {
  const qc = useQueryClient()

  // 已绑定列表
  const { data: identities, isLoading } = useQuery({
    queryKey: ['my-identities'],
    queryFn: authApi.myIdentities,
    enabled: open,
  })

  // 可用于绑定的认证方式（已启用且支持跳转的）
  const { data: providers } = useQuery({
    queryKey: ['auth-providers'],
    queryFn: authApi.providers,
    enabled: open,
    staleTime: 5 * 60 * 1000,
  })

  const [binding, setBinding] = useState<string | null>(null)

  const bindMut = useMutation({
    mutationFn: (kind: string) => authApi.startBind(kind),
    onMutate: (kind) => setBinding(kind),
    onSuccess: (r) => {
      // 跳转到第三方授权页，回来后会落回首页
      window.location.href = r.authorization_url
    },
    onSettled: () => setBinding(null),
  })

  const bound = new Set((identities ?? []).map((i) => i.provider))
  // /auth/providers 只返回已启用的方式，这里只需筛支持跳转且尚未绑定的
  const bindable = (providers ?? []).filter(
    (p) => p.flow === 'redirect' && !bound.has(p.kind),
  )

  const columns: ColumnsType<IdentityRow> = [
    {
      title: '登录方式',
      dataIndex: 'display_name',
      render: (v: string) => <Tag color="blue">{v}</Tag>,
    },
    { title: '平台账号', dataIndex: 'subject', ellipsis: true },
    {
      title: '最近登录',
      dataIndex: 'last_login_at',
      width: 160,
      render: (v: string | null) => (v ? new Date(v).toLocaleString('zh-CN') : '—'),
    },
  ]

  return (
    <Modal
      open={open}
      title="账号绑定"
      onCancel={onClose}
      footer={<Button onClick={onClose}>关闭</Button>}
      width={640}
      destroyOnHidden
    >
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Alert
          type="info"
          showIcon
          message="绑定后可用第三方身份直接登录"
          description="绑定的是当前账号，不会新建账号；解绑后该方式将无法登录本账号。"
        />

        <div>
          <div style={{ fontWeight: 600, marginBottom: 8 }}>已绑定</div>
          <Table<IdentityRow>
            rowKey="provider"
            size="small"
            loading={isLoading}
            columns={columns}
            dataSource={identities ?? []}
            pagination={false}
            locale={{ emptyText: '暂未绑定任何第三方账号' }}
          />
        </div>

        <div>
          <div style={{ fontWeight: 600, marginBottom: 8 }}>可绑定</div>
          {bindable.length === 0 ? (
            <div style={{ color: '#9ca3af', fontSize: 13 }}>
              没有更多可绑定的方式（需管理员先在「认证方式」中启用）
            </div>
          ) : (
            <Space wrap>
              {bindable.map((p) => (
                <Tooltip key={p.kind} title={`绑定 ${p.display_name}`}>
                  <Button
                    icon={<LinkOutlined />}
                    loading={binding === p.kind}
                    onClick={() => bindMut.mutate(p.kind)}
                  >
                    {p.display_name}
                  </Button>
                </Tooltip>
              ))}
            </Space>
          )}
        </div>

        <div style={{ color: '#9ca3af', fontSize: 12 }}>
          <DisconnectOutlined /> 如需解绑，请联系系统管理员（解绑属敏感操作）
        </div>
      </Space>
    </Modal>
  )
}
