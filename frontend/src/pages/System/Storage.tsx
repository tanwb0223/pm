import { useQuery } from '@tanstack/react-query'
import { Card, Col, Descriptions, Progress, Result, Row, Statistic, Tag, Typography } from 'antd'
import { CloudServerOutlined, DatabaseOutlined, FileOutlined } from '@ant-design/icons'

import PageHeader from '@/components/PageHeader'
import { attachmentApi } from '@/api'
import { useCan } from '@/stores/auth'

/**
 * 存储运维
 *
 * 把附件占用、磁盘剩余这些运维信息集中到一处。
 * 原来 attachmentApi.stats() 只有附件面板内部在用，没有独立入口，
 * 管理员想看「磁盘还剩多少」只能翻日志。
 */
export default function StoragePage() {
  const can = useCan()
  const allowed = can('system:config')

  const { data, isLoading } = useQuery({
    queryKey: ['attachment-stats'],
    queryFn: attachmentApi.stats,
    enabled: allowed,
  })

  if (!allowed) {
    return (
      <div className="page-container">
        <Result status="403" title="无权限" subTitle="需要 system:config 权限才能查看存储信息" />
      </div>
    )
  }

  const usedMb = data?.size_mb ?? 0
  const totalMb = (data?.disk_free_mb ?? 0) + usedMb
  // 磁盘已用比例（仅为粗略参考：只统计了附件占用与剩余空间）
  const usedPercent = totalMb > 0 ? Math.min(100, Math.round((usedMb / totalMb) * 100)) : 0
  const freePercent = totalMb > 0 ? Math.round(((data?.disk_free_mb ?? 0) / totalMb) * 100) : 0

  return (
    <div className="page-container">
      <PageHeader
        title="存储运维"
        subtitle="附件占用与磁盘剩余空间；磁盘空间不足时上传会失败，请提前清理或扩容"
      />

      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col xs={24} sm={8}>
          <Card loading={isLoading}>
            <Statistic
              title="附件数量"
              value={data?.count ?? 0}
              suffix="个"
              prefix={<FileOutlined />}
            />
          </Card>
        </Col>
        <Col xs={24} sm={8}>
          <Card loading={isLoading}>
            <Statistic
              title="附件占用"
              value={usedMb}
              precision={1}
              suffix="MB"
              prefix={<DatabaseOutlined />}
            />
          </Card>
        </Col>
        <Col xs={24} sm={8}>
          <Card loading={isLoading}>
            <Statistic
              title="磁盘剩余"
              value={data?.disk_free_mb ?? 0}
              precision={0}
              suffix="MB"
              prefix={<CloudServerOutlined />}
              valueStyle={{ color: (data?.disk_free_mb ?? 0) < 1024 ? '#cf1322' : undefined }}
            />
          </Card>
        </Col>
      </Row>

      <Card title="磁盘占用参考" style={{ marginBottom: 16 }}>
        <Row gutter={32}>
          <Col span={12}>
            <Typography.Text type="secondary">附件占用（相对剩余+附件）</Typography.Text>
            <Progress percent={usedPercent} status="active" />
          </Col>
          <Col span={12}>
            <Typography.Text type="secondary">磁盘剩余</Typography.Text>
            <Progress
              percent={freePercent}
              strokeColor={freePercent < 10 ? '#cf1322' : '#389e0d'}
            />
          </Col>
        </Row>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          注：附件占用只统计系统内附件；磁盘剩余为存储目录所在盘的剩余空间。
          磁盘低于 1GB 时统计卡片会标红。
        </Typography.Text>
      </Card>

      <Card title="存储位置">
        <Descriptions column={1} size="small" bordered>
          <Descriptions.Item label="附件目录">
            <code style={{ fontSize: 13 }}>{data?.storage_dir ?? '—'}</code>
          </Descriptions.Item>
          <Descriptions.Item label="磁盘剩余">
            {data?.disk_free_mb ?? 0} MB
            {(data?.disk_free_mb ?? 0) < 1024 && (
              <Tag color="red" style={{ marginLeft: 8 }}>
                空间不足
              </Tag>
            )}
          </Descriptions.Item>
        </Descriptions>
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          备份文件与附件分开存放；备份目录见「服务器配置建议」文档。
        </Typography.Text>
      </Card>
    </div>
  )
}
