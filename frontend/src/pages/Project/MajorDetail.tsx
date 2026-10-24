import { useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeftOutlined,
  ExclamationCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
  RollbackOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Form,
  Input,
  message,
  Modal,
  Popconfirm,
  Row,
  Select,
  Space,
  Spin,
  Tag,
  Tooltip,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'

import AttachmentPanel from '@/components/AttachmentPanel'
import PageHeader from '@/components/PageHeader'
import Table from '@/components/Table'
import { MajorStatusTag, ProgressCell, SubStatusTag } from '@/components/Tags'
import { alertApi, collegeApi, majorApi, progressApi, subApi, systemApi } from '@/api'
import { fmtDate, fmtDateTime } from '@/lib/constants'
import { useCan } from '@/stores/auth'
import type { College, MajorCollege, Sub, UserItem } from '@/types'

/** 人员下拉（限定学院） */
function CollegeOwnerSelect({
  collegeId,
  value,
  onChange,
}: {
  collegeId?: number
  value?: number
  onChange?: (v: number | undefined) => void
}) {
  const { data: users, isFetching } = useQuery({
    queryKey: ['pick-users', 'college', collegeId],
    queryFn: () => systemApi.pickUsers(collegeId ? { college_id: collegeId } : undefined),
    enabled: collegeId !== undefined,
    staleTime: 60_000,
  })
  return (
    <Select
      showSearch
      allowClear
      value={value}
      placeholder="选择学院负责人"
      loading={isFetching}
      optionFilterProp="label"
      onChange={(v) => onChange?.(v)}
      options={(users ?? []).map((u) => ({
        value: u.id,
        label: `${u.name}（${u.staff_no}）`,
      }))}
    />
  )
}

export default function MajorDetailPage() {
  const { id } = useParams()
  const majorId = Number(id)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const can = useCan()
  // 添加/移除参与学院需要 project:college:edit；完整性检查只读，登录即可
  const canEditCollege = can('project:college:edit')

  // ---------------------------------------------------------------- 数据
  const { data: major, isLoading } = useQuery({
    queryKey: ['major', majorId],
    queryFn: () => majorApi.detail(majorId),
    enabled: Number.isFinite(majorId) && majorId > 0,
  })

  const { data: colleges, isLoading: collegesLoading } = useQuery({
    queryKey: ['major-colleges', majorId],
    queryFn: () => majorApi.colleges(majorId),
    enabled: Number.isFinite(majorId) && majorId > 0,
  })

  const { data: subs, isLoading: subsLoading } = useQuery({
    queryKey: ['major-subs', majorId],
    queryFn: () => subApi.list({ major_id: majorId, page: 1, page_size: 200 }),
    enabled: Number.isFinite(majorId) && majorId > 0,
  })

  // 进度汇总：大项目进度 / 各学院进度 / 全校进度
  // P1-9：后端 GET /progress/major/{id} 早就算好了，但前端一直没有页面消费，
  // 校领导和大项目负责人打开详情看不到任何进度数字。
  const { data: summary } = useQuery({
    queryKey: ['major-summary', majorId],
    queryFn: () => progressApi.majorSummary(majorId),
    enabled: Number.isFinite(majorId) && majorId > 0,
  })

  // 该大项目名下未关闭的预警数（详情页一屏看到「当前有几条预警」）
  const { data: majorAlerts } = useQuery({
    queryKey: ['major-alerts', majorId],
    queryFn: () =>
      alertApi.list({ major_id: majorId, status: 'PENDING', page: 1, page_size: 1 }),
    enabled: Number.isFinite(majorId) && majorId > 0,
  })

  // 学院字典（添加学院弹窗用）
  const { data: allColleges } = useQuery({
    queryKey: ['colleges'],
    queryFn: collegeApi.list,
    staleTime: 5 * 60_000,
  })

  // ---------------------------------------------------------------- 完整性检查
  const [completeness, setCompleteness] = useState<Awaited<
    ReturnType<typeof majorApi.completeness>
  > | null>(null)
  const checkMutation = useMutation({
    mutationFn: () => majorApi.completeness(majorId),
    onSuccess: (result) => {
      setCompleteness(result)
      message.success('检查完成')
      void queryClient.invalidateQueries({ queryKey: ['major', majorId] })
    },
  })

  // ---------------------------------------------------------------- 添加学院
  const [addCollegeOpen, setAddCollegeOpen] = useState(false)
  const [addForm] = Form.useForm<{ college_id: number; college_owner_id?: number }>()
  const addedCollegeIds = useMemo(
    () => new Set((colleges ?? []).map((c) => c.college_id)),
    [colleges],
  )
  const availableColleges: College[] = useMemo(
    () => (allColleges ?? []).filter((c) => !addedCollegeIds.has(c.id)),
    [allColleges, addedCollegeIds],
  )
  const selectedCollegeId = Form.useWatch('college_id', addForm)

  const addCollegeMutation = useMutation({
    mutationFn: (values: { college_id: number; college_owner_id?: number }) =>
      majorApi.addCollege(majorId, values.college_id, values.college_owner_id ?? null),
    onSuccess: () => {
      message.success('已添加参与学院')
      setAddCollegeOpen(false)
      addForm.resetFields()
      void queryClient.invalidateQueries({ queryKey: ['major-colleges', majorId] })
      void queryClient.invalidateQueries({ queryKey: ['major', majorId] })
    },
  })

  const removeCollegeMutation = useMutation({
    mutationFn: (collegeId: number) => majorApi.removeCollege(majorId, collegeId),
    onSuccess: () => {
      message.success('已移除')
      void queryClient.invalidateQueries({ queryKey: ['major-colleges', majorId] })
      void queryClient.invalidateQueries({ queryKey: ['major', majorId] })
    },
  })

  // ---------------------------------------------------------------- 学院负责人
  // P0-3：大项目详情原来只能「添加学院」和「移除」，已添加的学院无法补/换
  // 负责人；而 AL-09（责任空缺）恰恰不可手工关闭、只能补齐数据自动关闭 ——
  // 于是漏填一次负责人，那条红色预警就永久关不掉。
  // 后端 add_major_college 对已存在的学院是「更新负责人」语义，这里复用。
  const [ownerOpen, setOwnerOpen] = useState<MajorCollege | null>(null)
  const [ownerForm] = Form.useForm<{ college_owner_id?: number }>()
  const setOwnerMutation = useMutation({
    mutationFn: (v: { collegeId: number; ownerId?: number }) =>
      majorApi.addCollege(majorId, v.collegeId, v.ownerId ?? null),
    onSuccess: () => {
      message.success('已设置学院负责人')
      setOwnerOpen(null)
      ownerForm.resetFields()
      void queryClient.invalidateQueries({ queryKey: ['major-colleges', majorId] })
      void queryClient.invalidateQueries({ queryKey: ['major', majorId] })
      void queryClient.invalidateQueries({ queryKey: ['alerts'] })
    },
  })

  // ---------------------------------------------------------------- 列定义
  const collegeColumns: ColumnsType<MajorCollege> = [
    { title: '学院名称', dataIndex: 'college_name' },
    {
      title: '学院负责人',
      dataIndex: 'college_owner_name',
      render: (v: string | null) => v ?? <span style={{ color: '#ef4444' }}>未指定</span>,
    },
    {
      title: '学院进度',
      key: 'college_progress',
      width: 110,
      align: 'center',
      render: (_, record: MajorCollege) => {
        const p = summary?.college_progress?.[String(record.college_id)]
        return p === undefined ? '—' : <b>{p}%</b>
      },
    },
    { title: '子项目数', dataIndex: 'sub_count', width: 100, align: 'center' },
    {
      title: '操作',
      key: 'action',
      width: 160,
      render: (_, record: MajorCollege) =>
        canEditCollege ? (
          <Space size={0}>
            <Button
              type="link"
              size="small"
              onClick={() => {
                setOwnerOpen(record)
                ownerForm.setFieldsValue({
                  college_owner_id: record.college_owner_id ?? undefined,
                })
              }}
            >
              设置负责人
            </Button>
            <Popconfirm
              title="确认移除该学院？"
              description="学院下有子项目时无法移除"
              onConfirm={() => removeCollegeMutation.mutate(record.college_id)}
            >
              <Button type="link" size="small" danger>
                移除
              </Button>
            </Popconfirm>
          </Space>
        ) : (
          <span style={{ color: '#9ca3af' }}>—</span>
        ),
    },
  ]

  const subColumns: ColumnsType<Sub> = [
    {
      title: '子项目名称',
      dataIndex: 'name',
      render: (_, record: Sub) => (
        <Link to="/progress/table">{record.name}</Link>
      ),
    },
    { title: '学院', dataIndex: 'college_name', width: 140 },
    { title: '负责人', dataIndex: 'owner_name', width: 100 },
    {
      title: '计划起止',
      key: 'plan',
      width: 190,
      render: (_, record: Sub) =>
        record.plan_start ? (
          <>
            {fmtDate(record.plan_start)} ~ {fmtDate(record.plan_end)}
          </>
        ) : (
          <span style={{ color: '#f59e0b' }}>未设置</span>
        ),
    },
    {
      title: '进度',
      dataIndex: 'current_progress',
      width: 150,
      render: (v: number) => <ProgressCell value={v} />,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (v: string) => <SubStatusTag status={v} />,
    },
  ]

  if (isLoading) {
    return (
      <div className="page-container" style={{ textAlign: 'center', paddingTop: 120 }}>
        <Spin size="large" />
      </div>
    )
  }

  if (!major) {
    return (
      <div className="page-container">
        <Alert type="warning" showIcon message="项目不存在或已被删除" />
      </div>
    )
  }

  return (
    <div className="page-container">
      <PageHeader
        title={major.name}
        subtitle={
          <Space>
            <Button
              type="link"
              size="small"
              icon={<ArrowLeftOutlined />}
              onClick={() => navigate('/project/majors')}
              style={{ padding: 0 }}
            >
              返回列表
            </Button>
          </Space>
        }
        extra={
          <Button
            icon={<ReloadOutlined />}
            loading={checkMutation.isPending}
            onClick={() => checkMutation.mutate()}
          >
            完整性检查
          </Button>
        }
      />

      {/* 进度汇总卡（需求 3.2(3)：子项目 → 学院 → 大项目 → 全校 一层层往上加） */}
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col xs={12} md={6}>
          <Card size="small">
            <div className="metric-label">大项目进度</div>
            <div className="metric-value" style={{ color: '#2563eb' }}>
              {summary ? `${summary.major_progress}%` : '—'}
            </div>
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card size="small">
            <div className="metric-label">全校进度</div>
            <div className="metric-value" style={{ color: '#0891b2' }}>
              {summary ? `${summary.school_progress}%` : '—'}
            </div>
            <Tooltip title="各大项目等权平均（一期不按经费折算，见需求 6.3 第 20 项）">
              <span style={{ fontSize: 12, color: '#9ca3af' }}>等权平均</span>
            </Tooltip>
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card size="small">
            <div className="metric-label">计入汇总子项目</div>
            <div className="metric-value">{summary?.counted_subs ?? '—'}</div>
            <span style={{ fontSize: 12, color: '#9ca3af' }}>不含未开始/已中止</span>
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card size="small">
            <div className="metric-label">待处置预警</div>
            <div className="metric-value" style={{ color: '#ef4444' }}>
              {majorAlerts ? majorAlerts.total : '—'}
            </div>
          </Card>
        </Col>
      </Row>

      {/* 基本信息 */}
      <Card style={{ marginBottom: 16 }}>
        <Descriptions column={2} size="small">
          <Descriptions.Item label="项目类型">{major.type}</Descriptions.Item>
          <Descriptions.Item label="项目状态">
            <MajorStatusTag status={major.status} />
          </Descriptions.Item>
          <Descriptions.Item label="合作企业">
            {major.partner_company ?? '—'}
          </Descriptions.Item>
          <Descriptions.Item label="建设周期">{major.build_period ?? '—'}</Descriptions.Item>
          <Descriptions.Item label="总负责人">{major.owner_name ?? '—'}</Descriptions.Item>
          <Descriptions.Item label="原始计划">
            {fmtDate(major.original_start)} ~ {fmtDate(major.original_end)}
            <span style={{ color: '#6b7280', marginLeft: 6, fontSize: 12 }}>（立项锁定）</span>
          </Descriptions.Item>
          <Descriptions.Item label="参与学院">{major.college_count} 个</Descriptions.Item>
          <Descriptions.Item label="子项目">{major.sub_count} 个</Descriptions.Item>
        </Descriptions>
      </Card>

      {/* 完整性检查结果 */}
      {completeness && (
        <Card title="责任与计划完整性" style={{ marginBottom: 16 }}>
          {completeness.complete ? (
            <Alert type="success" showIcon message="责任与计划完整" />
          ) : (
            <Alert
              type="warning"
              showIcon
              message="存在待完善项"
              description={
                <ul style={{ margin: '4px 0 0', paddingLeft: 20 }}>
                  {completeness.items.map((item) => (
                    <li key={item.code}>
                      {item.label}
                      {item.detail ? `：${item.detail}` : ''}
                    </li>
                  ))}
                </ul>
              }
            />
          )}
        </Card>
      )}

      {/* 参与学院 */}
      <Card
        title={`参与学院（${colleges?.length ?? 0}）`}
        style={{ marginBottom: 16 }}
        extra={
          canEditCollege && (
            <Button
              type="primary"
              size="small"
              icon={<PlusOutlined />}
              onClick={() => setAddCollegeOpen(true)}
            >
              添加学院
            </Button>
          )
        }
      >
        <Table<MajorCollege>
          rowKey="college_id"
          density="compact"
          loading={collegesLoading}
          columns={collegeColumns}
          dataSource={colleges ?? []}
          pagination={false}
        />
      </Card>

      {/* 子项目 */}
      <Card title={`子项目（${subs?.total ?? 0}）`} style={{ marginBottom: 16 }}>
        <Table<Sub>
          rowKey="id"
          density="compact"
          loading={subsLoading}
          columns={subColumns}
          dataSource={subs?.items ?? []}
          pagination={false}
          onRow={(record) => ({
            style: { cursor: 'pointer' },
            // 带上大项目筛选跳转，避免落到全量进度总表里让人自己翻
            onClick: () => navigate(`/progress/table?major_id=${majorId}`),
          })}
        />
      </Card>

      {/* 项目档案材料（需求 3.1：协议、方案、照片等不属于任何报表的材料）
          后端一直支持 biz_type=MAJOR，但前端此前只有子项目附件入口。 */}
      <Card title="项目档案材料" style={{ marginBottom: 16 }}>
        <AttachmentPanel
          bizType="MAJOR"
          bizId={majorId}
          readOnly={!can('project:major:edit') && !can('progress:update')}
        />
      </Card>

      {/* 设置学院负责人 */}
      <Modal
        title={`设置学院负责人 — ${ownerOpen?.college_name ?? ''}`}
        open={!!ownerOpen}
        onCancel={() => setOwnerOpen(null)}
        confirmLoading={setOwnerMutation.isPending}
        onOk={() => ownerForm.submit()}
        destroyOnHidden
      >
        <Form
          form={ownerForm}
          layout="vertical"
          onFinish={(v) =>
            setOwnerMutation.mutate({
              collegeId: ownerOpen!.college_id,
              ownerId: v.college_owner_id,
            })
          }
        >
          <Form.Item name="college_owner_id" label="负责人">
            {ownerOpen ? <CollegeOwnerSelect collegeId={ownerOpen.college_id} /> : null}
          </Form.Item>
          <Alert
            type="info"
            showIcon
            message="AL-09（责任空缺）预警只能通过补齐负责人自动关闭"
          />
        </Form>
      </Modal>

      {/* 添加学院弹窗 */}
      <Modal
        title="添加参与学院"
        open={addCollegeOpen}
        onCancel={() => setAddCollegeOpen(false)}
        confirmLoading={addCollegeMutation.isPending}
        onOk={() => addForm.submit()}
        destroyOnHidden
      >
        <Form
          form={addForm}
          layout="vertical"
          onFinish={(v) => addCollegeMutation.mutate(v)}
        >
          <Form.Item
            name="college_id"
            label="选择学院"
            rules={[{ required: true, message: '请选择学院' }]}
          >
            <Select
              showSearch
              placeholder="搜索学院"
              optionFilterProp="label"
              notFoundContent="所有学院均已参与"
              options={availableColleges.map((c) => ({ value: c.id, label: c.name }))}
            />
          </Form.Item>
          <Form.Item
            name="college_owner_id"
            label="学院负责人"
            extra={
              selectedCollegeId
                ? undefined
                : '请先选择学院，再从该学院人员中选择负责人'
            }
          >
            {selectedCollegeId ? (
              <CollegeOwnerSelect collegeId={selectedCollegeId} />
            ) : (
              <Select disabled placeholder="先选择学院" />
            )}
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
