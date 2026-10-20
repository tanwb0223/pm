# 产教融合项目管理系统 · 前端

React 18 + TypeScript + Ant Design 5 + TanStack Query
构建工具：**Vite 5** ｜ 包管理器：**pnpm**

## 快速开始

### 1. 安装 pnpm（首次）

```powershell
# 用 npm 全局安装 pnpm
npm install -g pnpm

# 或使用 corepack（Node 16.10+ 自带）
corepack enable
corepack prepare pnpm@latest --activate

# 验证
pnpm --version
```

### 2. 安装依赖

```powershell
cd frontend
pnpm install
```

> 首次安装约 10 秒（pnpm 用硬链接复用全局缓存，比 npm 快 2-3 倍）

### 3. 启动开发服务器

```powershell
pnpm dev
```

访问 http://127.0.0.1:5173

开发服务器已配置 `/api` 代理到 `http://127.0.0.1:8000`，需先启动后端。

```powershell
# 另开一个终端
cd backend
.\.venv\Scripts\uvicorn.exe app.main:app --port 8000
```

默认账号：`admin` / `Admin@123`

## 常用命令

| 命令 | 说明 |
|---|---|
| `pnpm dev` | 启动开发服务器（热更新） |
| `pnpm build` | 生产构建（类型检查 + 打包） |
| `pnpm preview` | 预览构建产物 |
| `pnpm lint` | ESLint 检查 |
| `pnpm clean` | 清理 node_modules 和 dist |
| `pnpm rebuild` | 清理后重新安装并构建 |

## 为什么用 pnpm

| 对比项 | npm | pnpm |
|---|---|---|
| 安装速度 | 基准 | **快 2-3 倍** |
| 磁盘占用 | 每个项目独立 node_modules | **硬链接共享，省 60%+** |
| 依赖冲突 | 扁平化，可能幽灵依赖 | **严格隔离，只能访问声明过的** |
| lock 文件 | package-lock.json | pnpm-lock.yaml |

本项目用 pnpm 的实际收益：
- 170 个包，10 秒装完
- 多个项目共用同一份依赖缓存
- 避免 antd 等库的隐式依赖问题
## 技术选型

| 项 | 选择 | 理由 |
|---|---|---|
| 构建 | Vite 5 | 启动快，HMR 体验好 |
| 框架 | React 18 | 生态成熟 |
| 语言 | TypeScript 5 严格模式 | 类型安全 |
| UI | **Ant Design 5** | 后台管理系统首选，表格/表单/弹窗开箱即用 |
| 数据 | TanStack Query 5 | 缓存、失效、重试自动化，省掉手写 loading 状态 |
| 状态 | zustand 5 | 轻量，只存 token 和字典 |
| 路由 | react-router-dom 6 | |
| HTTP | axios | 拦截器统一处理 token 与错误 |

**没有用 shadcn/ui + Tailwind**：本项目是重表格、重表单的管理系统，Ant Design 的 Table/Form/DatePicker/Upload 直接可用，比手搓 Tailwind 组件省 30% 以上工作量。

## 目录结构

```
src/
├── main.tsx            入口（ConfigProvider 主题 + QueryClient）
├── App.tsx             路由表
├── api/index.ts        全部接口封装（按模块分组）
├── components/
│   ├── PageHeader.tsx  统一页头
│   └── Tags.tsx        状态标签、偏差文本
├── layouts/
│   └── AppLayout.tsx   侧边栏 + 顶栏 + 权限菜单
├── lib/
│   ├── http.ts         axios 实例、拦截器、下载
│   └── constants.ts    枚举翻译、颜色、日期格式化
├── stores/
│   ├── auth.ts         token / 用户 / 权限判断
│   └── dict.ts         学院、大项目下拉缓存
├── types/index.ts      全部 TS 类型
└── pages/              22 个页面
```

## 页面清单

