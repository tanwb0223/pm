# 产教融合项目管理系统

面向高校的产教融合项目全生命周期管理平台，覆盖「立项建档 → 进度管控 → 预警督办 → 报表汇总 → 结项归档」。

## 项目构成

```
pm/
├── doc/                        全部文档
│   ├── 产教融合项目管理系统-需求方案说明书-V1.4.docx   需求方案（含 42 条待确认事项）
│   ├── 需求文档分析报告.md                            需求解读与疑点梳理
│   ├── 功能缺失分析报告.md                            与通用项目管理软件的差距分析
│   ├── 功能缺失复核报告.md                            代码实现与需求的逐项核对
│   ├── 开发方案.md                                    技术选型、数据模型、工作量
│   ├── Linux原生部署指南.md                            Linux 部署（含内网离线）
│   ├── Windows部署指南.md                              Windows 部署（含内网离线）
│   ├── 服务器配置建议.md                               配置选型与参数调优
│   └── 数据库双支持说明.md                             SQLite / PostgreSQL 切换与差异
├── backend/                    FastAPI + PostgreSQL 后端（84 个 API）
├── frontend/                   React 18 + TypeScript + Ant Design 5 前端（21 个页面）
├── pack.sh                     离线打包（同时产出 Linux 与 Windows 部署包）
├── deploy-linux.sh             Linux 一键部署脚本
├── deploy-windows.ps1          Windows 一键部署脚本
└── README.md                   本文件
```

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.12 · FastAPI 0.146 · SQLAlchemy 2.1（同步） · Alembic · psycopg 3 |
| 数据库 | **SQLite（默认，零依赖）** 或 PostgreSQL 18+（可选） |
| 前端 | React 18 · TypeScript 5 · Ant Design 5 · TanStack Query 5 · zustand · ECharts |
| 构建 | Vite 5 ｜ 包管理器 **pnpm** |
| 认证 | 9 种协议（密码 / OIDC / OAuth2 / CAS / SAML2 / LDAP / 飞书 / 钉钉 / 企业微信） |

## 快速开始

### 1. 数据库

```bash
createdb pm_dev
```

### 2. 后端

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt      # 或按 README 逐个安装
copy .env.example .env               # 修改数据库连接与密钥

$env:PM_DATABASE_URL="postgresql+psycopg://postgres:123@localhost:5432/pm_dev"
alembic upgrade head                 # 建表 + 种子数据
python scripts/seed_admin.py         # 初始化管理员
python scripts/seed_demo.py          # 可选：生成演示数据

uvicorn app.main:app --reload --port 8000
```

接口文档：http://127.0.0.1:8000/docs

### 3. 前端

本项目用 **pnpm** 管理前端依赖（比 npm 快 2-3 倍，磁盘占用少 60%）。

```powershell
# 首次：安装 pnpm
npm install -g pnpm

# 安装依赖
cd frontend
pnpm install

# 启动开发服务器
pnpm dev
```

访问 http://127.0.0.1:5173

生产构建：

```powershell
pnpm build
```

产物在 `frontend/dist/`，由后端托管（`PM_FRONTEND_SERVE=true`）。

**默认账号**

| 角色 | 账号 | 密码 |
|---|---|---|
| 系统管理员 | `admin` | `Admin@123` |
| 教师（演示） | `T001` ~ `T004` | `Test@123` |

## 核心业务模型

### 三级责任体系

```
大项目（学校立项）
  └── 参与学院（多对多，每个学院有负责人）
        └── 子项目（必须同时归属大项目 + 学院，有负责人、计划起止、权重）
