import { useEffect, useMemo, useState } from 'react'
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  AlertOutlined,
  ApartmentOutlined,
  AuditOutlined,
  BarChartOutlined,
  CalendarOutlined,
  DashboardOutlined,
  EditOutlined,
  DatabaseOutlined,
  DownOutlined,
  FileTextOutlined,
  LockOutlined,
  LinkOutlined,
  CloudServerOutlined,
  LogoutOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  NotificationOutlined,
  ProjectOutlined,
  SafetyOutlined,
  SettingOutlined,
  TeamOutlined,
  ThunderboltFilled,
  WarningOutlined,
} from '@ant-design/icons'
import { Avatar, Badge, Button, Dropdown, Layout, Menu, Space, Tag, Typography } from 'antd'

import { alertApi, authApi, collegeApi, majorApi } from '@/api'
import ChangePasswordModal from '@/components/ChangePasswordModal'
import BindAccountModal from '@/components/BindAccountModal'
import { useAuthStore, useCan, useCanAny } from '@/stores/auth'
import { clearQueryCache } from '@/lib/queryClient'
import { useDictStore } from '@/stores/dict'

const { Header, Sider, Content } = Layout
const { Text } = Typography

export default function AppLayout() {
  const navigate = useNavigate()
  const location = useLocation()
  const [collapsed, setCollapsed] = useState(false)
  // 修改密码弹窗；forced 表示首次登录/被重置，必须改完才能继续
  const [pwdOpen, setPwdOpen] = useState(false)
  const [bindOpen, setBindOpen] = useState(false)
  const [pwdForced, setPwdForced] = useState(false)
  const { user, setUser, resetSession, sessionKey } = useAuthStore()
  const setColleges = useDictStore((s) => s.setColleges)
  const setMajors = useDictStore((s) => s.setMajors)

  // 加载当前用户
  // queryKey 带上 sessionKey：换账号后 sessionKey 递增，强制重新拉取，
  // 不会命中上一个账号残留的 ['me'] 缓存（否则会用旧账号覆盖新登录用户）。
  const { data: me } = useQuery({
    queryKey: ['me', sessionKey],
    queryFn: authApi.me,
    staleTime: 5 * 60 * 1000,
  })
  useEffect(() => {
    if (me) setUser(me)
  }, [me, setUser])

  // 首次登录或被管理员重置密码后，强制弹改密框（不可取消）
  useEffect(() => {
    if (me?.must_change_password) {
      setPwdForced(true)
      setPwdOpen(true)
    }
  }, [me?.must_change_password])

  /** 退出登录：先清空查询缓存与字典，再跳登录页，避免换账号后串号 */
  const handleLogout = () => {
    clearQueryCache()
    setColleges([])
    setMajors([])
    resetSession()
    navigate('/login')
  }

  // 加载字典
  const { data: colleges } = useQuery({ queryKey: ['colleges'], queryFn: collegeApi.list })
  useEffect(() => {
    if (colleges) setColleges(colleges.map((c) => ({ id: c.id, name: c.name })))
  }, [colleges, setColleges])

  const { data: majors } = useQuery({
    queryKey: ['majors-brief'],
    queryFn: () => majorApi.list({ page: 1, page_size: 200 }),
  })
  useEffect(() => {
    if (majors) setMajors(majors.items.map((m) => ({ id: m.id, name: m.name })))
  }, [majors, setMajors])

  // 待办数
  const { data: todo } = useQuery({
    queryKey: ['todo'],
    queryFn: alertApi.todo,
    refetchInterval: 60 * 1000,
  })

  // 权限判定：useCan() 内部订阅 user，刷新后 me 回来会触发重渲染
  const can = useCan()
  const canAny = useCanAny()

  // 系统管理二级菜单（按权限过滤）
  const systemChildren = useMemo(() => {
    const list: { key: string; label: string; icon?: JSX.Element }[] = []
    if (can('system:org')) {
      list.push({ key: '/system/org', label: '组织管理', icon: <ApartmentOutlined /> })
    }
    if (can('system:user')) {
      list.push({ key: '/system/users', label: '用户管理', icon: <TeamOutlined /> })
    }
    if (can('system:role')) {
      list.push({ key: '/system/roles', label: '角色权限', icon: <SafetyOutlined /> })
      list.push({ key: '/system/auth-providers', label: '认证方式', icon: <AuditOutlined /> })
    }
    if (can('system:config')) {
      list.push({ key: '/system/configs', label: '参数配置', icon: <DatabaseOutlined /> })
    }
    if (can('system:holiday')) {
      list.push({ key: '/system/holidays', label: '免填日历', icon: <CalendarOutlined /> })
    }
    if (can('system:audit')) {
      list.push({ key: '/system/audit', label: '操作日志', icon: <AuditOutlined /> })
      list.push({ key: '/system/notify-logs', label: '通知日志', icon: <NotificationOutlined /> })
    }
    if (can('system:config')) {
      list.push({ key: '/system/storage', label: '存储运维', icon: <CloudServerOutlined /> })
    }
    return list
  }, [can])

  // 项目管理子项（按权限过滤：只有查看大项目权限的不应看到子项目）
  const projectChildren = useMemo(() => {
    const list: { key: string; label: string; icon?: JSX.Element }[] = []
    if (can('project:major:view')) list.push({ key: '/project/majors', label: '大项目' })
    if (can('project:sub:view')) list.push({ key: '/project/subs', label: '子项目' })
    return list
  }, [can])

  // 预警管理子项
  const alertChildren = useMemo(
    () => [
      { key: '/alert/center', label: '预警中心', icon: <AlertOutlined /> },
      { key: '/alert/stats', label: '预警统计', icon: <BarChartOutlined /> },
    ],
    [],
  )

  // 报表管理子项（按权限过滤）
  // 判定只用 report 系列权限：不能拿通用 export 判，否则只读用户
  // （有 export）也会看到「及时率」，还会把「报表管理」父菜单带出来。
  const canReport = useMemo(
    () => canAny('report', 'report:fill', 'report:void'),
    [canAny],
  )

  const reportChildren = useMemo(() => {
    if (!canReport) return [] as { key: string; label: string; icon?: JSX.Element }[]
    const list: { key: string; label: string; icon?: JSX.Element }[] = []
    if (can('report:fill')) {
      list.push({ key: '/report/fill', label: '报表填报', icon: <EditOutlined /> })
    }
    list.push({ key: '/report/list', label: '报表列表', icon: <FileTextOutlined /> })
    list.push({ key: '/report/timeliness', label: '及时率', icon: <BarChartOutlined /> })
    return list
  }, [can, canReport])

  const menuItems = useMemo(() => {
    const items: { key: string; icon: JSX.Element; label: React.ReactNode }[] = [
      {
        key: '/dashboard',
        icon: <DashboardOutlined />,
        label: (
          <Space size={6}>
            我的督办
            {todo && todo.pending_alerts > 0 && (
              <Badge count={todo.pending_alerts} size="small" />
            )}
          </Space>
        ),
      },
    ]

    if (projectChildren.length > 0) {
      items.push({
        key: 'project',
        icon: <ProjectOutlined />,
        label: '项目管理',
      } as never)
    }
    if (can('progress:table')) {
      items.push({ key: '/progress/table', icon: <BarChartOutlined />, label: '进度总表' })
      items.push({
        key: '/progress/college-compare',
        icon: <BarChartOutlined />,
        label: '学院对比',
      })
    }
    if (can('alert:list') && alertChildren.length > 0) {
      items.push({
        key: 'alert',
        icon: <AlertOutlined />,
        label: (
          <Space size={6}>
            预警管理
            {todo && todo.pending_alerts > 0 && (
              <Badge count={todo.pending_alerts} size="small" color="#ef4444" />
            )}
          </Space>
        ),
      } as never)
    }
    if (reportChildren.length > 0) {
      items.push({ key: 'report', icon: <FileTextOutlined />, label: '报表管理' } as never)
    }
    if (can('risk:list')) {
      items.push({ key: '/risk/list', icon: <WarningOutlined />, label: '风险登记' })
    }
    if (systemChildren.length > 0) {
      items.push({ key: 'system', icon: <SettingOutlined />, label: '系统管理' } as never)
    }
    return items
  }, [can, todo, systemChildren, projectChildren, alertChildren, reportChildren])

  const childrenMap: Record<string, { key: string; label: string; icon?: JSX.Element }[]> = {
    project: projectChildren,
    alert: alertChildren,
    report: reportChildren,
    system: systemChildren,
  }
  const finalItems = menuItems.map((item) => {
    const children = childrenMap[item.key]
    if (children) {
      return { ...item, children }
    }
    return item
  })

  const selectedKey = (() => {
    const p = location.pathname
    if (p.startsWith('/project/majors')) return '/project/majors'
    if (p.startsWith('/project/subs')) return '/project/subs'
    if (p.startsWith('/progress')) return '/progress/table'
    if (p.startsWith('/alert/center')) return '/alert/center'
    if (p.startsWith('/alert/stats')) return '/alert/stats'
    if (p.startsWith('/report/list')) return '/report/list'
    if (p.startsWith('/report/fill')) return '/report/fill'
    if (p.startsWith('/report/timeliness')) return '/report/timeliness'
    if (p.startsWith('/risk')) return '/risk/list'
    if (p.startsWith('/system')) return p
    return p
  })()

  return (
    <Layout style={{ height: '100vh' }}>
      <Header className="app-header">
        <Space size={14}>
          <Button
            type="text"
            icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
            onClick={() => setCollapsed(!collapsed)}
            style={{ color: '#6b7280' }}
          />
          <div className="app-brand">
            <div className="app-logo">
              <ThunderboltFilled />
            </div>
            <span className="app-title">产教融合项目管理系统</span>
          </div>
        </Space>

        <Space size={14}>
          {todo && todo.pending_alerts > 0 && (
            <Link to="/alert/center">
              <Tag
                color="error"
                style={{ margin: 0, padding: '2px 10px', fontWeight: 600, fontSize: 12.5 }}
              >
                待处置预警 {todo.pending_alerts}
              </Tag>
            </Link>
          )}
          <Dropdown
            menu={{
              items: [
                {
                  key: 'password',
                  icon: <LockOutlined />,
                  label: '修改密码',
                  onClick: () => setPwdOpen(true),
                },
                {
                  key: 'bind',
                  icon: <LinkOutlined />,
                  label: '账号绑定',
                  onClick: () => setBindOpen(true),
                },
                { type: 'divider' },
                {
                  key: 'logout',
                  icon: <LogoutOutlined />,
                  label: '退出登录',
                  onClick: handleLogout,
                },
              ],
            }}
          >
            <div className="header-user">
              <Avatar size={28} className="header-avatar">
                {user?.name?.[0] ?? 'U'}
              </Avatar>
              <Text style={{ color: '#374151', fontSize: 13.5, fontWeight: 500 }}>
                {user?.name ?? '加载中'}
              </Text>
              <DownOutlined style={{ fontSize: 10, color: '#9ca3af' }} />
            </div>
          </Dropdown>
        </Space>
      </Header>

      <Layout>
        <Sider
          collapsed={collapsed}
          width={212}
          theme="light"
          style={{ overflow: 'auto', background: '#fff' }}
        >
          <Menu
            mode="inline"
            selectedKeys={[selectedKey]}
            defaultOpenKeys={['project', 'alert', 'report', 'system']}
            items={finalItems as never}
            onClick={({ key }) => {
              if (key.startsWith('/')) navigate(key)
            }}
            style={{ border: 'none', paddingTop: 10, paddingBottom: 16 }}
          />
        </Sider>

        <Content style={{ overflow: 'auto', background: '#f8fafc' }}>
          <Outlet />
        </Content>
      </Layout>

      <BindAccountModal open={bindOpen} onClose={() => setBindOpen(false)} />

      <ChangePasswordModal
        open={pwdOpen}
        forced={pwdForced}
        onClose={() => {
          setPwdOpen(false)
          setPwdForced(false)
        }}
        onSuccess={() => {
          // 改完立即刷新 me，让 must_change_password 归零
          if (me) setUser({ ...me, must_change_password: false })
        }}
      />
    </Layout>
  )
}