| 模块 | 页面 | 路由 |
|---|---|---|
| 登录 | 账号密码 / 9 种第三方 | `/login` |
| 登录 | OIDC 回调落地 | `/login/oidc/complete` |
| 督办 | 我的督办（4 指标 + 2 待办表） | `/dashboard` |
| 项目 | 大项目列表 | `/project/majors` |
| 项目 | 大项目详情（学院分工 + 子项目） | `/project/majors/:id` |
| 项目 | 子项目列表 | `/project/subs` |
| 进度 | **进度总表**（最高频） | `/progress/table` |
| 进度 | 学院横向对比 | `/progress/college-compare` |
| 预警 | 预警中心（折叠聚合 + 处置） | `/alert/center` |
| 预警 | 预警统计 | `/alert/stats` |
| 报表 | 报表列表 | `/report/list` |
| 报表 | **报表填报**（周报初稿） | `/report/fill` |
| 报表 | 报表及时率 | `/report/timeliness` |
| 风险 | 风险登记 | `/risk/list` |
| 系统 | 组织管理（学院增删改 + 批量导入） | `/system/org` |
| 系统 | 用户管理 | `/system/users` |
| 系统 | 角色权限（3 Tab） | `/system/roles` |
| 系统 | 认证方式（9 种协议） | `/system/auth-providers` |
| 系统 | 参数配置 | `/system/configs` |
| 系统 | 免填日历 | `/system/holidays` |
| 系统 | 操作日志 | `/system/audit` |
| 其他 | 404 | `*` |

## 核心设计

### 1. 权限控制

```tsx
const hasPerm = useAuthStore((s) => s.hasPerm)

{hasPerm('project:major:edit') && <Button>新建</Button>}
```

菜单项也按权限过滤，SYS_ADMIN 自动拥有全部权限。

### 2. 数据范围

前端不做数据过滤，全部由后端 `apply_scope` 处理。前端只负责展示——这样即使前端被绕过，数据也不会越权泄露。

### 3. 请求层

```ts
// 响应自动解包：后端 {code, message, data} → 直接返回 data
const data = await get<PageData<Major>>('/majors', { page: 1 })

// 错误自动提示：拦截器统一 message.error
// 401 自动跳登录页
```

### 4. 偏差着色（业务规则可视化）

`DeviationText` 组件按文档 4.2 阈值着色：

| 偏差 | 颜色 | 含义 |
|---|---|---|
| ≤ −25% | 红 | 严重滞后（AL-02） |
| < −10% | 橙 | 进度滞后（AL-01） |
| > 0 | 绿 | 超前 |
| 其他 | 黑 | 正常 |

### 5. 进度填报交互

- Slider + InputNumber 联动
- 勾选「本周期无变化」后进度输入禁用（对应后端 `no_change`）
- **回调动态校验**：新进度低于当前值时，「回调原因」变为必填（对应后端校验）

### 6. 预警处置

- AL-09（责任空缺）、AL-10（计划缺失）的关闭/忽略按钮**禁用**，提示「补齐数据后自动关闭」——与后端硬约束一致
- 转交用 `systemApi.pickUsers`（不需要 `system:user` 权限）

## 构建

```powershell
pnpm build
```

产物在 `dist/`，已做代码分割：

| 包 | 大小 | gzip |
|---|---|---|
| antd | 1,175 KB | 368 KB |
| react | 163 KB | 53 KB |
| index（业务） | 112 KB | 36 KB |
| data（query/axios） | 94 KB | 32 KB |

antd 占大头是因为全量引入。如需优化可改用按需加载，但当前 gzip 后总量约 490 KB，校园内网可接受。

## 部署

### 开发

Vite 代理转发 `/api` 到后端 8000 端口。

### 生产

```nginx
server {
    listen 80;
    server_name pm.school.edu.cn;

    root /var/www/pm/dist;
    index index.html;

    # 前端路由（history 模式）
    location / {
        try_files $uri $uri/ /index.html;
    }

    # API 反向代理
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
        # 导出大文件
        proxy_read_timeout 300s;
    }
}
```

`X-Forwarded-*` 头是必须的——后端用它拼接 OIDC/CAS 回调地址。

## 已验证

用 Edge 无头浏览器逐页实测（1600×1000）：

