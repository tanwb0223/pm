import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { App as AntApp, ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import dayjs from 'dayjs'

import App from './App'
import './styles/global.css'

dayjs.locale('zh-cn')

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
      staleTime: 30 * 1000,
    },
  },
})

/** 主题 token（对应设计文档的深蓝主色） */
const theme = {
  token: {
    colorPrimary: '#1e3a5f',
    colorInfo: '#1e3a5f',
    colorSuccess: '#389e0d',
    colorWarning: '#d46b08',
    colorError: '#cf1322',
    borderRadius: 4,
    fontSize: 14,
    fontFamily:
      '-apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", "PingFang SC", "Hiragino Sans GB", "Helvetica Neue", Arial, sans-serif',
  },
  components: {
    Layout: {
      headerBg: '#1e3a5f',
      siderBg: '#ffffff',
    },
    Menu: {
      itemSelectedBg: '#e9eef4',
      itemSelectedColor: '#1e3a5f',
    },
    Table: {
      headerBg: '#f0f4f8',
      headerColor: '#1e3a5f',
      rowHoverBg: '#f7f9fc',
    },
  },
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider locale={zhCN} theme={theme}>
      <AntApp>
        <QueryClientProvider client={queryClient}>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </QueryClientProvider>
      </AntApp>
    </ConfigProvider>
  </React.StrictMode>,
)
