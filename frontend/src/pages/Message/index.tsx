import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { App, Badge, Button, Card, Empty, Segmented, Space, Tag, Typography } from 'antd'
import { BellOutlined, CheckOutlined } from '@ant-design/icons'

import PageHeader from '@/components/PageHeader'
import { messageApi, type MessageItem } from '@/api'
import { fmtDateTime } from '@/lib/constants'

const BIZ_LABEL: Record<string, string> = {
  ALERT: '预警',
  URGE: '催办',
  DIGEST: '每日摘要',
  REPORT: '报表',
  SYSTEM: '系统',
}

const BIZ_COLOR: Record<string, string> = {
  ALERT: 'red',
  URGE: 'orange',
  DIGEST: 'blue',
  REPORT: 'green',
  SYSTEM: 'default',
}

const LEVEL_COLOR: Record<string, string> = {
  RED: 'red',
  ORANGE: 'orange',
  YELLOW: 'gold',
}

/** 消息点击后的跳转目标 */
function targetOf(m: MessageItem): string | null {
  if (m.biz_type === 'ALERT') return '/alert/center'
  if (m.biz_type === 'URGE') return '/progress/table'
  if (m.biz_type === 'DIGEST') return '/alert/stats'
  return null
}

/**
 * 我的消息（站内消息收件箱）
 *
 * 背景：早期 INNER 通道只往 notify_log 写一行状态就记 SUCCESS，
 * 但 notify_log 是管理员运维日志（要 system:audit 才能看），
 * 被催办/被预警的人登录后什么也看不到。本页是收件人视角的入口。
 */
export default function MessagePage() {
  const { message } = App.useApp()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [unreadOnly, setUnreadOnly] = useState(false)
  const [page, setPage] = useState(1)
  const pageSize = 20

  const params = useMemo(
    () => ({ unread_only: unreadOnly || undefined, page, page_size: pageSize }),
    [unreadOnly, page],
  )

  const { data, isLoading } = useQuery({
    queryKey: ['messages', params],
    queryFn: () => messageApi.list(params),
  })

  const { data: unread } = useQuery({
    queryKey: ['messages-unread'],
    queryFn: messageApi.unreadCount,
    refetchInterval: 60 * 1000,
  })

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['messages'] })
    queryClient.invalidateQueries({ queryKey: ['messages-unread'] })
  }

  const readMut = useMutation({
    mutationFn: (id: number) => messageApi.read(id),
    onSuccess: invalidate,
  })

  const readAllMut = useMutation({
    mutationFn: () => messageApi.readAll(),
    onSuccess: (res) => {
      message.success(`已将 ${res.updated} 条消息标记为已读`)
      invalidate()
    },
  })

  const items = data?.items ?? []
  const total = data?.total ?? 0

  const handleClick = async (m: MessageItem) => {
    if (!m.read_at) {
      readMut.mutate(m.id)
    }
    const target = targetOf(m)
    if (target) navigate(target)
  }

  return (
    <div className="page-container">
      <PageHeader
        title="我的消息"
        subtitle="预警提醒、催办通知、每日摘要；未读消息会在顶栏铃铛上显示红点"
        extra={
          <Space>
            <Segmented
              value={unreadOnly ? 'unread' : 'all'}
              options={[
                { value: 'all', label: '全部' },
                { value: 'unread', label: '只看未读' },
              ]}
              onChange={(v) => {
                setUnreadOnly(v === 'unread')
                setPage(1)
              }}
            />
            <Button
              icon={<CheckOutlined />}
              disabled={!unread?.count}
              loading={readAllMut.isPending}
              onClick={() => readAllMut.mutate()}
            >
              全部已读
            </Button>
          </Space>
        }
      />

      <Space direction="vertical" size={10} style={{ width: '100%' }}>
        {isLoading && <Card loading />}

        {!isLoading && items.length === 0 && (
          <Card>
            <Empty
              image={<BellOutlined style={{ fontSize: 40, color: '#d1d5db' }} />}
              imageStyle={{ height: 48 }}
              description={unreadOnly ? '没有未读消息' : '暂无消息'}
            />
          </Card>
        )}

        {!isLoading &&
          items.map((m) => {
            const unreadFlag = !m.read_at
            return (
              <Card
                key={m.id}
                size="small"
                hoverable={!!targetOf(m)}
                onClick={() => handleClick(m)}
                style={{
                  cursor: targetOf(m) ? 'pointer' : 'default',
                  borderLeft: unreadFlag ? '3px solid #2563eb' : '3px solid transparent',
                }}
              >
                <Space direction="vertical" size={4} style={{ width: '100%' }}>
                  <Space size={8} wrap>
                    {unreadFlag && <Badge status="processing" />}
                    <Tag color={BIZ_COLOR[m.biz_type] ?? 'default'} style={{ margin: 0 }}>
                      {BIZ_LABEL[m.biz_type] ?? m.biz_type}
                    </Tag>
                    {m.level && (
                      <Tag color={LEVEL_COLOR[m.level] ?? 'default'} style={{ margin: 0 }}>
                        {m.level === 'RED' ? '红色' : m.level === 'ORANGE' ? '橙色' : '黄色'}
                      </Tag>
                    )}
                    <Typography.Text strong={unreadFlag}>{m.title}</Typography.Text>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {fmtDateTime(m.created_at)}
                    </Typography.Text>
                  </Space>
                  {m.body && (
                    <Typography.Paragraph
                      type="secondary"
                      style={{ margin: 0, whiteSpace: 'pre-wrap', fontSize: 13 }}
                    >
                      {m.body}
                    </Typography.Paragraph>
                  )}
                  {targetOf(m) && (
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      点击查看详情 →
                    </Typography.Text>
                  )}
                </Space>
              </Card>
            )
          })}

        {total > pageSize && (
          <Space style={{ justifyContent: 'flex-end', width: '100%' }}>
            <Button disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
              上一页
            </Button>
            <Typography.Text type="secondary">
              第 {page} / {Math.ceil(total / pageSize)} 页，共 {total} 条
            </Typography.Text>
            <Button
              disabled={page >= Math.ceil(total / pageSize)}
              onClick={() => setPage((p) => p + 1)}
            >
              下一页
            </Button>
          </Space>
        )}
      </Space>
    </div>
  )
}
