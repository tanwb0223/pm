import { useLayoutEffect, useRef, useState } from 'react'
import { Table } from 'antd'
import type { TableProps } from 'antd'

interface Props<T> extends TableProps<T> {
  /**
   * 表格最小宽度（px）。
   *
   * 默认不设下限，交给浏览器按内容自适应。只有内容过于稀疏、
   * 希望兜一个下限时才传。注意：**不要**再用 `scroll.x` 手写数字，
   * 手写值小于实际列宽时末列（通常是「操作」）会被裁掉。
   */
  minTableWidth?: number
  /** 最小表体高度，避免窗口过小时压扁 */
  minBodyHeight?: number
  /**
   * 密度档位，默认 default。
   *
   * - `default`：页面主列表
   * - `compact`：内嵌明细
   *
   * 全站只允许这两档，不要再传 antd 的 `size`。
   */
  density?: 'default' | 'compact'
}

/** 累加列声明的固定宽度，得到列宽下限 */
function sumColumnWidth(columns: TableProps<any>['columns']): number {
  if (!columns) return 0
  let total = 0
  for (const col of columns) {
    if (!col) continue
    const anyCol = col as { width?: number | string; children?: TableProps<any>['columns'] }
    const w = anyCol.width
    if (typeof w === 'number') total += w
    else if (typeof w === 'string' && /^\d+(\.\d+)?(px)?$/.test(w)) total += parseFloat(w)
    if (anyCol.children?.length) total += sumColumnWidth(anyCol.children)
  }
  return total
}

/**
 * 自适应高度表格
 *
 * 特性：
 * 1. 表头固定（Ant Design 的 scroll.y 机制）
 * 2. 表体滚动，只滚动内容区
 * 3. 分页固定在容器底部
 * 4. 数据不足一页时，表体仍撑满可用高度（不留白）
 * 5. scroll.x 自动兜底，保证末列（通常是「操作」）不会被裁掉
 *
 * 用法：替换 `<Table>` 为 `<DataTable>`，把 `scroll={{ x: 1100 }}`
 * 改成 `minTableWidth={1100}`。父容器需要有确定高度
 * （页面用 .page-container 自动满足）。
 */
export default function DataTable<T extends object>({
  minTableWidth,
  minBodyHeight = 120,
  density = 'default',
  scroll,
  pagination,
  columns,
  expandable,
  rowSelection,
  ...rest
}: Props<T>) {
  const wrapRef = useRef<HTMLDivElement>(null)
  const [bodyHeight, setBodyHeight] = useState<number>(0)

  useLayoutEffect(() => {
    const el = wrapRef.current
    if (!el) return

    const calc = () => {
      const total = el.clientHeight
      if (total <= 0) return

      // 表头高度（scroll.y 模式下是独立 header）
      const headerEl =
        el.querySelector<HTMLElement>('.ant-table-header') ??
        el.querySelector<HTMLElement>('.ant-table-thead')
      const headerH = headerEl?.offsetHeight ?? 40

      // 分页高度（现在 margin 为 0，直接用 offsetHeight）
      const pagEl = el.querySelector<HTMLElement>('.ant-table-pagination')
      const pagH = pagEl ? pagEl.offsetHeight : 0

      const h = Math.max(total - headerH - pagH, minBodyHeight)
      setBodyHeight((prev) => (Math.abs(prev - h) > 1 ? h : prev))
    }

    calc()

    const ro = new ResizeObserver(calc)
    ro.observe(el)
    const t = window.setTimeout(calc, 120) // 等首屏渲染稳定

    return () => {
      ro.disconnect()
      window.clearTimeout(t)
    }
  }, [minBodyHeight, rest.dataSource, pagination])

  // 横向策略：默认用 'max-content'，让浏览器按「表头文字 + 单元格内容」
  // 的实际宽度排布，末列永远不会被裁掉；列总宽小于容器时由 CSS 拉伸铺满。
  //
  // 传了 minTableWidth 时才退回固定像素：这时 scroll.x 取
  // max(下限, 列宽之和)，避免手写下限偏小把末列挤出去。
  const minX =
    minTableWidth !== undefined
      ? Math.max(minTableWidth, sumColumnWidth(columns))
      : 'max-content'

  const mergedScroll = {
    ...scroll,
    x: minX,
    ...(bodyHeight > 0 ? { y: bodyHeight } : {}),
  }

  return (
    <div className="data-table-fill" ref={wrapRef}>
      <Table<T>
        {...rest}
        columns={columns}
        // 密度档位：default 用默认档字号/内边距，compact 走小尺寸档
        size={density === 'compact' ? 'small' : 'large'}
        scroll={mergedScroll}
        pagination={pagination}
      />
    </div>
  )
}
