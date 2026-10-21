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
  DatabaseOutlined,
  DownOutlined,
  FileTextOutlined,
  LogoutOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  ProjectOutlined,
  SafetyOutlined,
  SettingOutlined,
  TeamOutlined,
  ThunderboltFilled,
  WarningOutlined,
} from '@ant-design/icons'
import { Avatar, Badge, Button, Dropdown, Layout, Menu, Space, Tag, Typography } from 'antd'

import { alertApi, authApi, collegeApi, majorApi } from '@/api'
import { useAuthStore, useCan } from '@/stores/auth'
import { clearQueryCache } from '@/lib/queryClient'
import { useDictStore } from '@/stores/dict'

const { Header, Sider, Content } = Layout
const { Text } = Typography

export default function AppLayout() {
  const navigate = useNavigate()
  const location = useLocation()
  const [collapsed, setCollapsed] = useState(false)
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
    }
    return list
  }, [can])

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

    if (can('project:major:view') || can('project:sub:view')) {
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
    if (can('alert:list')) {
      items.push({
        key: '/alert/center',
        icon: <AlertOutlined />,
        label: (
          <Space size={6}>
            预警中心
            {todo && todo.pending_alerts > 0 && (
              <Badge count={todo.pending_alerts} size="small" color="#ef4444" />
            )}
          </Space>
        ),
      })
    }
    if (can('report:fill')) {
      items.push({ key: '/report/list', icon: <FileTextOutlined />, label: '报表管理' })
    }
    if (can('risk:list')) {
      items.push({ key: '/risk/list', icon: <WarningOutlined />, label: '风险登记' })
    }
    if (systemChildren.length > 0) {
      items.push({ key: 'system', icon: <SettingOutlined />, label: '系统管理' } as never)
    }
    return items
  }, [can, todo, systemChildren])

  const childrenMap: Record<string, { key: string; label: string; icon?: JSX.Element }[]> = {
    project: [
      { key: '/project/majors', label: '大项目' },
      { key: '/project/subs', label: '子项目' },
    ],
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
            defaultOpenKeys={['project', 'system']}
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
    </Layout>
  )
}
