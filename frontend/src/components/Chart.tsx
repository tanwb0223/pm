import { useEffect, useRef } from 'react'

import echarts, { type EChartsOption } from '@/lib/echarts'

/**
 * ECharts 轻量封装（按需引入，不依赖 echarts-for-react）
 *
 * 相比 echarts-for-react，自己封装的好处：
 * - 完全控制 resize 与 dispose 时机
 * - 避免额外依赖，打包体积更小
 */
interface Props {
  option: EChartsOption
  height?: number
  loading?: boolean
  empty?: boolean
  emptyText?: string
}

export default function Chart({ option, height = 280, loading, empty, emptyText }: Props) {
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<echarts.ECharts | null>(null)

  // 初始化 + resize 监听
  useEffect(() => {
    if (empty || !ref.current) return

    const chart = echarts.init(ref.current)
    chartRef.current = chart

    const onResize = () => chart.resize()
    window.addEventListener('resize', onResize)

    // 侧边栏折叠等布局变化也能自适应
    const ro = new ResizeObserver(() => chart.resize())
    ro.observe(ref.current)

    return () => {
      window.removeEventListener('resize', onResize)
      ro.disconnect()
      chart.dispose()
      chartRef.current = null
    }
  }, [empty])

  // 更新配置
  useEffect(() => {
    if (!chartRef.current || empty) return
    const merged: EChartsOption = {
      textStyle: {
        fontFamily:
          '-apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", "PingFang SC", sans-serif',
        color: '#1f2937',
      },
      grid: { left: 12, right: 20, top: 30, bottom: 8, containLabel: true },
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      ...option,
    }
    chartRef.current.setOption(merged, true)
  }, [option, empty])

  // 加载态
  useEffect(() => {
    if (!chartRef.current) return
    if (loading) chartRef.current.showLoading('default', { text: '加载中', color: '#4f46e5' })
    else chartRef.current.hideLoading()
  }, [loading])

  if (empty) {
    return (
      <div
        style={{
          height,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: '#d1d5db',
          fontSize: 13,
        }}
      >
        {emptyText ?? '暂无数据'}
      </div>
    )
  }

  return <div ref={ref} style={{ height, width: '100%' }} />
}
