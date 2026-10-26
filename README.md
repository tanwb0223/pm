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
├── backend/                    FastAPI + SQLite/PostgreSQL 后端（105 个 API）
├── frontend/                   React 18 + TypeScript + Ant Design 5 前端（22 个页面）
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
copy .env.example .env               # 修改数据库连接

# 【必做】生成 JWT 密钥写入 .env 的 PM_SECRET_KEY（至少 32 位随机串）
python -c "import secrets; print(secrets.token_urlsafe(48))"
# 未设置或仍是占位值时，非 PM_DEBUG 模式下应用会拒绝启动

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

### 基础数据从哪录入

上线顺序：**先录组织（学院）→ 再录人员 → 再建大项目 / 子项目**。学院是所有基础数据的根，学院为空时下游全部选不到。

| 要录什么 | 入口 | 说明 |
|---|---|---|
| 学院（二级学院 / 下级组织） | 系统管理 → **组织管理**（`/system/org`） | 单个新增 / 编辑 / 删除，或「批量导入」用 Excel 模板一次导完 |
| 人员名单 | 系统管理 → 用户管理 → 批量导入 | 模板里「学院」列按已有学院名称或编码匹配，因此**必须先建学院** |
| 大项目 | 项目管理 → 大项目 → 新建项目 | 合作企业、建设周期直接填；参与学院在大项目详情里添加 |
| 学院分工 / 负责人 | 项目管理 → 大项目 → 详情 → 添加学院 | 学院必须先参与项目，才能在其下建子项目 |
| 子项目 | 项目管理 → 子项目 → 新建 | 必须同时选大项目 + 该大项目已参与的学院 |
| 角色授权 | 系统管理 → 角色权限 → 授权 | 角色 + 数据范围（全校 / 大项目 / 学院 / 本人） |

接口层同样具备：`GET/POST/PUT/DELETE /api/colleges`、`POST /api/system/import/colleges`（权限点 `system:org`）、`GET /api/system/import/templates/college` 下载导入模板。

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
| 认证 | 16 | 9 种协议，配置入库，登录失败锁定（落库可解锁），一次性下载票据 |
| 项目台账 | 20 | 学院 / 大项目 / 学院分工 / 子项目 / 结项确认 / 结项异议 / 变更留痕 / 完整性检查 |
| 进度管控 | 13 | 填报留痕 / 进度总表 / 汇总 / 趋势 / 更新及时率 / 每日快照 / 催办与催办统计 |
| 预警管理 | 12 | 扫描 / 去重 / 状态机 / 处置措施 / 升级 / 统计 |
| 报表管理 | 15 | 日周月报 / 周报初稿 / 更正与更正放行 / 作废 / 逾期补填 / 及时率 |
| 站内消息 | 4 | 我的消息 / 未读数 / 标记已读 / 全部已读 |
| 数据导出 | 4 | Excel / PDF（中文字体）+ 报表及时率列 |
| 系统管理 | 27 | 组织 / 用户 / 角色 / 授权 / 参数 / 日历 / 审计 / 通知日志 / 任务记录 / 备份 / 存储运维 / 批量导入 |
| 风险登记 | 5 | 登记 / 闭环 / 超期识别 |
| 附件管理 | 9 | 上传 / 下载 / 预览 / 软删除 / 存储统计 / 孤儿清理 |

> 接口总数 **105**（`app.openapi()` 实测）。每条业务链路都要求「入口 → 数据 → 状态 → 反馈」闭环，
> 详细的分级修复清单见 `doc/闭环缺口汇总-20261024.md` 与 `doc/修复记录-20261024-闭环.md`。

## 数据库

26 张表，8 个 Alembic 迁移：

| 迁移 | 内容 |
|---|---|
| `0001_init` | 24 张业务表 + 种子（7 角色 / 40 权限 / 41 参数） |
| `0002_auth` | 3 张认证表 + 9 种协议初始化 |
| `0003_alert_action_nullable` | `alert_action.operator_id` 允许为空 |
| `0004_attachment` | 附件表增强（哈希 / 软删除 / 下载计数 / `stored_path`） |
| `0005_role_perms` | 按角色矩阵补齐权限（修正"填报人没有填报权限"） |
| `0006_password_selfservice` | 自助改密：`must_change_password` / `password_updated_at` |
| `0007_report_grace` | 逾期补填放行表 `report_grace` |
| `0008_closure_fixes` | 消息中心 `sys_message`、任务记录 `sys_job_run`、登录限流落库、报表代填、更正放行、日报按项目、结项异议、留痕事件类型、预警详情、权限与参数补齐 |

> SQLite 部署不走迁移：启动时 `create_all` 建表 + 自动写入版本标记；
> 版本落后时自动执行增量迁移，已知的结构漂移（如 `alert_action.operator_id`、
> `0008` 新增的列）由 `app/db/alembic_sync.repair_sqlite_schema()` 自动修复。
>
> 索引必须声明在模型 `__table_args__` 里（`Index(...)`），不能只写在迁移中：
> SQLite 走 `create_all`，迁移里的 `create_index` 不会执行。存量库启动时由
> `ensure_indexes()` 按 inspector 比对补齐，幂等。
>
> **权限点以 `app/core/permissions.py` 的 `ALL_PERMISSIONS` + `PERMISSION_META` 为唯一来源**，
> 种子与迁移都从它生成（历史上手写清单漏过 `report:grant`，导致全新部署没人能开逾期补填）。

## 部署

### Docker（推荐：离线部署只需拷镜像）

`docker/` 提供完整的 Docker Compose 方案：

- 有网机器构建离线包：`bash docker/build-offline.sh`，Windows 用 `powershell -ExecutionPolicy Bypass -File docker\build-offline.ps1`
- 目标机：解压 → 修改 `.env` → `sudo bash deploy.sh`
- 离线包约 **70 MB**（含 app + Caddy 镜像），目标机不需要源码、Python、Node、外网
- 域名、证书、SQLite 数据目录、附件目录都在 `.env` 配置
- 日常运维用 `./pmctl.sh status|logs|backup|restore|upgrade`

详见 [`docker/README.md`](docker/README.md)。

### 原生部署（systemd + Nginx）

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
