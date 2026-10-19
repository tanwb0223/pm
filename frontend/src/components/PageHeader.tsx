import type { ReactNode } from 'react'

interface Props {
  title: string
  subtitle?: ReactNode
  extra?: ReactNode
}

/** 统一页头 */
export default function PageHeader({ title, subtitle, extra }: Props) {
  return (
    <div className="page-header">
      <div>
        <h2 className="page-title">{title}</h2>
        {subtitle && <div className="page-subtitle">{subtitle}</div>}
      </div>
      {extra && <div>{extra}</div>}
    </div>
  )
}
