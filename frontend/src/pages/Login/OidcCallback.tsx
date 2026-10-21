import { useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Button, Result, Spin } from 'antd'

import { authApi } from '@/api'
import { useAuthStore } from '@/stores/auth'
import { clearQueryCache, queryClient } from '@/lib/queryClient'

/** 第三方登录回调落地页：拿一次性 login_code 换 JWT */
export default function OidcCallback() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const setToken = useAuthStore((s) => s.setToken)
  const setUser = useAuthStore((s) => s.setUser)

  const [error, setError] = useState<string | null>(null)
  const calledRef = useRef(false)

  const loginCode = searchParams.get('login_code')
  const redirectAfter = searchParams.get('redirect_after') || '/dashboard'
  const errorParam = searchParams.get('error')

  useEffect(() => {
    if (calledRef.current) return
    calledRef.current = true

    if (errorParam) {
      setError(errorParam === 'access_denied' ? '您取消了授权' : decodeURIComponent(errorParam))
      return
    }
    if (!loginCode) {
      setError('缺少登录凭据（login_code），请从登录页重新发起')
      return
    }

    authApi
      .exchangeToken(loginCode)
      .then(async (result) => {
        // 换账号：先清掉上一个账号的查询缓存
        clearQueryCache()
        setToken(result.access_token)
        const me = await authApi.me()
        setUser(me)
        queryClient.setQueryData(['me', useAuthStore.getState().sessionKey], me)
        navigate(redirectAfter, { replace: true })
      })
      .catch((e: unknown) => {
        const msg = e instanceof Error ? e.message : '换取登录凭据失败'
        setError(msg)
      })
  }, [loginCode, errorParam, redirectAfter, navigate, setToken, setUser])

  if (error) {
    return (
      <div className="login-wrapper">
        <Result
          status="error"
          title="登录失败"
          subTitle={error}
          extra={
            <Button type="primary" onClick={() => navigate('/login', { replace: true })}>
              返回登录页
            </Button>
          }
        />
      </div>
    )
  }

  return (
    <div className="login-wrapper">
      <Spin size="large" tip="正在完成登录…">
        <div style={{ minHeight: 120, minWidth: 240 }} />
      </Spin>
    </div>
  )
}
