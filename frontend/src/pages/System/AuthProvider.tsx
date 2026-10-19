import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  App,
  Button,
  Col,
  Drawer,
  Input,
  Row,
  Select,
  Space,
  Switch,
  Tag,
  Typography,
} from 'antd'
import {
  BankOutlined,
  BellOutlined,
  CloudOutlined,
  KeyOutlined,
  LinkOutlined,
  MailOutlined,
  SafetyCertificateOutlined,
  SecurityScanOutlined,
  WindowsOutlined,
} from '@ant-design/icons'

import PageHeader from '@/components/PageHeader'
import { authAdminApi, systemApi } from '@/api'
import { useAuthStore } from '@/stores/auth'

interface ProviderRow {
  kind: string
  enabled: boolean
  display_name: string
  issuer: string
  client_id: string
  has_client_secret: boolean
  scopes: string
  server_url: string
  extra: Record<string, unknown>
  auto_create_user: boolean
  default_role_id: number | null
  redirect_uri: string
  configured: boolean
}

const KIND_META: Record<string, { icon: React.ReactNode; desc: string }> = {
  password: { icon: <KeyOutlined />, desc: '本地账号密码登录' },
  oidc: { icon: <SecurityScanOutlined />, desc: 'OpenID Connect，支持 PKCE' },
  oauth2: { icon: <LinkOutlined />, desc: '通用 OAuth2 授权码' },
  cas: { icon: <SafetyCertificateOutlined />, desc: 'CAS 2.0/3.0，高校常用' },
  saml2: { icon: <MailOutlined />, desc: 'SAML 2.0 联合认证' },
  ldap: { icon: <WindowsOutlined />, desc: 'LDAP / AD 域账号' },
  feishu: { icon: <BellOutlined />, desc: '飞书扫码登录' },
  dingtalk: { icon: <CloudOutlined />, desc: '钉钉扫码登录' },
  wecom: { icon: <BankOutlined />, desc: '企业微信登录' },
}

/** 认证方式管理（9 种协议） */
export default function AuthProviderPage() {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const hasPerm = useAuthStore((s) => s.hasPerm)
  const canWrite = hasPerm('system:role')

  const [editingKind, setEditingKind] = useState<string | null>(null)

  const { data, isLoading } = useQuery({ queryKey: ['auth-providers'], queryFn: authAdminApi.providers })
  const { data: roles } = useQuery({ queryKey: ['roles'], queryFn: systemApi.roles })

  const providers = data ?? []
  const editing = providers.find((p) => p.kind === editingKind) ?? null

  const toggleMut = useMutation({
    mutationFn: ({ kind, enabled }: { kind: string; enabled: boolean }) =>
      authAdminApi.update(kind, { enabled }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['auth-providers'] })
    },
    onError: () => {
      qc.invalidateQueries({ queryKey: ['auth-providers'] })
    },
  })

  const testMut = useMutation({
    mutationFn: (kind: string) => authAdminApi.test(kind),
    onSuccess: (r) => {
      if (r.ok) message.success(r.detail || '连接成功')
      else message.error(r.detail || '连接失败')
    },
  })

  return (
    <div className="page-container">
      <PageHeader
        title="认证方式"
        subtitle="支持 9 种登录协议；启用前请先完成配置并通过连通性测试；密钥加密存储"
      />

      <Row gutter={[16, 16]}>
        {providers.map((p) => {
          const meta = KIND_META[p.kind] ?? { icon: <KeyOutlined />, desc: '' }
          return (
            <Col key={p.kind} xs={24} md={8}>
              <div
                style={{
                  border: '1px solid #e8eef4',
                  borderRadius: 8,
                  padding: 16,
                  background: '#fff',
                }}
              >
                <Space style={{ justifyContent: 'space-between', width: '100%' }}>
                  <Space size={8}>
                    <span style={{ fontSize: 20, color: '#1e3a5f' }}>{meta.icon}</span>
                    <div>
                      <div style={{ fontWeight: 600 }}>{p.display_name || p.kind}</div>
                      <code style={{ fontSize: 12, color: '#8c8c8c' }}>{p.kind}</code>
                    </div>
                  </Space>
                  {canWrite && (
                    <Switch
                      checked={p.enabled}
                      checkedChildren="启用"
                      unCheckedChildren="停用"
                      onChange={(v) => toggleMut.mutate({ kind: p.kind, enabled: v })}
                    />
                  )}
                </Space>

                <div style={{ color: '#8c8c8c', fontSize: 13, margin: '10px 0' }}>{meta.desc}</div>

                <Space wrap size={4} style={{ marginBottom: 12 }}>
                  {p.configured ? (
                    <Tag color="green">已配置</Tag>
                  ) : (
                    <Tag>未配置</Tag>
                  )}
                  {p.enabled && <Tag color="processing">已启用</Tag>}
                </Space>

                <Space>
                  <Button size="small" onClick={() => setEditingKind(p.kind)}>
                    配置
                  </Button>
                  <Button
                    size="small"
                    loading={testMut.isPending && testMut.variables === p.kind}
                    onClick={() => testMut.mutate(p.kind)}
                  >
                    测试连接
                  </Button>
                </Space>
              </div>
            </Col>
          )
        })}
      </Row>

      <ConfigDrawer
        provider={editing}
        open={editingKind !== null}
        roles={roles ?? []}
        canWrite={canWrite}
        onClose={() => setEditingKind(null)}
      />
    </div>
  )
}

