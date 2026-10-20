import { Navigate, Route, Routes } from 'react-router-dom'

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
import AuthProviderPage from '@/pages/System/AuthProvider'
import NotFound from '@/pages/NotFound'

import { useAuthStore } from '@/stores/auth'

function RequireAuth({ children }: { children: React.ReactNode }) {
  const token = useAuthStore((s) => s.token)
  if (!token) {
    return <Navigate to="/login" replace />
  }
  return <>{children}</>
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

        <Route path="project/majors" element={<MajorListPage />} />
        <Route path="project/majors/:id" element={<MajorDetailPage />} />
        <Route path="project/subs" element={<SubListPage />} />

        <Route path="progress/table" element={<ProgressTablePage />} />
        <Route path="progress/college-compare" element={<CollegeComparePage />} />

        <Route path="alert/center" element={<AlertCenterPage />} />
        <Route path="alert/stats" element={<AlertStatsPage />} />

        <Route path="report/list" element={<ReportListPage />} />
        <Route path="report/fill" element={<ReportFillPage />} />
        <Route path="report/timeliness" element={<TimelinessPage />} />

        <Route path="risk/list" element={<RiskListPage />} />

        <Route path="system/users" element={<UserPage />} />
        <Route path="system/org" element={<OrgPage />} />
        <Route path="system/roles" element={<RolePage />} />
        <Route path="system/configs" element={<ConfigPage />} />
        <Route path="system/holidays" element={<HolidayPage />} />
        <Route path="system/audit" element={<AuditPage />} />
        <Route path="system/auth-providers" element={<AuthProviderPage />} />
      </Route>

      <Route path="*" element={<NotFound />} />
    </Routes>
  )
}
