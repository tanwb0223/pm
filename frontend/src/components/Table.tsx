import { Table as AntTable } from 'antd'
import type { TableProps } from 'antd'

/** 表格密度
 *
 * - `default`：页面主列表
 * - `compact`：内嵌明细（展开行、弹窗内、卡片内小表、权限矩阵）
 */
export type TableDensity = 'default' | 'compact'

export interface TableProps2<T> extends TableProps<T> {
  /** 密度档位，默认 default。不要再用 antd 的 size 控制表格疏密。 */
  density?: TableDensity
}

/**
 * 全站统一表格
 *
 * 字号、行高、内边距、表头配色全部来自 styles/theme.ts 的 tableDensity，
 * 这里只负责把 density 翻译成 antd 的 size：
 *
 *   density="default" → size="large"（用 cellFontSize / cellPaddingBlock）
 *   density="compact" → size="small"（用 cellFontSizeSM / cellPaddingBlockSM）
 *
 * 之所以不用 antd 的 middle，是因为 middle 档的表头与表体字号/缩进
 * 和默认档并不一致，混用会出现表头文字比单元格偏左/偏大的问题。
 */
export default function Table<T extends object>({ density = 'default', ...rest }: TableProps2<T>) {
  return <AntTable<T> {...rest} size={density === 'compact' ? 'small' : 'large'} />
}
