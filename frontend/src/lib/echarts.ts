/**
 * ECharts 按需引入
 *
 * 只注册项目里实际用到的图表与组件，避免整包引入（整包约 1MB，按需后约 350KB）。
 * 新增图表类型时，在这里补 `use([...])` 即可。
 */
import * as echarts from 'echarts/core'
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import {
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  MarkPointComponent,
  TitleComponent,
  TooltipComponent,
} from 'echarts/components'
import { LabelLayout, UniversalTransition } from 'echarts/features'
import { CanvasRenderer } from 'echarts/renderers'
import type { ComposeOption } from 'echarts/core'
import type {
  BarSeriesOption,
  LineSeriesOption,
  PieSeriesOption,
} from 'echarts/charts'
import type {
  GridComponentOption,
  LegendComponentOption,
  TitleComponentOption,
  TooltipComponentOption,
} from 'echarts/components'

echarts.use([
  BarChart,
  LineChart,
  PieChart,
  GridComponent,
  LegendComponent,
  TitleComponent,
  TooltipComponent,
  MarkLineComponent,
  MarkPointComponent,
  LabelLayout,
  UniversalTransition,
  CanvasRenderer,
])

/** 项目内可用的图表配置类型（只含已注册的图表与组件） */
export type EChartsOption = ComposeOption<
  | BarSeriesOption
  | LineSeriesOption
  | PieSeriesOption
  | GridComponentOption
  | LegendComponentOption
  | TitleComponentOption
  | TooltipComponentOption
>

export type { ECharts } from 'echarts/core'

export default echarts
