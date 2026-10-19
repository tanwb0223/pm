import { useState } from 'react'
import { Alert, App, Button, Modal, Space, Switch, Table, Tag, Upload } from 'antd'
import { DownloadOutlined, InboxOutlined } from '@ant-design/icons'
import type { UploadFile } from 'antd'

import { systemApi } from '@/api'
import type { ImportResult } from '@/types'

interface Props {
  open: boolean
  kind: 'user' | 'college'
  onClose: () => void
  onSuccess?: () => void
}

/**
 * 批量导入弹窗（用户 / 学院）
 *
 * 流程：下载模板 → 填好上传 → 后端校验全部行 → 全部通过才写库
 */
export default function ImportModal({ open, kind, onClose, onSuccess }: Props) {
  const { message } = App.useApp()
  const [file, setFile] = useState<File | null>(null)
  const [updateExisting, setUpdateExisting] = useState(false)
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<ImportResult | null>(null)

  const isUser = kind === 'user'
  const label = isUser ? '用户' : '学院'

  const reset = () => {
    setFile(null)
    setResult(null)
    setUpdateExisting(false)
  }

  const handleClose = () => {
    reset()
    onClose()
  }

  const doImport = async () => {
    if (!file) {
      message.warning('请先选择文件')
      return
    }
    setLoading(true)
    setResult(null)
    try {
      const r = isUser
        ? await systemApi.importUsers(file, { update_existing: updateExisting })
        : await systemApi.importColleges(file, updateExisting)
      setResult(r)
      if (r.error_count === 0) {
        message.success(
          `导入完成：新建 ${r.created}，更新 ${r.updated}，跳过 ${r.skipped}`,
        )
        onSuccess?.()
      } else {
        message.warning(`发现 ${r.error_count} 处错误，未导入任何数据`)
      }
    } catch {
      // 错误已由 http 拦截器提示
    } finally {
      setLoading(false)
    }
  }

  return (
    <Modal
      title={`批量导入${label}`}
      open={open}
      onCancel={handleClose}
      onOk={doImport}
      okText="开始导入"
      confirmLoading={loading}
      okButtonProps={{ disabled: !file }}
      width={620}
      afterClose={reset}
    >
      <Space direction="vertical" size={14} style={{ width: '100%' }}>
        <Alert
          type="info"
          showIcon
          message="导入前请先下载模板"
          description={
            <div style={{ fontSize: 13, lineHeight: 1.8 }}>
              支持 .xlsx 和 .csv 两种格式，单次最多 10MB。
              <br />
              系统会先校验全部行，<b>只要有一处错误就整体不导入</b>，避免只导入一半。
            </div>
          }
          action={
            <Button
              size="small"
              icon={<DownloadOutlined />}
              onClick={() => systemApi.downloadTemplate(kind)}
            >
              下载模板
            </Button>
          }
        />

        <Upload.Dragger
          accept=".xlsx,.xlsm,.csv"
          maxCount={1}
          beforeUpload={(f) => {
            setFile(f)
            setResult(null)
            return false // 阻止自动上传，交给按钮触发
          }}
          onRemove={() => setFile(null)}
          fileList={
            file
              ? ([{ uid: '1', name: file.name, status: 'done' }] as UploadFile[])
              : []
          }
        >
          <p className="ant-upload-drag-icon">
            <InboxOutlined />
          </p>
          <p className="ant-upload-text">点击或拖拽文件到此区域</p>
          <p className="ant-upload-hint">支持 .xlsx / .csv</p>
        </Upload.Dragger>

        <Space>
          <Switch checked={updateExisting} onChange={setUpdateExisting} size="small" />
          <span style={{ fontSize: 13 }}>
            {isUser ? '工号已存在时更新该用户信息' : '学院已存在时更新名称与编码'}
          </span>
        </Space>

        {isUser && (
          <Alert
            type="warning"
            showIcon
            style={{ fontSize: 13 }}
            message="导入用户的初始密码为 Init@123456，请提醒教师首次登录后修改"
          />
        )}

        {result && (
          <div>
            <Space size={16} style={{ marginBottom: 8 }}>
              <span>
                共 <b>{result.total}</b> 行
              </span>
              <Tag color="green">新建 {result.created}</Tag>
              <Tag color="blue">更新 {result.updated}</Tag>
              <Tag>跳过 {result.skipped}</Tag>
              {result.error_count > 0 && <Tag color="red">错误 {result.error_count}</Tag>}
            </Space>
            {result.error_count > 0 && (
              <Table
                size="small"
                rowKey="row"
                pagination={false}
                scroll={{ y: 180 }}
                columns={[
                  { title: '行号', dataIndex: 'row', width: 70 },
                  { title: '错误原因', dataIndex: 'error' },
                ]}
                dataSource={result.errors}
              />
            )}
          </div>
        )}
      </Space>
    </Modal>
  )
}
