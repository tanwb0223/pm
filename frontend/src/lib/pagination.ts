import type { TablePaginationConfig } from 'antd'

/**
 * 列表分页统一规范
 *
 * 全站表格共用同一套分页外观与行为，避免各页面各写一套导致
 * 条数、文案、是否可改页大小不一致。
 */

/** 默认每页条数 */
export const PAGE_SIZE = 20

/** 可选的每页条数 */
export const PAGE_SIZE_OPTIONS = ['20', '50', '100']

/**
 * 生成统一的分页配置
 *
 * @param opts.current    当前页（1 基）
 * @param opts.total      总条数
 * @param opts.pageSize   每页条数，默认 PAGE_SIZE
 * @param opts.onChange   页码/页大小变化回调
 * @param opts.unit       计数单位，默认「条」
 * @param opts.simple     是否隐藏页大小切换（如行数很少的表格）
 *
 * 用法：
 *   pagination={tablePagination({ current: page, total, onChange: setPage })}
 */
export function tablePagination(opts: {
  current: number
  total: number
  pageSize?: number
  onChange?: (page: number, pageSize: number) => void
  unit?: string
  simple?: boolean
}): TablePaginationConfig {
  const size = opts.pageSize ?? PAGE_SIZE
  const unit = opts.unit ?? '条'
  return {
    current: opts.current,
    pageSize: size,
    total: opts.total,
    size: 'small',
    showSizeChanger: !opts.simple,
    pageSizeOptions: PAGE_SIZE_OPTIONS,
    showTotal: (t) => `共 ${t} ${unit}`,
    onChange: (page, ps) => opts.onChange?.(page, ps),
  }
}