```

子项目的 `major_id` 与 `college_id` 均为非空，且学院必须先参与该大项目才能建子项目——这是保证责任到人的硬约束。

### 双日历口径

| 用途 | 口径 |
|---|---|
| 时间进度 | **自然日** |
| 预警计时（停滞、滞后持续） | **有效日**（排除免填期） |

两者必须分开，否则寒暑假会把预警全部误判。

### 进度与偏差

```
时间进度 = (今天 − 计划开始) ÷ (计划结束 − 计划开始) × 100%
偏差     = 实际进度 − 时间进度
```

| 偏差 | 含义 | 对应预警 |
|---|---|---|
| ≤ −25% | 严重滞后 | AL-02（红） |
| < −10% | 进度滞后 | AL-01（橙） |
| > 0 | 超前 | — |

### 11 条预警规则

| 编号 | 类型 | 触发条件 | 等级 | 可关闭 |
|---|---|---|---|---|
| AL-01 | 进度滞后 | 偏差 < −10% 且持续 3 个有效日 | 橙 | ✅ |
| AL-02 | 严重滞后 | 偏差 ≤ −25% | 红 | ✅ |
| AL-03 | 进度停滞 | 超过 21 个有效日未更新 | 橙 | ✅ |
| AL-04 | 进度回调 | 本次进度低于上次 | 黄 | ✅ |
| AL-05 | 项目临期 | 距结束 < 30 天且进度 < 80% | 红 | ✅ |
| AL-06 | 报表逾期 | 补填期结束后仍未提交 | 黄 | ✅ |
| AL-07 | 连续未填 | 连续 3 个周期未提交报表 | 橙 | ✅ |
| AL-08 | 风险超期 | 超过计划闭环时间未闭环 | 橙 | ✅ |
| AL-09 | 责任空缺 | 学院或子项目无负责人 | 红 | ❌ |
| AL-10 | 计划缺失 | 进行中但无计划起止时间 | 橙 | ❌ |
| AL-11 | 长期零进度 | 连续 60 个有效日进度为 0 | 橙 | ✅ |

AL-09 / AL-10 只能通过补齐数据自动关闭，不能手工关闭或忽略。

### 权限模型

功能权限：`模块:页面:操作` 三级编码（38 个权限点）

数据范围（`sys_user_role` 三元组）：

| scope_type | 含义 |
|---|---|
| `ALL` | 全校 |
| `MAJOR` | 所辖大项目 |
| `COLLEGE` | 本学院 |
| `SELF` | 本人 |
| `CUSTOM` | 自定义（带有效期） |

一个用户可有多条授权，实现「同一人在不同项目身份不同」。

## 已实现功能

| 模块 | 接口数 | 说明 |
|---|---|---|
| 认证 | 14 | 9 种协议，配置入库，密钥 Fernet 加密 |
| 项目台账 | 18 | 学院 / 大项目 / 学院分工 / 子项目 / 完整性检查 |
| 进度管控 | 7 | 填报留痕 / 进度总表 / 汇总 / 每日快照 |
| 预警管理 | 11 | 扫描 / 去重 / 状态机 / 升级 / 统计 |
| 报表管理 | 9 | 日周月报 / 周报初稿 / 更正作废 / 及时率 |
| 数据导出 | 4 | Excel / PDF（中文字体） |
| 系统管理 | 15 | 用户 / 角色 / 授权 / 参数 / 日历 / 审计 |
| 风险登记 | 5 | 登记 / 闭环 / 超期识别 |

## 数据库

27 张表，6 个 Alembic 迁移：

| 迁移 | 内容 |
|---|---|
| `0001_init` | 24 张业务表 + 种子（7 角色 / 38 权限 / 33 参数） |
| `0002_auth` | 3 张认证表 + 9 种协议初始化 |
| `0003_alert_nullable` | `alert_action.operator_id` 允许为空 |

## 部署

生产环境参考 `frontend/README.md` 的 Nginx 配置，要点：

- 前端 `try_files $uri $uri/ /index.html`（history 路由）
- `/api/` 反向代理到后端，**必须转发 `X-Forwarded-Proto` / `X-Forwarded-Host`**（用于拼接 OIDC/CAS 回调地址）
- 定时任务用 `PM_SCHEDULER_ENABLED=true`，多实例部署时靠 PG advisory lock 去重

## 待确认事项

需求文档 V1.4 第 6 章列出 42 条需校方确认的事项，其中甲类（需当场定）3 条：

- 第 2 条：子项目是否允许跨学院
- 第 5 条：进度回调是否需上级审批
- 第 6 条：结项是否需验收通过

其余为业务边界与流程细节，详见 `doc/产教融合项目管理系统-需求方案说明书-V1.4.docx`。
