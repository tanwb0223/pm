import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Alert, Button, Divider, Form, Input, Space, Typography } from 'antd'
import { LockOutlined, ThunderboltFilled, UserOutlined } from '@ant-design/icons'

import { authApi } from '@/api'
import { useAuthStore } from '@/stores/auth'
import { clearQueryCache, queryClient } from '@/lib/queryClient'
import type { AuthProviderInfo } from '@/types'

const { Text } = Typography

interface LoginForm {
  username: string
  password: string
}

/** 登录页：账号密码 + 第三方跳转登录 */
export default function LoginPage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const setToken = useAuthStore((s) => s.setToken)
  const setUser = useAuthStore((s) => s.setUser)

  const [form] = Form.useForm<LoginForm>()
  const [submitting, setSubmitting] = useState(false)
  const [urlError, setUrlError] = useState<string | null>(null)

  const redirect = searchParams.get('redirect') || '/dashboard'
  const errorParam = searchParams.get('error')

  useEffect(() => {
    if (errorParam) {
      setUrlError(
        errorParam === 'access_denied'
          ? '您取消了授权，登录未完成'
          : `登录失败：${decodeURIComponent(errorParam)}`,
      )
    }
  }, [errorParam])

  // 可用登录方式
  const { data: providers } = useQuery({
    queryKey: ['auth-providers'],
    queryFn: authApi.providers,
    staleTime: 5 * 60 * 1000,
  })

  const redirectProviders = (providers ?? []).filter((p) => p.flow === 'redirect')

  const handlePasswordLogin = async (values: LoginForm) => {
    setSubmitting(true)
    try {
      const result = await authApi.login(values.username.trim(), values.password)
      // 换账号：先清掉上一个账号的全部查询缓存，再写新 token
      clearQueryCache()
      setToken(result.access_token)
      const me = await authApi.me()
      setUser(me)
      // 预热 ['me'] 缓存，避免 AppLayout 挂载时又打一次接口
      queryClient.setQueryData(['me', useAuthStore.getState().sessionKey], me)
      navigate(redirect, { replace: true })
    } catch {
      // 错误提示已由拦截器统一弹出
    } finally {
      setSubmitting(false)
    }
  }

  const handleProviderLogin = async (p: AuthProviderInfo) => {
    try {
      const { authorization_url } = await authApi.startLogin(p.kind, redirect)
      window.location.href = authorization_url
    } catch {
      // 错误提示已由拦截器统一弹出
    }
  }

  return (
    <div className="login-wrapper">
      <div className="login-card">
        <div className="login-logo">
          <ThunderboltFilled />
        </div>
        <h1 className="login-title">产教融合项目管理系统</h1>
        <p className="login-sub">产教融合项目管理平台</p>

        {urlError && (
          <Alert
            type="error"
            showIcon
            message={urlError}
            style={{ marginBottom: 16 }}
            closable
            onClose={() => setUrlError(null)}
          />
        )}

        <Form form={form} layout="vertical" onFinish={handlePasswordLogin} size="large">
          <Form.Item
            name="username"
            rules={[{ required: true, message: '请输入工号' }]}
          >
            <Input prefix={<UserOutlined />} placeholder="工号" autoComplete="username" />
          </Form.Item>

          <Form.Item
            name="password"
            rules={[{ required: true, message: '请输入密码' }]}
          >
            <Input.Password
              prefix={<LockOutlined />}
              placeholder="密码"
              autoComplete="current-password"
            />
          </Form.Item>

          <Form.Item style={{ marginBottom: 8 }}>
            <Button type="primary" htmlType="submit" block loading={submitting}>
              登录
            </Button>
          </Form.Item>
        </Form>

        {redirectProviders.length > 0 && (
          <>
            <Divider plain style={{ margin: '8px 0 16px' }}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                或使用以下方式登录
              </Text>
            </Divider>
            <Space direction="vertical" style={{ width: '100%' }} size={8}>
              {redirectProviders.map((p) => (
                <Button
                  key={p.kind}
                  block
                  onClick={() => handleProviderLogin(p)}
                >
                  {p.display_name || p.kind}
                </Button>
              ))}
            </Space>
          </>
        )}

        <div className="login-footer">建议使用 Chrome / Edge 浏览器访问本系统</div>
      </div>
    </div>
  )
}
