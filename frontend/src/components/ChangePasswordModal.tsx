import { useState } from 'react'
import { Form, Input, Modal, message } from 'antd'
import { LockOutlined } from '@ant-design/icons'

import { authApi } from '@/api'

interface Props {
  open: boolean
  /** 首次登录/被重置时强制改密：不可关闭、无取消按钮 */
  forced?: boolean
  onClose: () => void
  onSuccess?: () => void
}

interface FormValues {
  old_password: string
  new_password: string
  confirm_password: string
}

/**
 * 修改密码弹窗
 *
 * 首次登录或被管理员重置密码后由外层强制打开（forced），
 * 此时不给关闭入口，必须改完才能继续使用。
 */
export default function ChangePasswordModal({
  open,
  forced = false,
  onClose,
  onSuccess,
}: Props) {
  const [form] = Form.useForm<FormValues>()
  const [loading, setLoading] = useState(false)

  const submit = async () => {
    const values = await form.validateFields()
    setLoading(true)
    try {
      await authApi.changePassword(values.old_password, values.new_password)
      message.success('密码已修改，请牢记新密码')
      form.resetFields()
      onSuccess?.()
      onClose()
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data
        ?.detail
      message.error(detail || '修改失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <Modal
      open={open}
      title={forced ? '首次登录，请修改初始密码' : '修改密码'}
      okText="确认修改"
      cancelText="取消"
      confirmLoading={loading}
      onOk={submit}
      onCancel={forced ? undefined : onClose}
      closable={!forced}
      maskClosable={!forced}
      keyboard={!forced}
      cancelButtonProps={{ style: forced ? { display: 'none' } : undefined }}
      destroyOnClose
    >
      {forced && (
        <p style={{ color: '#b45309', marginBottom: 16 }}>
          当前使用的是初始密码，为保障账号安全，请先设置新密码。
        </p>
      )}
      <Form form={form} layout="vertical" preserve={false}>
        <Form.Item
          label="原密码"
          name="old_password"
          rules={[{ required: true, message: '请输入原密码' }]}
        >
          <Input.Password
            prefix={<LockOutlined />}
            placeholder={forced ? '请输入初始密码' : '请输入当前密码'}
            autoComplete="current-password"
          />
        </Form.Item>
        <Form.Item
          label="新密码"
          name="new_password"
          rules={[
            { required: true, message: '请输入新密码' },
            { min: 8, message: '至少 8 位' },
            {
              pattern: /^(?=.*[A-Za-z])(?=.*\d).+$/,
              message: '需同时包含字母和数字',
            },
          ]}
          extra="至少 8 位，需同时包含字母和数字"
        >
          <Input.Password
            prefix={<LockOutlined />}
            placeholder="设置新密码"
            autoComplete="new-password"
          />
        </Form.Item>
        <Form.Item
          label="确认新密码"
          name="confirm_password"
          dependencies={['new_password']}
          rules={[
            { required: true, message: '请再次输入新密码' },
            ({ getFieldValue }) => ({
              validator(_, value) {
                if (!value || getFieldValue('new_password') === value) {
                  return Promise.resolve()
                }
                return Promise.reject(new Error('两次输入的新密码不一致'))
              },
            }),
          ]}
        >
          <Input.Password
            prefix={<LockOutlined />}
            placeholder="再次输入新密码"
            autoComplete="new-password"
          />
        </Form.Item>
      </Form>
    </Modal>
  )
}
