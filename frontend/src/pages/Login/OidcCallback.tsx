import { useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Button, Result, Spin } from 'antd'

import { authApi } from '@/api'
import { useAuthStore } from '@/stores/auth'
import { clearQueryCache, queryClient } from '@/lib/queryClient'

/**
 * 第三方认证回调落地页
 *
 * 两种场景共用这个页面（后端 callback 302 过来）：
 * 1. 登录：带 `login_code`，换成 JWT 后进系统
 * 2. 账号绑定：带 `login_code` + `bind=1`，此时用户已经登录，
 *    只需要确认绑定结果，不能覆盖当前登录态
 *
 * 背景：早期后端 callback 直接 `return ok(data)` 返回 JSON，
 * 浏览器授权完停在一屏 JSON 上，前端这个落地页永远没人跳进来 ——
 * 7 种跳转协议（OIDC/OAuth2/CAS/SAML2/飞书/钉钉/企微）全部不可用。
 * 后端改成 302 之后，这里才是真正的「最后一公里」。
 */
export default function OidcCallback() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const setToken = useAuthStore((s) => s.setToken)
  const setUser = useAuthStore((s) => s.setUser)
  const currentToken = useAuthStore((s) => s.token)

  const [error, setError] = useState<string | null>(null)
  const [bindDone, setBindDone] = useState(false)
  const calledRef = useRef(false)

  const loginCode = searchParams.get('login_code')
  const redirectAfter = searchParams.get('redirect_after') || '/dashboard'
  const errorParam = searchParams.get('error')
  const isBind = searchParams.get('bind') === '1' || searchParams.get('bind') === 'true'

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

    // 绑定场景：当前已登录，只是把第三方身份挂到本账号上，
    // 换出来的 token 不采用，否则会把当前登录态覆盖掉。
    if (isBind && currentToken) {
      authApi
        .exchangeToken(loginCode)
        .then(() => setBindDone(true))
        .catch((e: unknown) => setError(e instanceof Error ? e.message : '绑定失败'))
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
  }, [loginCode, errorParam, redirectAfter, navigate, setToken, setUser, isBind, currentToken])

  if (bindDone) {
    return (
      <div className="login-wrapper">
        <Result
          status="success"
          title="绑定成功"
          subTitle="第三方账号已与当前账号绑定，下次可直接用它登录"
          extra={
            <Button type="primary" onClick={() => navigate('/', { replace: true })}>
              返回系统
            </Button>
          }
        />
      </div>
    )
  }

  if (error) {
    return (
      <div className="login-wrapper">
        <Result
          status="error"
          title={isBind ? '绑定失败' : '登录失败'}
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
      <Spin size="large" tip={isBind ? '正在完成绑定…' : '正在完成登录…'}>
        <div style={{ minHeight: 120, minWidth: 240 }} />
      </Spin>
    </div>
  )
}
