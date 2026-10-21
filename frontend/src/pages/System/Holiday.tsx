import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  App,
  Button,
  Calendar,
  DatePicker,
  Form,
  Input,
  Modal,
  Popconfirm,
  Radio,
  Result,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import dayjs, { type Dayjs } from 'dayjs'
import type { ColumnsType } from 'antd/es/table'

import PageHeader from '@/components/PageHeader'
import { systemApi } from '@/api'
import { PAGE_SIZE, tablePagination } from '@/lib/pagination'
import { useAuthStore, useCan } from '@/stores/auth'

interface HolidayRow {
  id: number
  date: string
  type: string
  name: string | null
}

const TYPE_LABEL: Record<string, string> = { HOLIDAY: '假期', VACATION: '寒暑假' }

/** 免填日历：免填日不计入预警计时（有效日口径） */
export default function HolidayPage() {
  const { message } = App.useApp()
  const qc = useQueryClient()
  const can = useCan()
  const canWrite = can('system:holiday')

  const thisYear = dayjs().year()
  const [year, setYear] = useState<number>(thisYear)
  const [month, setMonth] = useState<Dayjs>(dayjs())
  const [open, setOpen] = useState(false)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(PAGE_SIZE)
  const [form] = Form.useForm()

  const canView = can('system:holiday')

  const { data, isLoading } = useQuery({
    queryKey: ['holidays', year],
    queryFn: () => systemApi.holidays(year),
    // 无权限时不发请求：否则会弹一次 403 错误提示
    enabled: canView,
  })

  const addMut = useMutation({
    mutationFn: (values: { range: [Dayjs, Dayjs]; type: string; name?: string }) => {
      const dates: string[] = []
      let cur = values.range[0].startOf('day')
      const end = values.range[1].startOf('day')
      while (cur.isBefore(end) || cur.isSame(end)) {
        dates.push(cur.format('YYYY-MM-DD'))
        cur = cur.add(1, 'day')
      }
      return systemApi.addHolidays(dates, values.type, values.name)
    },
    onSuccess: (r) => {
      message.success(`已设置 ${r.total} 天（新增 ${r.added} 天）`)
      setOpen(false)
      qc.invalidateQueries({ queryKey: ['holidays'] })
    },
  })

  const removeMut = useMutation({
    mutationFn: (id: number) => systemApi.removeHoliday(id),
    onSuccess: () => {
      message.success('已删除')
      qc.invalidateQueries({ queryKey: ['holidays'] })
    },
  })

  const byDate = useMemo(() => {
    const map = new Map<string, HolidayRow>()
    for (const h of data ?? []) map.set(h.date, h)
    return map
  }, [data])

  const columns: ColumnsType<HolidayRow> = [
    { title: '日期', dataIndex: 'date', width: 140 },
    {
      title: '类型',
      dataIndex: 'type',
      width: 110,
      render: (v: string) => <Tag color={v === 'VACATION' ? 'purple' : 'orange'}>{TYPE_LABEL[v] ?? v}</Tag>,
    },
    { title: '名称', dataIndex: 'name', render: (v: string | null) => v ?? '—' },
    {
      title: '操作',
      key: 'op',
      width: 90,
      render: (_, row) =>
        canWrite && (
          <Popconfirm title="确认删除该免填日？" onConfirm={() => removeMut.mutate(row.id)}>
            <Button type="link" size="small" danger>
              删除
            </Button>
          </Popconfirm>
        ),
    },
  ]

  const dateCellRender = (value: Dayjs) => {
    const key = value.format('YYYY-MM-DD')
    const h = byDate.get(key)
    if (!h) return null
    return (
      <Tag color={h.type === 'VACATION' ? 'purple' : 'red'} style={{ margin: 0, fontSize: 12 }}>
        {h.name || TYPE_LABEL[h.type] || '免填'}
      </Tag>
    )
  }

  const yearOptions = [thisYear - 2, thisYear - 1, thisYear, thisYear + 1, thisYear + 2].map((y) => ({
    value: y,
    label: `${y} 年`,
  }))

  if (!canView) {
    return (
      <div className="page-container">
        <Result status="403" title="无权限" subTitle="需要 system:holiday 权限才能查看免填日历" />
      </div>
    )
  }

  return (
    <div className="page-container">
      <PageHeader
        title="免填日历"
        subtitle="免填日期不计入预警计时（有效日口径），例如寒暑假、法定节假日；报表应填判定同样使用该日历"
        extra={
          canWrite && (
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>
              批量设置
            </Button>
          )
        }
      />

      <Space style={{ marginBottom: 16 }}>
        <Select style={{ width: 130 }} options={yearOptions} value={year} onChange={setYear} />
        <span style={{ color: '#6b7280', fontSize: 13 }}>
          当年共 {data?.length ?? 0} 个免填日
        </span>
      </Space>

      <Calendar
        value={month}
        onPanelChange={(v) => {
          setMonth(v)
          if (v.year() !== year) setYear(v.year())
        }}
        cellRender={(current, info) => {
          if (info.type === 'date') return dateCellRender(current)
          return null
        }}
      />

      <div style={{ marginTop: 24 }}>
        <Typography.Title level={5}>列表</Typography.Title>
        <Table
          rowKey="id"
          columns={columns}
          dataSource={data ?? []}
          loading={isLoading}
          pagination={tablePagination({
            current: page,
            pageSize,
            total: data?.length ?? 0,
            onChange: (p, ps) => {
              setPage(p)
              setPageSize(ps)
            },
            unit: '天',
          })}
        />
      </div>

      <Modal
        title="批量设置免填日"
        open={open}
        onOk={async () => {
          const values = await form.validateFields()
          addMut.mutate(values)
        }}
        confirmLoading={addMut.isPending}
        onCancel={() => setOpen(false)}
        destroyOnHidden
      >
        <Form form={form} layout="vertical">
          <Form.Item
            label="日期范围"
            name="range"
            rules={[{ required: true, message: '请选择起止日期' }]}
          >
            <DatePicker.RangePicker style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item label="类型" name="type" initialValue="HOLIDAY" rules={[{ required: true }]}>
            <Radio.Group
              options={[
                { value: 'HOLIDAY', label: '假期（法定节假日等）' },
                { value: 'VACATION', label: '寒暑假' },
              ]}
            />
          </Form.Item>
          <Form.Item label="名称" name="name">
            <Input placeholder="如 国庆节、寒假" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
