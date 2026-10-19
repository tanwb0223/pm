import { useEffect, useMemo, useState } from 'react'
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  AlertOutlined,
  AuditOutlined,
  BarChartOutlined,
  CalendarOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  FileTextOutlined,
  LogoutOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  ProjectOutlined,
  SafetyOutlined,
  SettingOutlined,
  TeamOutlined,
  WarningOutlined,
} from '@ant-design/icons'
import { Avatar, Badge, Button, Dropdown, Layout, Menu, Space, Tag, Typography } from 'antd'

import { alertApi, authApi, collegeApi, majorApi } from '@/api'
import { useAuthStore } from '@/stores/auth'
import { useDictStore } from '@/stores/dict'

const { Header, Sider, Content } = Layout
const { Text } = Typography

export default function AppLayout() {
  const navigate = useNavigate()
  const location = useLocation()
  const [collapsed, setCollapsed] = useState(false)
  const { user, setUser, logout, hasPerm } = useAuthStore()
  const setColleges = useDictStore((s) => s.setColleges)
  const setMajors = useDictStore((s) => s.setMajors)

  // 加载当前用户
  const { data: me } = useQuery({
    queryKey: ['me'],
    queryFn: authApi.me,
    staleTime: 5 * 60 * 1000,
  })
  useEffect(() => {
    if (me) setUser(me)
  }, [me, setUser])

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

  const menuItems = useMemo(() => {
    const items: { key: string; icon: JSX.Element; label: React.ReactNode }[] = [
      {
        key: '/dashboard',
        icon: <DashboardOutlined />,
        label: (
          <Space>
            我的督办
            {todo && todo.pending_alerts > 0 && (
              <Badge count={todo.pending_alerts} size="small" />
            )}
          </Space>
        ),
      },
    ]

    if (hasPerm('project:major:view') || hasPerm('project:sub:view')) {
      items.push({
        key: 'project',
        icon: <ProjectOutlined />,
        label: '项目管理',
      } as never)
    }
    if (hasPerm('progress:table')) {
      items.push({ key: '/progress/table', icon: <BarChartOutlined />, label: '进度总表' })
      items.push({
        key: '/progress/college-compare',
        icon: <BarChartOutlined />,
        label: '学院对比',
      })
    }
    if (hasPerm('alert:list')) {
      items.push({
        key: '/alert/center',
        icon: <AlertOutlined />,
        label: (
          <Space>
            预警中心
            {todo && todo.pending_alerts > 0 && (
              <Badge count={todo.pending_alerts} size="small" color="red" />
            )}
          </Space>
        ),
      })
    }
    if (hasPerm('report:fill')) {
      items.push({ key: '/report/list', icon: <FileTextOutlined />, label: '报表管理' })
    }
    if (hasPerm('risk:list')) {
      items.push({ key: '/risk/list', icon: <WarningOutlined />, label: '风险登记' })
    }
    if (hasPerm('system:user') || hasPerm('system:role')) {
      items.push({ key: 'system', icon: <SettingOutlined />, label: '系统管理' } as never)
    }
    return items
  }, [hasPerm, todo])

  // 二级菜单
  const childrenMap: Record<string, { key: string; label: string; icon?: JSX.Element }[]> = {
    project: [
      { key: '/project/majors', label: '大项目' },
      { key: '/project/subs', label: '子项目' },
    ],
    system: [
      { key: '/system/users', label: '用户管理', icon: <TeamOutlined /> },
      { key: '/system/roles', label: '角色权限', icon: <SafetyOutlined /> },
      { key: '/system/auth-providers', label: '认证方式', icon: <AuditOutlined /> },
      { key: '/system/configs', label: '参数配置', icon: <DatabaseOutlined /> },
      { key: '/system/holidays', label: '免填日历', icon: <CalendarOutlined /> },
      { key: '/system/audit', label: '操作日志', icon: <AuditOutlined /> },
    ],
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
      <Header
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '0 20px',
          height: 56,
          lineHeight: '56px',
        }}
      >
        <Space size={12}>
          <Button
            type="text"
            icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
            onClick={() => setCollapsed(!collapsed)}
            style={{ color: '#fff' }}
          />
          <Text style={{ color: '#fff', fontSize: 17, fontWeight: 600, letterSpacing: 1 }}>
            产教融合项目管理系统
          </Text>
        </Space>
        <Space size={16}>
          {todo && todo.pending_alerts > 0 && (
            <Link to="/alert/center">
              <Tag color="red">待处置预警 {todo.pending_alerts}</Tag>
            </Link>
          )}
          <Dropdown
            menu={{
              items: [
                {
                  key: 'logout',
                  icon: <LogoutOutlined />,
                  label: '退出登录',
                  onClick: () => {
                    logout()
                    navigate('/login')
                  },
                },
              ],
            }}
          >
            <Space style={{ cursor: 'pointer', color: '#fff' }}>
              <Avatar size="small" style={{ background: '#4a7ba7' }}>
                {user?.name?.[0] ?? 'U'}
              </Avatar>
              <Text style={{ color: '#fff' }}>{user?.name ?? '加载中'}</Text>
            </Space>
          </Dropdown>
        </Space>
      </Header>

      <Layout>
        <Sider
          collapsed={collapsed}
          width={208}
          theme="light"
          style={{ borderRight: '1px solid #e8eef4', overflow: 'auto' }}
        >
          <Menu
            mode="inline"
            selectedKeys={[selectedKey]}
            defaultOpenKeys={['project', 'system']}
            items={finalItems as never}
            onClick={({ key }) => {
              if (key.startsWith('/')) navigate(key)
            }}
            style={{ border: 'none', paddingTop: 8 }}
          />
        </Sider>

        <Content style={{ overflow: 'auto', background: '#f5f7fa' }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  )
}
