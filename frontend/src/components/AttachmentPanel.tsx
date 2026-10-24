import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  App,
  Button,
  Empty,
  Popconfirm,
  Space,
  Tag,
  Tooltip,
  Upload,
} from 'antd'
import {
  DeleteOutlined,
  DownloadOutlined,
  EyeOutlined,
  InboxOutlined,
  PaperClipOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'
import type { UploadFile } from 'antd'

import { attachmentApi, type AttachmentItem } from '@/api'
import Table from '@/components/Table'
import { fmtDateTime } from '@/lib/constants'
import { useAuthStore, useCan } from '@/stores/auth'

interface Props {
  bizType: 'SUB' | 'MAJOR' | 'REPORT' | 'RISK' | 'OTHER'
  bizId: number
  readOnly?: boolean
}

/** 文件类型图标颜色 */
const EXT_COLOR: Record<string, string> = {
  pdf: 'red',
  doc: 'blue',
  docx: 'blue',
  xls: 'green',
  xlsx: 'green',
  ppt: 'orange',
  pptx: 'orange',
  zip: 'purple',
  rar: 'purple',
  '7z': 'purple',
  jpg: 'cyan',
  jpeg: 'cyan',
  png: 'cyan',
  txt: 'default',
}

/**
 * 附件管理组件
 *
 * 文件存文件系统，数据库只存元数据。
 * 支持拖拽上传、下载、预览、删除。
 */
export default function AttachmentPanel({ bizType, bizId, readOnly = false }: Props) {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const can = useCan()
  const me = useAuthStore((s) => s.user)
  const [uploading, setUploading] = useState(false)

  const queryKey = ['attachments', bizType, bizId]

  const { data = [], isLoading } = useQuery({
    queryKey,
    queryFn: () => attachmentApi.list(bizType, bizId),
    enabled: bizId > 0,
  })

  // 存储统计是管理员运维信息（后端要 system:config），普通用户不该请求，
  // 否则每次展开附件区都会弹一条"缺少权限：system:config"。
  const canSeeStats = can('system:config')
  const { data: stats } = useQuery({
    queryKey: ['attachment-stats'],
    queryFn: attachmentApi.stats,
    enabled: canSeeStats,
  })

  // 后端规定：只有上传人本人或管理员能删。按上传人决定按钮显隐，
  // 否则同学院同事上传的材料，负责人点了必 403。
  const canDelete = (r: AttachmentItem) =>
    !readOnly && (r.uploader_id === me?.id || me?.roles.includes('SYS_ADMIN'))

  const uploadMut = useMutation({
    mutationFn: (file: File) => attachmentApi.upload(file, bizType, bizId),
    onSuccess: () => {
      message.success('上传成功')
      qc.invalidateQueries({ queryKey })
      qc.invalidateQueries({ queryKey: ['attachment-stats'] })
    },
    onSettled: () => setUploading(false),
  })

  const delMut = useMutation({
    mutationFn: (id: number) => attachmentApi.remove(id),
    onSuccess: () => {
      message.success('已删除')
      qc.invalidateQueries({ queryKey })
      qc.invalidateQueries({ queryKey: ['attachment-stats'] })
    },
  })

  /** 读取当前登录令牌（下载/预览都要带 Authorization） */
  const currentToken = (): string => {
    try {
      const raw = localStorage.getItem('pm-auth')
      return raw ? (JSON.parse(raw)?.state?.token ?? '') : ''
    } catch {
      return ''
    }
  }

  /** 下载：带 token 的请求需要走 blob */
  const handleDownload = async (att: AttachmentItem) => {
    try {
      const accessToken = currentToken()
      const resp = await fetch(attachmentApi.downloadUrl(att.id), {
        headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
      })
      if (!resp.ok) throw new Error('下载失败')
      const blob = await resp.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = att.file_name
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } catch {
      message.error('下载失败')
    }
  }

  /**
   * 预览：只看图片和 PDF
   *
   * 不能直接 window.open 接口地址——浏览器导航不会带 Authorization 头，
   * 后端必然 401（早期实现就是这样，预览 100% 不可用）。
   * 这里改成先 fetch 成 blob，再开 object URL。
   */
  const handlePreview = async (att: AttachmentItem) => {
    try {
      const accessToken = currentToken()
      const resp = await fetch(attachmentApi.previewUrl(att.id), {
        headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
      })
      if (!resp.ok) throw new Error('预览失败')
      const blob = await resp.blob()
      const url = URL.createObjectURL(blob)
      window.open(url, '_blank', 'noopener')
      // 给新窗口留出读取时间再释放
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
    } catch {
      message.error('预览失败，请改用下载')
    }
  }

  /** 可内联预览的类型（图片 / PDF） */
  const canPreview = (att: AttachmentItem) =>
    att.mime_type?.startsWith('image/') || att.mime_type === 'application/pdf'

  const columns: ColumnsType<AttachmentItem> = [
    {
      title: '文件名',
      dataIndex: 'file_name',
      ellipsis: true,
      render: (v: string, r) => (
        <Space size={6}>
          <Tag color={EXT_COLOR[r.file_ext] ?? 'default'} style={{ margin: 0 }}>
            {(r.file_ext || '?').toUpperCase()}
          </Tag>
          <span title={v}>{v}</span>
        </Space>
      ),
    },
    { title: '大小', dataIndex: 'size_text', width: 90, align: 'right' },
    {
      title: '上传人',
      dataIndex: 'uploader_name',
      width: 100,
      render: (v: string | null) => v ?? '—',
    },
    {
      title: '上传时间',
      dataIndex: 'uploaded_at',
      width: 150,
      render: (v: string) => fmtDateTime(v),
    },
    {
      title: '下载',
      dataIndex: 'download_count',
      width: 70,
      align: 'center',
      render: (v: number) => (v > 0 ? v : '—'),
    },
    {
      title: '操作',
      width: 160,
      render: (_, r) => (
        <Space size={0}>
          <Tooltip title="下载">
            <Button
              type="link"
              size="small"
              icon={<DownloadOutlined />}
              onClick={() => handleDownload(r)}
            />
          </Tooltip>
          {canPreview(r) && (
            <Tooltip title="预览">
              <Button
                type="link"
                size="small"
                icon={<EyeOutlined />}
                onClick={() => handlePreview(r)}
              />
            </Tooltip>
          )}
          {canDelete(r) && (
            <Popconfirm
              title="确认删除？"
              description="删除后将不在列表中显示"
              onConfirm={() => delMut.mutate(r.id)}
            >
              <Tooltip title="删除">
                <Button type="link" size="small" danger icon={<DeleteOutlined />} />
              </Tooltip>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ]

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      {!readOnly && (
        <Upload.Dragger
          multiple={false}
          showUploadList={false}
          disabled={uploading}
          beforeUpload={(file) => {
            setUploading(true)
            uploadMut.mutate(file)
            return false // 阻止自动上传
          }}
          style={{ padding: '12px 0' }}
        >
          <p className="ant-upload-drag-icon" style={{ marginBottom: 4 }}>
            <InboxOutlined style={{ fontSize: 28, color: '#4f46e5' }} />
          </p>
          <p className="ant-upload-text" style={{ fontSize: 13 }}>
            点击或拖拽文件到此处上传
          </p>
          <p className="ant-upload-hint" style={{ fontSize: 12 }}>
            支持 PDF / Word / Excel / PPT / 图片 / 压缩包，单个不超过 50MB
          </p>
        </Upload.Dragger>
      )}

      <Table<AttachmentItem>
        rowKey="id"
        density="compact"
        loading={isLoading}
        columns={columns}
        dataSource={data}
        pagination={false}
        locale={{
          emptyText: (
            <Empty
              image={<PaperClipOutlined style={{ fontSize: 32, color: '#d1d5db' }} />}
              imageStyle={{ height: 40 }}
              description="暂无附件"
            />
          ),
        }}
      />

      {stats && (
        <div style={{ color: '#9ca3af', fontSize: 12 }}>
          共 {stats.count} 个文件，占用 {stats.size_mb} MB ｜ 磁盘剩余{' '}
          {Math.round(stats.disk_free_mb / 1024)} GB
          {stats.disk_free_mb < 1024 && (
            <Tag color="red" style={{ marginLeft: 8 }}>
              磁盘空间不足 1GB
            </Tag>
          )}
        </div>
      )}
    </Space>
  )
}
