import { useLayoutEffect, useRef, useState } from 'react'
import { Table } from 'antd'
import type { TableProps } from 'antd'

interface Props<T> extends TableProps<T> {
  /** 表格最小宽度，等价于 scroll.x（列多时横向滚动） */
  minTableWidth?: number
  /** 最小表体高度，避免窗口过小时压扁 */
  minBodyHeight?: number
}

/**
 * 自适应高度表格
 *
 * 特性：
 * 1. 表头固定（Ant Design 的 scroll.y 机制）
 * 2. 表体滚动，只滚动内容区
 * 3. 分页固定在容器底部
 * 4. 数据不足一页时，表体仍撑满可用高度（不留白）
 *
 * 用法：替换 `<Table>` 为 `<DataTable>`，把 `scroll={{ x: 1100 }}`
 * 改成 `minTableWidth={1100}`。父容器需要有确定高度
 * （页面用 .page-container 自动满足）。
 */
export default function DataTable<T extends object>({
  minTableWidth,
  minBodyHeight = 120,
  scroll,
  pagination,
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

  const mergedScroll = {
    ...scroll,
    ...(minTableWidth ? { x: minTableWidth } : {}),
    ...(bodyHeight > 0 ? { y: bodyHeight } : {}),
  }

  return (
    <div className="data-table-fill" ref={wrapRef}>
      <Table<T> {...rest} scroll={mergedScroll} pagination={pagination} />
    </div>
  )
}
