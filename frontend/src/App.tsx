import { Link, Navigate, Route, Routes } from 'react-router-dom'
import { Button, Result, Spin } from 'antd'

import AppLayout from '@/layouts/AppLayout'
import LoginPage from '@/pages/Login'
import OidcCallback from '@/pages/Login/OidcCallback'
import DashboardPage from '@/pages/Dashboard'
import MajorListPage from '@/pages/Project/MajorList'
import MajorDetailPage from '@/pages/Project/MajorDetail'
import SubListPage from '@/pages/Project/SubList'
import ProgressTablePage from '@/pages/Progress/Table'
import CollegeComparePage from '@/pages/Progress/CollegeCompare'
import AlertCenterPage from '@/pages/Alert/Center'
import AlertStatsPage from '@/pages/Alert/Stats'
import ReportListPage from '@/pages/Report/List'
import ReportFillPage from '@/pages/Report/Fill'
import TimelinessPage from '@/pages/Report/Timeliness'
import RiskListPage from '@/pages/Risk/List'
import UserPage from '@/pages/System/User'
import OrgPage from '@/pages/System/Org'
import RolePage from '@/pages/System/Role'
import ConfigPage from '@/pages/System/Config'
import HolidayPage from '@/pages/System/Holiday'
import AuditPage from '@/pages/System/Audit'
import NotifyLogPage from '@/pages/System/NotifyLog'
import StoragePage from '@/pages/System/Storage'
import AuthProviderPage from '@/pages/System/AuthProvider'
import NotFound from '@/pages/NotFound'

import { useAuthStore, useCanAny } from '@/stores/auth'

function RequireAuth({ children }: { children: React.ReactNode }) {
  const token = useAuthStore((s) => s.token)
  if (!token) {
    return <Navigate to="/login" replace />
  }
  return <>{children}</>
}

/**
 * 页面级权限守卫
 *
 * 之前路由只判"有没有 token"，菜单按权限过滤但路由没有 —— 直接输 URL
 * 能进无权限页面，页面渲染后发一串 403 请求，表现为白屏 + 错误提示。
 * 这里与左侧菜单用同一套权限码，口径保持一致。
 */
function RequirePerm({ any, children }: { any: string[]; children: React.ReactNode }) {
  const user = useAuthStore((s) => s.user)
  const canAny = useCanAny()

  // /auth/me 还没回来时先等，避免刚刷新页面就误判成无权限
  if (!user) {
    return (
      <div style={{ padding: '120px 0', textAlign: 'center' }}>
        <Spin tip="正在加载权限…">
          <div style={{ height: 40 }} />
        </Spin>
      </div>
    )
  }

  if (!canAny(...any)) {
    return (
      <Result
        status="403"
        title="无权访问"
        subTitle="当前账号没有该页面的权限，如需开通请联系系统管理员。"
        extra={
          <Link to="/dashboard">
            <Button type="primary">回到我的督办</Button>
          </Link>
        }
      />
    )
  }

  return <>{children}</>
}

/** 语法糖：element + 允许访问的权限码（任一命中即可） */
function guard(element: React.ReactNode, ...codes: string[]) {
  return <RequirePerm any={codes}>{element}</RequirePerm>
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/login/oidc/complete" element={<OidcCallback />} />

      <Route
        path="/"
        element={
          <RequireAuth>
            <AppLayout />
          </RequireAuth>
        }
      >
        <Route index element={<Navigate to="/dashboard" replace />} />
        <Route path="dashboard" element={<DashboardPage />} />

        <Route
          path="project/majors"
          element={guard(<MajorListPage />, 'project:major:view')}
        />
        <Route
          path="project/majors/:id"
          element={guard(<MajorDetailPage />, 'project:major:view')}
        />
        <Route path="project/subs" element={guard(<SubListPage />, 'project:sub:view')} />

        <Route
          path="progress/table"
          element={guard(<ProgressTablePage />, 'progress:table')}
        />
        <Route
          path="progress/college-compare"
          element={guard(<CollegeComparePage />, 'progress:table')}
        />

        <Route path="alert/center" element={guard(<AlertCenterPage />, 'alert:list')} />
        <Route path="alert/stats" element={guard(<AlertStatsPage />, 'alert:list')} />

        <Route
          path="report/list"
          element={guard(<ReportListPage />, 'report', 'report:fill', 'report:void')}
        />
        <Route
          path="report/fill"
          element={guard(<ReportFillPage />, 'report:fill')}
        />
        <Route
          path="report/timeliness"
          element={guard(<TimelinessPage />, 'report', 'report:fill', 'report:void')}
        />

        <Route path="risk/list" element={guard(<RiskListPage />, 'risk:list')} />

        <Route path="system/users" element={guard(<UserPage />, 'system:user')} />
        <Route path="system/org" element={guard(<OrgPage />, 'system:org')} />
        <Route path="system/roles" element={guard(<RolePage />, 'system:role')} />
        <Route path="system/configs" element={guard(<ConfigPage />, 'system:config')} />
        <Route path="system/holidays" element={guard(<HolidayPage />, 'system:holiday')} />
        <Route path="system/audit" element={guard(<AuditPage />, 'system:audit')} />
        <Route
          path="system/notify-logs"
          element={guard(<NotifyLogPage />, 'system:audit')}
        />
        <Route path="system/storage" element={guard(<StoragePage />, 'system:config')} />
        <Route
          path="system/auth-providers"
          element={guard(<AuthProviderPage />, 'system:role')}
        />
      </Route>

      <Route path="*" element={<NotFound />} />
    </Routes>
  )
}