// ================================================================ 配置抽屉
function ConfigDrawer({
  provider,
  open,
  roles,
  canWrite,
  onClose,
}: {
  provider: ProviderRow | null
  open: boolean
  roles: { id: number; name: string }[]
  canWrite: boolean
  onClose: () => void
}) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const [displayName, setDisplayName] = useState('')
  const [clientId, setClientId] = useState('')
  const [secret, setSecret] = useState('')
  const [issuer, setIssuer] = useState('')
  const [scopes, setScopes] = useState('')
  const [serverUrl, setServerUrl] = useState('')
  const [extra, setExtra] = useState<Record<string, string>>({})
  const [autoCreate, setAutoCreate] = useState(true)
  const [defaultRoleId, setDefaultRoleId] = useState<number | undefined>()

  const kind = provider?.kind ?? ''
  const lastKind = useMemo(() => kind, [kind])

  // 打开时按当前 provider 初始化
  useQuery({
    queryKey: ['provider-init', provider?.kind],
    queryFn: () => {
      if (provider) {
        setDisplayName(provider.display_name ?? '')
        setClientId(provider.client_id ?? '')
        setSecret('')
        setIssuer(provider.issuer ?? '')
        setScopes(provider.scopes ?? '')
        setServerUrl(provider.server_url ?? '')
        setExtra(
          Object.fromEntries(
            Object.entries(provider.extra ?? {}).map(([k, v]) => [k, String(v ?? '')]),
          ),
        )
        setAutoCreate(provider.auto_create_user)
        setDefaultRoleId(provider.default_role_id ?? undefined)
      }
      return null
    },
    enabled: open && provider !== null,
  })

  const saveMut = useMutation({
    mutationFn: () => {
      const payload: Record<string, unknown> = {
        display_name: displayName,
        client_id: clientId,
        auto_create_user: autoCreate,
        default_role_id: defaultRoleId ?? null,
      }
      if (secret) payload.client_secret = secret
      if (kind === 'oidc') {
        payload.issuer = issuer
        payload.scopes = scopes
      } else if (kind === 'cas' || kind === 'ldap') {
        payload.server_url = serverUrl
        payload.extra = extra
      } else if (kind === 'oauth2' || kind === 'saml2' || kind === 'wecom') {
        payload.extra = extra
        if (kind === 'oauth2') payload.scopes = scopes
      } else {
        payload.extra = extra
      }
      return authAdminApi.update(kind, payload)
    },
    onSuccess: () => {
      message.success('已保存')
      setSecret('')
      qc.invalidateQueries({ queryKey: ['auth-providers'] })
      onClose()
    },
  })

  if (!provider) return null

  const E = ({ k, label, placeholder }: { k: string; label: string; placeholder?: string }) => (
    <div style={{ marginBottom: 12 }}>
      <div style={{ color: '#8c8c8c', marginBottom: 4 }}>{label}</div>
      <Input
        value={extra[k] ?? ''}
        placeholder={placeholder}
        onChange={(e) => setExtra({ ...extra, [k]: e.target.value })}
      />
    </div>
  )

  return (
    <Drawer
      title={`配置 ${provider.display_name || kind}`}
      width={520}
      open={open}
      onClose={onClose}
      extra={
        canWrite && (
          <Button type="primary" loading={saveMut.isPending} onClick={() => saveMut.mutate()}>
            保存
          </Button>
        )
      }
      destroyOnHidden
    >
      <div style={{ marginBottom: 16 }}>
        <div style={{ color: '#8c8c8c', marginBottom: 4 }}>显示名称</div>
        <Input value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
      </div>

      {(kind === 'oidc' || kind === 'oauth2' || kind === 'feishu' || kind === 'dingtalk' || kind === 'wecom') && (
        <div style={{ marginBottom: 12 }}>
          <div style={{ color: '#8c8c8c', marginBottom: 4 }}>
            Client ID{kind === 'wecom' ? '（企业 ID corp_id）' : ''}
          </div>
          <Input value={clientId} onChange={(e) => setClientId(e.target.value)} />
        </div>
      )}

      {(kind === 'feishu' || kind === 'dingtalk' || kind === 'wecom' || kind === 'oidc' || kind === 'oauth2') && (
        <div style={{ marginBottom: 12 }}>
          <div style={{ color: '#8c8c8c', marginBottom: 4 }}>Client Secret</div>
          <Input.Password
            value={secret}
            placeholder={provider.has_client_secret ? '已保存，留空不修改' : '请输入'}
            autoComplete="new-password"
            onChange={(e) => setSecret(e.target.value)}
          />
        </div>
      )}

      {kind === 'oidc' && (
        <>
          <div style={{ marginBottom: 12 }}>
            <div style={{ color: '#8c8c8c', marginBottom: 4 }}>
              Issuer（如 https://sso.school.edu.cn，自动读取 /.well-known/openid-configuration）
            </div>
            <Input value={issuer} onChange={(e) => setIssuer(e.target.value)} />
          </div>
          <div style={{ marginBottom: 12 }}>
            <div style={{ color: '#8c8c8c', marginBottom: 4 }}>Scopes</div>
            <Input value={scopes} placeholder="openid profile email" onChange={(e) => setScopes(e.target.value)} />
          </div>
        </>
      )}

      {kind === 'oauth2' && (
        <>
          <E k="authorize_url" label="授权地址 authorize_url" />
          <E k="token_url" label="Token 地址 token_url" />
          <E k="userinfo_url" label="用户信息地址 userinfo_url" />
          <E k="user_id_field" label="用户标识字段（可选，默认 sub）" />
          <div style={{ marginBottom: 12 }}>
            <div style={{ color: '#8c8c8c', marginBottom: 4 }}>Scopes</div>
            <Input value={scopes} onChange={(e) => setScopes(e.target.value)} />
          </div>
        </>
      )}

      {(kind === 'cas' || kind === 'ldap') && (
        <div style={{ marginBottom: 12 }}>
          <div style={{ color: '#8c8c8c', marginBottom: 4 }}>
            {kind === 'cas' ? 'CAS 服务地址（如 https://sso.school.edu.cn/cas）' : 'LDAP 地址（如 ldaps://ad.school.edu.cn）'}
          </div>
          <Input value={serverUrl} onChange={(e) => setServerUrl(e.target.value)} />
        </div>
      )}

      {kind === 'cas' && <E k="cas_version" label="CAS 版本（2.0 / 3.0，默认 2.0）" />}

      {kind === 'saml2' && (
        <>
          <E k="idp_sso_url" label="IdP 单点登录地址 idp_sso_url" />
          <E k="sp_entity_id" label="SP 实体 ID sp_entity_id" />
        </>
      )}

      {kind === 'ldap' && (
        <>
          <E k="base_dn" label="Base DN（如 dc=school,dc=edu,dc=cn）" />
          <E k="user_filter" label="用户过滤器（支持 {username} 占位，如 (sAMAccountName={username})）" />
          <E k="bind_dn" label="绑定 DN（可选，用于先搜索后验证）" />
          <E k="bind_password" label="绑定密码（可选）" />
        </>
      )}

      {kind === 'wecom' && <E k="agent_id" label="应用 ID agent_id" />}

      {/* 用户字段映射（全部协议通用） */}
      <Typography.Title level={5} style={{ marginTop: 20 }}>
        用户字段映射（可选）
      </Typography.Title>
      <E k="claim_username" label="工号字段（默认按 staff_no → employee_id → preferred_username 兜底）" />
      <E k="claim_name" label="姓名字段" />
      <E k="claim_email" label="邮箱字段" />

      <Typography.Title level={5} style={{ marginTop: 20 }}>
        自动建号
      </Typography.Title>
      <Space direction="vertical" style={{ width: '100%' }} size={12}>
        <Space>
          <Switch checked={autoCreate} onChange={setAutoCreate} />
          <span>第三方首次登录时自动创建账号</span>
        </Space>
        <Select
          allowClear
          placeholder="默认角色（自动建号时分配）"
          style={{ width: '100%' }}
          value={defaultRoleId}
          options={roles.map((r) => ({ value: r.id, label: r.name }))}
          onChange={(v) => setDefaultRoleId(v)}
        />
      </Space>

      <Typography.Title level={5} style={{ marginTop: 20 }}>
        回调地址
      </Typography.Title>
      <Space.Compact style={{ width: '100%' }}>
        <Input value={provider.redirect_uri} readOnly />
        <Button
          onClick={() => {
            navigator.clipboard.writeText(provider.redirect_uri)
            message.success('已复制')
          }}
        >
          复制
        </Button>
      </Space.Compact>
      <div style={{ color: '#8c8c8c', fontSize: 12, marginTop: 4 }}>
        在第三方平台配置回调时使用此地址
      </div>
    </Drawer>
  )
}