| 项 | 结果 |
|---|---|
| TypeScript 严格模式检查 | ✅ 0 错误 |
| 生产构建 | ✅ 6.5s |
| 18 个页面渲染 | ✅ 全部正常 |
| 控制台错误 | ✅ 无 |
| 登录 → 跳转 → 各页导航 | ✅ |
| API 代理连通 | ✅ |
| 登录卡片居中 | ✅（修复 `height:100%` → `min-height:100vh`） |
| 看板预警类型列 | ✅（修复后端字段 `type` → `alert_type`） |
| 进度条可读性 | ✅（加宽 + 按进度着色 + 数字） |
| antd 弃用警告 | ✅（`destroyOnClose` → `destroyOnHidden`） |
| favicon 404 | ✅（内联 SVG） |
| 组织管理页（新增 / 编辑 / 权限菜单） | ✅（无头浏览器真实点击验证，接口同步校验） |

## 已知限制

- 报表明细没有逐条列表接口，及时率页只展示统计卡与圆环
- 预警统计图表用 antd 基础组件实现，未引入 ECharts（如需折线/饼图可后续加）
- 大项目导出字段勾选未在前端做 UI，接口已支持 `fields` 参数

---

## 迭代更新（v0.2）

本轮完成三项增强：

### 1. 预警统计图表（ECharts）

`/alert/stats` 由 antd 基础组件升级为 5 个真实图表：

| 图表 | 类型 | 数据来源 |
|---|---|---|
| 预警趋势 | 折线（3 条：红/橙/黄） | 后端 `stats.trend`（按日聚合） |
| 按等级分布 | 环形图 | `by_level` |
| 按类型分布 | 横向柱状图 | `by_type` |
| 按大项目分布 Top10 | 双柱状（总数/红色） | 后端新增 `by_major` |
| 未处置时长分布 | 饼图（1天内/1-3天/3-7天/7天以上） | 后端新增 `age_buckets` |

**按需引入**：只注册 Bar/Line/Pie + Grid/Tooltip/Legend 等必需组件，ECharts 包从 1MB 降到 604KB（gzip 209KB），独立成 chunk 不影响首屏。

自己封装了 `components/Chart.tsx` 而非用 `echarts-for-react`：
- 完全控制 init/dispose 时机
- 用 `ResizeObserver` 响应侧边栏折叠
- 少一个依赖

### 2. 报表明细接口 + 及时率明细表

**后端新增**：`GET /api/reports/timeliness/detail`（分页 + 按状态筛选）

**及时率页新增「逐期明细」表**：

| 列 | 说明 |
|---|---|
| 子项目 / 类型 / 应报周期 | |
| 截止时间 / 提交时间 | |
| 状态 | 缺交（红）/ 迟交（橙）/ 按期（绿） |
| 逾期 | 逾期天数，按期显示「—」 |

支持 Segmented 切换「全部/缺交/迟交/按期」，分页展示。原来只有统计卡和圆环，无法定位到具体是哪一期没交。

### 3. 大项目导出字段勾选

导出时弹窗勾选需要的列（12 个字段），支持全选/清空，当前筛选条件（关键词/类型/状态）一并生效。

实测：全字段 12 列、只选 3 项则导出 3 列。

## 修复的问题

| 问题 | 根因 | 修复 |
|---|---|---|
| 趋势图日期标签重叠成一条 | 30 天标签全显示 | 按 `Math.ceil(len/12)-1` 计算 interval 抽稀 |
| 未处置时长饼图全是同色 | 0 值数据项参与渲染 | 过滤 `value > 0` 的数据项 |
| 及时率「应报周期」列文字重叠 | 列宽 200px 不足 | 加宽到 230px + `whiteSpace: nowrap` |
| 构建报错 | `manualChunks` 仍引用已卸载的 `echarts-for-react` | 改为只引用 `echarts` |

## 构建体积（更新）

| 包 | 大小 | gzip |
|---|---|---|
| antd | 1,185 KB | 371 KB |
| echarts（按需，独立 chunk） | 604 KB | 209 KB |
| react | 163 KB | 53 KB |
| index（业务） | 120 KB | 38 KB |
| data | 94 KB | 32 KB |

echarts 只在访问 `/alert/stats` 时才加载，不影响其他页面首屏。
