# 产教融合项目管理系统 · 后端

FastAPI + PostgreSQL + SQLAlchemy 2.0

## 目录结构

```
backend/
├── alembic.ini
├── .env.example
├── alembic/
│   └── versions/
│       ├── 0001_init.py        24 张业务表
│       └── 0002_auth.py        认证配置表（3 张）
└── app/
    ├── main.py                 应用入口
    ├── core/
    │   ├── config.py           配置（pydantic-settings）
    │   ├── security.py         密码哈希 + JWT
    │   ├── deps.py             当前用户、权限校验、数据范围
    │   ├── exceptions.py       业务异常
    │   └── response.py         统一响应
    ├── db/session.py           引擎与会话
    ├── models/
    │   ├── system.py           组织/用户/角色/权限/参数/审计
    │   └── auth.py             认证配置/登录态/身份绑定
    ├── auth/                   ★ 多协议认证（参考 Octop 架构）
    │   ├── kinds.py            协议清单 + 适配器 Protocol
    │   ├── crypto.py           密钥加密（Fernet）
    │   ├── flow.py             PKCE / state / nonce
    │   ├── service.py          登录编排
    │   └── providers/          9 种协议适配器
    └── api/v1/auth.py          认证 API
```

## 快速开始

```powershell
# 1. 虚拟环境
python -m venv .venv
.\.venv\Scripts\activate

# 2. 依赖
pip install "fastapi>=0.115" "uvicorn[standard]" "sqlalchemy>=2.0" "alembic>=1.13" `
            "psycopg[binary]>=3.2" "pydantic-settings" "pyjwt[crypto]" `
            "argon2-cffi" "ldap3" "httpx" "python-multipart"

# 3. 配置
copy .env.example .env
# 编辑 .env，填 PM_DATABASE_URL 和 PM_SECRET_KEY

# 4. 建表
$env:PM_DATABASE_URL="postgresql+psycopg://postgres:123@localhost:5432/pm_dev"
alembic upgrade head

# 5. 启动
$env:PM_DEBUG="true"
uvicorn app.main:app --reload --port 8000
```

打开 http://127.0.0.1:8000/docs 看接口文档（需 `PM_DEBUG=true`）。

## 认证架构（参考 Octop）

### 设计要点

1. **适配器模式**：每种协议实现统一的 `AuthProvider` Protocol，通过注册表按 `kind` 取用
2. **配置入库**：认证参数存 `auth_provider` 表，管理员在后台改配置即可切换，不改代码
3. **密钥加密**：`client_secret`、LDAP 密码用 Fernet 加密后存库，密钥来自 `PM_SECRET_KEY`
4. **登录流程**：state + nonce + PKCE（S256），登录态存 `auth_login_state`，10 分钟过期
5. **一次性登录码**：回调成功后发 60 秒有效的 login_code，前端换 JWT
6. **身份绑定**：`auth_identity` 表记录第三方 subject，一个用户可绑定多个第三方账号

### 支持的 9 种协议

| kind | 协议 | 流程 | 典型场景 |
|---|---|---|---|
| `password` | 本地账号密码 | 表单直连 | 一期默认 |
| `oidc` | OpenID Connect | 跳转 + PKCE | Keycloak / Authing / Casdoor / Azure AD |
| `oauth2` | 通用 OAuth2 | 跳转 | GitHub / Gitee / 自建 |
| `cas` | CAS 2.0/3.0 | 跳转 + ticket | **高校最常用** |
| `saml2` | SAML 2.0 | HTTP-Redirect + HTTP-POST | 部分高校统一认证 |
| `ldap` | LDAP / AD 域 | 表单直连 | 校内域账号 |
| `feishu` | 飞书 | 跳转 | 飞书办公 |
| `dingtalk` | 钉钉 | 跳转 | 钉钉办公 |
| `wecom` | 企业微信 | 跳转 | 企业微信 |

### 登录流程

**密码 / LDAP（表单直连）**
```
POST /api/auth/login            → JWT
POST /api/auth/login/ldap       → JWT
```

**跳转类（OIDC / CAS / SAML / OAuth2 / 三家办公平台）**
```
GET  /api/auth/login/{kind}/start          → {authorization_url, state}
     （前端跳转到 authorization_url）
GET  /api/auth/{kind}/callback?code&state  → {login_code, redirect_after}
POST /api/auth/token  {code: login_code}   → JWT
```

> 用「一次性登录码」而不是回调直接发 JWT，是为了避免 token 出现在 URL 里被日志/Referer 泄露。

### 配置某个认证方式

```http
PUT /api/auth/admin/providers/oidc
{
  "enabled": true,
  "display_name": "学校统一认证",
  "issuer": "https://sso.school.edu.cn",
  "client_id": "pm-client",
  "client_secret": "xxxxx",
  "scopes": "openid profile email",
  "auto_create_user": true,
  "default_role_id": 7
}
```

各协议的 `extra` 字段：

| kind | extra 必填项 |
|---|---|
| `oidc` | 无（走 discovery） |
| `oauth2` | `authorize_url`、`token_url`、`userinfo_url` |
| `cas` | 用 `server_url` |
| `saml2` | `idp_sso_url`、`sp_entity_id` |
| `ldap` | `server_url`、`base_dn`、`user_filter`、可选 `bind_dn` |
| `feishu` / `dingtalk` | 用 `client_id` + `client_secret` |
| `wecom` | `extra.corp_id`、`extra.agent_id` + `client_secret` |

连通性测试：`POST /api/auth/admin/providers/{kind}/test`

### 用户字段映射

不同协议返回的字段名不同，在 `extra` 里可指定：

```json
{"claim_username": "employeeNo", "claim_name": "cn", "claim_email": "mail"}
```

不指定时按内置优先级兜底（`staff_no` → `employee_id` → `uid` → `preferred_username` → `sub`）。

## 数据权限模型

### 一个 token 表达不了「同一人在不同项目身份不同」

所以授权是三元组：`(user_id, role_id, scope_type, scope_id)`

| scope_type | scope_id | 含义 |
|---|---|---|
| `ALL` | NULL | 全校 |
| `MAJOR` | 大项目 id | 所辖大项目 |
| `COLLEGE` | 学院 id | 本学院 |
| `SELF` | NULL | 本人 |
| `CUSTOM` | NULL | 自定义（配 valid_from/valid_to） |

### 使用方式

```python
from app.core.deps import require_perm

@router.post("/project/sub")
def create_sub(
    body: SubIn,
    user: CurrentUser = Depends(require_perm("project:sub:edit")),
):
    ...
```

列表查询必须经过数据范围过滤：

```python
from app.core.scope import apply_scope

stmt = select(ProjSub)
stmt = apply_scope(stmt, user, ProjSub)   # 自动按 scope 过滤
```

## 已验证

在 PostgreSQL 18.6 上实测：

| 项 | 结果 |
|---|---|
| `alembic upgrade head` | ✅ 24 + 3 张表 |
| 密码登录 | ✅ 返回 JWT |
| 错误密码 | ✅ 400，不暴露用户是否存在 |
| `/api/auth/me` | ✅ 返回角色、38 个权限、数据范围 |
| 未带 token | ✅ 401 |
| 认证方式配置读写 | ✅ 密钥加密存储 |
| 启用后公开列表 | ✅ 只返回已配置的 |
| 第三方不可达 | ✅ 转为 400 业务错误，不抛 500 |

## 下一步

- [ ] M1 台账模块：学院、用户、大项目、子项目 CRUD
- [ ] 数据范围过滤（`app/core/scope.py`）
- [ ] M2 进度模块：填报、留痕、汇总、偏差
- [ ] M3 预警引擎：11 条规则

---

## M1 项目台账（已完成）

### 接口一览

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| GET | `/api/colleges` | 学院列表 | 登录 |
| POST | `/api/colleges` | 新增学院 | `system:org` |
| PUT | `/api/colleges/{id}` | 编辑学院 | `system:org` |
| DELETE | `/api/colleges/{id}` | 删除学院（有项目 / 子项目 / 用户 / 下级组织时拒绝） | `system:org` |
| GET | `/api/majors` | 大项目分页列表 | 登录 + 数据范围 |
| GET | `/api/majors/{id}` | 大项目详情（含学院分工） | 登录 + 数据范围 |
| POST | `/api/majors` | 新建大项目 | `project:major:edit` |
| PUT | `/api/majors/{id}` | 编辑大项目 | `project:major:edit` |
| DELETE | `/api/majors/{id}` | 删除大项目（软删） | `project:major:edit` |
| GET | `/api/majors/{id}/colleges` | 参与学院列表 | 登录 |
| POST | `/api/majors/{id}/colleges` | 添加学院 / 指定负责人 | `project:college:edit` |
| DELETE | `/api/majors/{id}/colleges/{cid}` | 移除学院 | `project:college:edit` |
| GET | `/api/subs` | 子项目分页列表 | 登录 + 数据范围 |
| GET | `/api/subs/{id}` | 子项目详情 | 登录 |
| POST | `/api/subs` | 新建子项目 | `project:sub:edit` |
| PUT | `/api/subs/{id}` | 编辑子项目（含状态迁移校验） | `project:sub:edit` |
| DELETE | `/api/subs/{id}` | 删除子项目（软删） | `project:sub:edit` |
| GET | `/api/majors/{id}/completeness` | 责任与计划完整性检查 | 登录 |

### 已落地的业务规则

| 规则 | 来源 | 实现 |
|---|---|---|
| 子项目必须同时归属大项目和学院 | 文档 2.1 | `proj_sub` 两字段 NOT NULL |
| 学院必须先参与项目，才能建子项目 | 文档 2.1 | `create_sub` 校验 `proj_major_college` |
| 每个学院、每个子项目必须有负责人 | 文档 2.1 / AL-09 | 完整性检查 |
| 必须有计划起止时间 | 文档 4.2 / AL-10 | 完整性检查 |
| 原始计划立项时锁定，编辑不改 | 第 38 项 | `original_*` 字段独立于 `plan_*` |
| 状态迁移受控 | 第 17、19 项 | `_ALLOWED_TRANSITIONS` |
| 结项需进度 100% | 文档 6.1 第 6 项 | `update_sub` 校验 |
| 已结项子项目不能删除 | 文档 6.1 第 6 项 | `delete_sub` 校验 |
| 学院下有子项目时不能移除/删除 | 一致性 | `remove_major_college` / `delete_college` |
| 学院编码全局唯一（编辑时排除自身） | 一致性 | `create_college` / `update_college` |
| 上级组织必须存在、不能自引用、不能成环 | 一致性 | `_check_parent_college` |
| 学院仍被用户 / 子项目 / 下级组织引用时不能删除 | 一致性 | `delete_college` 四项前置校验 |
| 数据范围过滤 | 3.5 / 第 30 项 | `apply_scope` 统一注入 |

### 状态迁移图

```
NOT_STARTED ──► IN_PROGRESS ──► CLOSED（需 100%）
     │               │
     └──► ABORTED ◄──┘
            │
            └──► IN_PROGRESS（重启）
```

`CLOSED` 是终态，不能回到任何状态。

### 实测结果（端到端）

| 场景 | 结果 |
|---|---|
| 建 3 个学院 | ✅ |
| 建大项目（原始计划锁定） | ✅ |
| 完整性检查（无学院无子项目） | ✅ 报 2 项 |
| 添加参与学院（含指定负责人） | ✅ |
| 建 3 个子项目 | ✅ |
| **给未参与学院建子项目** | ✅ 正确拒绝（400） |
| 完整性检查（缺负责人 + 缺计划） | ✅ 报 2 项 |
| 补计划后复检 | ✅ 只剩 1 项 |
| 列表分页查询 | ✅ 大项目 1 条、子项目 3 条 |
| **编辑时试图改原始计划** | ✅ 被忽略，仍为立项值 |

### 初始化管理员

```powershell
python scripts/seed_admin.py admin Admin@123
```

幂等，可重复执行（会重置密码）。

## 下一步

- [ ] M2 进度模块：填报、留痕、汇总计算、偏差、进度总表
- [ ] M3 预警引擎：11 条规则

---

## M2 进度管控（已完成）

### 接口一览

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| POST | `/api/subs/{id}/progress` | 更新进度（自动留痕） | `progress:update` |
| GET | `/api/subs/{id}/records` | 进度变更留痕 | 登录 |
| GET | `/api/subs/{id}/view` | 进度视图（时间进度/偏差/停滞） | 登录 |
| GET | `/api/progress/table` | 进度总表（默认按偏差排序） | 登录 + 数据范围 |
| GET | `/api/progress/major/{id}` | 大项目汇总（含学院分解） | 登录 |
| GET | `/api/progress/school` | 全校汇总 | 登录 |
| POST | `/api/progress/snapshot` | 生成每日快照 | `system:config` |

### 核心算法

**时间进度**（文档 4.2，自然日）
```
时间进度 = (今天 − 计划开始) ÷ (计划结束 − 计划开始) × 100%
截断到 [0, 100]；未设计划时间返回 None（触发 AL-10）
```

**偏差值**
```
偏差 = 实际进度 − 时间进度
```

**汇总**（文档 4.1 + 第 19/20/21 项）
```
大项目进度 = Σ(子项目进度 × 权重) ÷ Σ权重
学院进度   = 该学院在该大项目内子项目按权重加权
全校进度   = 各大项目等权平均
```

分母口径：

| 状态 | 是否计入 | 按多少计 |
|---|---|---|
| `IN_PROGRESS` | ✅ | 当前值 |
| `CLOSED` | ✅ | 固定 100% |
| `NOT_STARTED` | ❌ | — |
| `ABORTED` | ❌ | — |

Σ权重 = 0（或全未设）时退化为等权。

### 双日历（关键设计）

文档要求两套时间口径，**必须分开**：

| 用途 | 口径 | 实现 |
|---|---|---|
| 时间进度 | **自然日** | `time_progress()` |
| 停滞/零进度/滞后持续 | **有效日**（排除免填期） | `CalendarService.effective_days_between()` |

免填日启动时加载进内存 Set，不查库。

### 填报规则

| 规则 | 行为 |
|---|---|
| 进度回调（新值 < 旧值） | 必须填 `rollback_reason`，否则 400 |
| 「本周期无变化」 | 写留痕但**不修改**当前进度，与「没更新」区分 |
| 代填 | 操作人 ≠ 负责人时记 `on_behalf_of` |
| 已结项/已中止 | 拒绝更新 |
| 首次填报 | 自动从 `NOT_STARTED` 转 `IN_PROGRESS` |

### 每日快照

`POST /api/progress/snapshot`（生产环境由定时任务每日 01:00 触发）

每个进行中的子项目写一条 `prog_snapshot`，含时间进度、偏差、未更新天数、是否免填日。

**快照让「连续 N 天」判定变成纯 SQL**：

```sql
-- AL-01：连续 3 个有效日偏差 < -10%
SELECT sub_id, COUNT(*) FROM prog_snapshot
WHERE deviation < -10 AND is_free_day = false
  AND snap_date > CURRENT_DATE - INTERVAL '7 day'
GROUP BY sub_id HAVING COUNT(*) >= 3;
```

### 实测结果

| 场景 | 结果 |
|---|---|
| 填报 A=30%、B=80% | ✅ |
| 「本周期无变化」不改当前值 | ✅ |
| 回调无原因被拒 / 有原因通过 | ✅ 400 / 200 |
| 时间进度（过半场景） | ✅ 50% |
| 偏差计算 | ✅ A=-20、B=+59 |
| **加权汇总** (30×3+70×1)/4 | ✅ **40%，与手算一致** |
| 学院分解 | ✅ {物流:30, 运输:70} |
| 全校汇总 | ✅ 40% |
| 进度总表排序（最滞后在前） | ✅ 教材-50 → 场地-20 → 课程+59 |
| 只看异常 | ✅ 2 条 |
| 留痕区分「无变化」与「正常填报」 | ✅ |
| 免填日：自然日 10 天 → 有效日 7 天 | ✅ |
| `add_effective_days(today, 8)` | ✅ 跳过 3 个免填日 |
| 时间进度边界（未开始 0 / 已结束 100 / 无计划 None） | ✅ |
| 快照生成 + 幂等重跑 | ✅ created=3 → 0 |
| **AL-01 连续 3 天判定** | ✅ 正确命中 2 个子项目 |
| **AL-02 严重滞后判定** | ✅ 正确命中 |
| AL-05 临期判定 | ✅ 逻辑正确（测试数据未命中） |

## 下一步

- [ ] M3 预警引擎：11 条规则、去重、状态机、通知
- [ ] M4 报表模块

---

## M3 预警引擎（已完成）

### 接口一览

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| POST | `/api/alerts/scan` | 执行预警扫描 | `system:config` |
| POST | `/api/alerts/escalate` | 执行超时升级 | `system:config` |
| GET | `/api/alerts` | 预警列表 | 登录 + 数据范围 |
| GET | `/api/alerts/grouped` | 折叠聚合（大项目→学院） | 登录 |
| GET | `/api/alerts/stats` | 统计与按时处置率 | 登录 |
| GET | `/api/alerts/{id}` | 预警详情（含处置流水） | 登录 |
| POST | `/api/alerts/{id}/claim` | 认领 | 登录 |
| POST | `/api/alerts/{id}/close` | 关闭 | 登录 |
| POST | `/api/alerts/{id}/ignore` | 忽略（必填理由） | 登录 |
| POST | `/api/alerts/{id}/transfer` | 转交 | 登录 |
| GET | `/api/dashboard/todo` | 我的督办 | 登录 |

### 11 条规则（策略模式，一条一个类）

| 编号 | 类型 | 触发条件 | 等级 | 触发方式 | 可关闭 |
|---|---|---|---|---|---|
| AL-01 | 进度滞后 | 偏差 < −10% 且持续 3 个有效日 | 橙 | 每日 | ✅ |
| AL-02 | 严重滞后 | 偏差 ≤ −25% | 红 | 每日 | ✅ |
| AL-03 | 进度停滞 | 超过 21 个有效日未更新 | 橙 | 每日 | ✅ |
| AL-04 | 进度回调 | 本次进度低于上次 | 黄 | **事件** | ✅ |
| AL-05 | 项目临期 | 距结束 < 30 天且进度 < 80% | 红 | 每日 | ✅ |
| AL-06 | 报表逾期 | 补填期结束后仍未提交 | 黄 | 每小时 | ✅ |
| AL-07 | 连续未填 | 连续 3 个周期未提交报表 | 橙 | 每日 | ✅ |
| AL-08 | 风险超期 | 超过计划闭环时间未闭环 | 橙 | 每日 | ✅ |
| AL-09 | 责任空缺 | 学院未指定负责人 或 子项目无负责人 | 红 | 每日 | ❌ |
| AL-10 | 计划缺失 | 进行中但未设计划起止时间 | 橙 | 每日 | ❌ |
| AL-11 | 长期零进度 | 连续 60 个有效日进度为 0 | 橙 | 每日 | ✅ |

### 三种触发方式

| 方式 | 规则 | 实现 |
|---|---|---|
| **事件驱动** | AL-04 | 填报回调时同步调用 `handle_rollback()` |
| **每日扫描** | AL-01/02/03/05/07/08/09/10/11 | `POST /api/alerts/scan`（定时任务 01:00） |
| **每小时扫描** | AL-06 | 同上（定时任务每小时） |

### 去重合并（文档 3.3）

```
dedup_key = "{sub_id}:{alert_type}"
窗口 = 7 天
同 key 且状态为 PENDING/CLAIMED → merged_count += 1，不新建、不重复推
```

实测：连续扫描两次，第一次生成 8 条，第二次 **新建 0 条、合并 7 条**。

### 状态机

```
PENDING ──认领──► CLAIMED ──关闭──► CLOSED
   │                  │
   ├──忽略(必填理由)──► IGNORED
   │                  └──转交──► 仍 CLAIMED，换 claimed_by（时限不重算）
   └──超时未认领──► 追加通知（状态不变，标记 escalated_at）
```

**AL-09 / AL-10 硬校验**：不能关闭、不能忽略，只能靠补齐数据自动关闭。

### 接收人兜底链（AL-09 报的就是"没人"）

```
子项目无负责人 → 学院负责人 → 大项目负责人 → 系统管理员
```

### 通知策略

| 等级 | 站内 | 邮件 | 说明 |
|---|---|---|---|
| 红色 | ✅ 实时 | ✅ 实时 | 最高优先级 |
| 橙色 | ✅ 进每日摘要 | ✅ | |
| 黄色 | ✅ 仅提示 | ❌ | 不进待办 |

`notify_log` 记录每条通知的渠道、接收人、状态，用于「以邮件发出成功作为送达认定」。

### 超时升级

`POST /api/alerts/escalate`，扫描 `status=PENDING` 且 `deadline_at < now` 且未升级过的预警：

- 追加通知大项目负责人
- 只增加通知对象与督办权，**不改变预警状态，处置权仍属原责任人**
- 标记 `escalated_at` 防止重复升级

### 统计口径（文档 4.3）

**预警按时处置率** = 按期关闭数 ÷ 应处置数

- 分母：不含黄色预警、不含已忽略，**含已升级**
- 按时：`closed_at <= deadline_at`

### 实测结果

| 场景 | 结果 |
|---|---|
| 造 3 天快照后扫描 | ✅ 命中 AL-01×2、AL-02×2、AL-05×1、AL-06×1、AL-07×1、AL-09×1 |
| 生成 8 条预警 | ✅ |
| 折叠聚合 | ✅ 红4 橙3 黄1 |
| 认领 → 关闭 | ✅ |
| 重复认领 | ✅ 400 |
| **AL-09 关闭/忽略** | ✅ 均 400 拒绝 |
| 忽略空理由 | ✅ 422（Pydantic 校验） |
| 忽略有理由 | ✅ 200 |
| **重复扫描去重** | ✅ 新建 0、合并 7 |
| 统计与及时率 | ✅ 12.5%（1/8） |
| 我的督办 | ✅ 待处置 8、滞后 2 |
| **超时升级** | ✅ 8 条全部标记 |
| 系统自动动作无操作人 | ✅ 迁移 0003 修正 |

### 迁移

`0003_alert_nullable`：`alert_action.operator_id` 改为可空（系统自动动作没有操作人）。

## 下一步

- [ ] M4 报表模块（日/周/月报、更正作废、及时率）
- [ ] 定时任务（APScheduler：01:00 快照+扫描、每小时报表扫描、08:30 每日摘要）
- [ ] 导出（Excel / PDF）

---

## M4 报表模块（已完成）

### 接口一览

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| POST | `/api/reports` | 提交报表（日/周/月） | `report:fill` |
| GET | `/api/reports` | 报表列表 | 登录 + 数据范围 |
| GET | `/api/reports/{id}` | 报表详情 | 登录 |
| POST | `/api/reports/{id}/void` | 作废（须填理由） | `report:void` |
| POST | `/api/reports/weekly-draft` | 生成周报初稿 | 登录 |
| GET | `/api/reports/pending` | 待填清单 | 登录 |
| GET | `/api/reports/timeliness` | 报表及时率 | 登录 |
| GET | `/api/reports/period` | 查询周期与截止时间 | 登录 |

### 一张表存三种报表

`report.content` 用 jsonb，按类型存不同字段：

```json
// DAILY
{"today": "...", "tomorrow": "...", "issues": "..."}
// WEEKLY
{"summary": "...", "next_week": "...", "issues": "..."}
// MONTHLY
{"completed": "...", "next_month": "...", "issues": "...", "suggestions": "..."}
```

唯一约束 `(sub_id, type, period_start)` 保证同期只有一条。

### 周期与截止时间（文档 4.3）

| 类型 | 周期 | 截止时间 |
|---|---|---|
| 日报 | 自然日 | 当日 24:00 |
| 周报 | 周一~周日 | 次周一 12:00 |
| 月报 | 自然月 | 次月第 3 个工作日 18:00 |

**应填判定**：
- 子项目必须进行中（已结项且结项日早于该期 → 不要求）
- 未到计划开始日 → 不要求
- 日报：当天是免填日 → 不要求
- 周报/月报：当期工作日 ≥ 1 天

### 周报初稿自动生成

从「本周期进度记录 + 上期周报未完成计划」拼成（文档 3.4）：

```json
{
  "summary": "10-19：20%（开工）\n10-19：35%（基础完成）",
  "next_week": "（取上期周报的 next_week）",
  "progress": 35,
  "progress_delta": 15
}
```

实测：正确拼出两条进度记录，delta 计算正确。

### 更正与作废（文档 3.4）

| 操作 | 规则 |
|---|---|
| 3 个自然日内 | 填报人可自行更正，`correct_count++` |
| 超过 3 天 | 需 `project:sub:edit` 权限（学院负责人及以上） |
| 作废 | 仅 `report:void`（系统管理员），必须填理由 |
| 作废后果 | 从及时率剔除；该报表触发的未处置 AL-06/AL-07 预警自动关闭 |

### 报表及时率（文档 4.3）

```
及时率 = 按期提交数 ÷ 应提交数
```

- **分子**：按期提交（`submitted_at <= deadline`），不含逾期补填
- **分母**：应提交期次，**只统计截止时间已过的期次**，不含免填、已作废
- **作废的处理**：该期重算为「缺交」

**关键边界**：截止时间未到的当期**不计入分母**（否则永远算不完）。

### 实测结果

| 场景 | 结果 |
|---|---|
| 周期计算（日/周/月） | ✅ 周报 10-19~10-25 截止 10-26 12:00；月报截止次月第 3 工作日 18:00 |
| 周报初稿生成 | ✅ 正确拼出进度记录，delta=15 |
| 提交带进度同步 | ✅ 子项目进度同步为 40% |
| 3 天内自更正 | ✅ correct_count=1 |
| 作废空理由 | ✅ 422 |
| 作废有理由 | ✅ VOIDED，记录 void_reason |
| 待填清单 | ✅ |
| **及时率（周报）** | ✅ 应 4 期：按期 1、迟交 1、缺交 2 → 25.0% |
| **及时率（含月报）** | ✅ 应 5 期：按期 1、迟交 1、缺交 3 → 20.0% |
| **未到期期次不计入分母** | ✅ 修正后正确 |

---

## 定时任务（已完成）

`app/jobs/scheduler.py`，APScheduler + **PG advisory lock**（防多实例重复执行）。

| 任务 | 时间 | 内容 |
|---|---|---|
| `daily_snapshot_scan` | 每日 01:00 | 生成进度快照 + 预警扫描 |
| `escalate` | 每 30 分钟 | 超时预警升级 |
| `hourly_scan` | 每小时 :05 | 报表逾期扫描（AL-06） |
| `daily_digest` | 每日 08:30 | 每日摘要（预留） |

启用：`.env` 中 `PM_SCHEDULER_ENABLED=true`

实测：4 个任务全部注册成功并正常启停。

## 下一步

- [ ] 导出（Excel / PDF）
- [ ] 风险登记 CRUD
- [ ] 系统管理（用户/角色/参数/日历）
- [ ] 前端

---

## 导出模块（已完成）

### 接口一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/export/majors` | 导出项目台账（xlsx/pdf） |
| GET | `/api/export/progress` | 导出进度报表（xlsx/pdf） |
| GET | `/api/export/reports` | 导出报表材料（xlsx/pdf） |
| GET | `/api/export/fields` | 可导出字段清单（供前端勾选） |

### 技术选型调整

**WeasyPrint 在 Windows 上装不上**（缺少 GTK 的 `libgobject-2.0-0`），改用 **reportlab**：

| | WeasyPrint | reportlab |
|---|---|---|
| 系统依赖 | 需 GTK/Pango/cairo | **纯 Python，无系统依赖** |
| 中文 | CSS `@font-face` | 显式 `TTFont` 注册 |
| 部署 | Windows 麻烦，Linux 需装包 | 任何平台 `pip install` 即可 |

对高校项目来说这是**正确选择**——学校机房多为 Windows，装 GTK 是负担。

### PDF 中文字体（最大的坑）

reportlab 内置字体**不含中文**，必须显式注册 TTF：

```python
pdfmetrics.registerFont(TTFont("PMCJK", "C:/Windows/Fonts/simhei.ttf"))
```

字体查找顺序（自动降级）：

```
backend/fonts/simhei.ttf     ← 推荐：随项目发布，不依赖系统
backend/fonts/simsun.ttc
C:/Windows/Fonts/simhei.ttf
C:/Windows/Fonts/msyh.ttc
/usr/share/fonts/.../NotoSansCJK-Regular.ttc
/usr/share/fonts/.../wqy-zenhei.ttc
```

**生产部署建议**：把 `simhei.ttf` 放进 `backend/fonts/`，这样 Windows/Linux 都能用。

### Excel 导出特性

- 标题行 + 导出时间
- 表头深蓝底白字、冻结首行
- 自动筛选、列宽自适应
- 边框、斑马纹（PDF）
- **字段可勾选**：`?fields=name,type,owner_name`

### 字段清单

| 类型 | 可用字段 |
|---|---|
| 项目台账 | 项目名称、类型、合作企业、建设周期、总负责人、状态、计划起止、参与学院数、子项目数、进度、待完善项 |
| 进度报表 | 所属项目、学院、子项目、负责人、计划起止、时间进度、实际进度、偏差、状态、最近更新、未更新天数 |
| 报表材料 | 子项目、报表类型、周期、进度、状态、提交时间、更正次数、内容摘要 |

### 实测结果

| 场景 | 结果 |
|---|---|
| 字段清单接口 | ✅ 3 类字段 |
| 项目台账 Excel | ✅ 5.8KB，中文正常 |
| 进度报表 Excel | ✅ 5.9KB |
| **进度报表 PDF** | ✅ 20KB，**中文完全正常**（宋体/黑体渲染无乱码） |
| 报表材料 Excel | ✅ 5.7KB |
| **字段勾选** | ✅ 只导 3 列，表头/数据均正确 |
| Excel 内容校验 | ✅ 标题「产教融合项目台账」、表头、数据均正确 |
| 中文文件名下载 | ✅ `filename*=UTF-8''%E9%A1%B9%E7%9B%AE...` |

### 依赖

```
openpyxl>=3.1
reportlab>=4.0
```

## 后端完成度

| 模块 | 接口数 | 状态 |
|---|---|---|
| 认证（9 种协议） | 14 | ✅ |
| 项目台账 | 18 | ✅ |
| 进度管控 | 7 | ✅ |
| 预警管理 | 11 | ✅ |
| 报表管理 | 8 | ✅ |
| 数据导出 | 4 | ✅ |
| **合计** | **62** | ✅ |

对应需求文档 M1–M4 全部功能 + 文档 3.6 导出。

## 下一步

- [ ] 系统管理（用户/角色/参数/免填日历 CRUD）
- [ ] 风险登记 CRUD
- [ ] 报表明细导出（按项目打包 zip）
- [ ] 前端（React 18 + TypeScript + Ant Design 5）

---

## 系统管理与风险登记（已完成）

### 系统管理接口（14 个）

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| GET | `/api/system/users` | 用户列表（搜索/筛选） | `system:user` |
| POST | `/api/system/users` | 新增用户 | `system:user` |
| PUT | `/api/system/users/{id}` | 编辑用户（含改密） | `system:user` |
| DELETE | `/api/system/users/{id}` | 停用用户 | `system:user` |
| GET | `/api/system/roles` | 角色列表 | 登录 |
| POST | `/api/system/roles` | 新增自定义角色（上限 3） | `system:role` |
| GET | `/api/system/permissions` | 权限点清单 | `system:role` |
| PUT | `/api/system/roles/{id}/permissions` | 设置角色权限 | `system:role` |
| GET | `/api/system/grants` | 授权列表 | `system:role` |
| POST | `/api/system/grants` | 授予角色（含数据范围） | `system:role` |
| DELETE | `/api/system/grants/{id}` | 撤销授权 | `system:role` |
| GET | `/api/system/configs` | 系统参数 | `system:config` |
| PUT | `/api/system/configs/{key}` | 修改参数（即时生效） | `system:config` |
| GET/POST/DELETE | `/api/system/holidays` | 免填日历（支持批量） | `system:holiday` |
| GET | `/api/system/audit-logs` | 操作日志 | `system:audit` |

### 授权接口支持「一人多角色 + 不同项目不同身份」

```http
POST /api/system/grants
{
  "user_id": 2,
  "role_id": 4,
  "scope_type": "COLLEGE",
  "scope_id": 1,
  "valid_from": "2026-09-01",
  "valid_to": "2027-01-31"
}
```

`scope_type=MAJOR/COLLEGE` 时 `scope_id` 必填（Pydantic 校验，缺失返回 422）。

### 风险登记接口（5 个）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/risks` | 列表（支持 `only_overdue`） |
| POST | `/api/risks` | 登记风险 |
| PUT | `/api/risks/{id}` | 更新 |
| POST | `/api/risks/{id}/close` | 闭环（自动记实际闭环日） |
| DELETE | `/api/risks/{id}` | 删除 |

### 审计日志（中间件实现）

`app/core/audit.py`：

- 只记录**写操作**（POST/PUT/PATCH/DELETE），避免日志爆炸
- 跳过登录接口（避免记录密码相关请求）
- 记录：操作人、动作、目标类型/ID、查询参数、状态码、IP、UA
- **独立数据库会话写入**，审计失败不影响主流程
- 4xx 也记录，便于排查越权尝试

### 实测结果

| 场景 | 结果 |
|---|---|
| 建用户 + 搜索 | ✅ |
| 角色 7 个、权限点 38 个 | ✅ |
| **自定义角色上限 3 个** | ✅ 第 4 个返回 400 |
| 授予 COLLEGE 范围 | ✅ |
| **缺 scope_id 校验** | ✅ 422 |
| 参数修改即时生效 | ✅ -10 → -15 |
| 免填日历批量设置 | ✅ |
| 风险登记 + 超期识别 | ✅ 超期 3 天 |
| 风险闭环后不再超期 | ✅ |
| **风险超期触发 AL-08** | ✅ 扫描命中 1 条 |
| **审计日志** | ✅ 16 条记录 |

---

## 后端全部完成

| 模块 | 接口数 |
|---|---|
| 认证（9 种协议） | 14 |
| 项目台账 | 18 |
| 进度管控 | 7 |
| 预警管理 | 11 |
| 报表管理 | 8 |
| 数据导出 | 4 |
| 系统管理 | 14 |
| 风险登记 | 5 |
| **合计** | **81 个路径** |

### 实现的功能（对应需求文档）

| 文档章节 | 状态 |
|---|---|
| 2.1 三级责任体系 | ✅ |
| 2.2 角色与职责（一人多角色） | ✅ |
| 3.1 项目管理 | ✅ |
| 3.2 进度管控 | ✅ |
| 3.3 预警管理（11 条规则） | ✅ |
| 3.4 报表管理 | ✅ |
| 3.5 权限管理（功能 + 数据范围） | ✅ |
| 3.6 数据导出 | ✅ |
| 3.7 系统设置与审计 | ✅ |
| 4.1–4.5 关键业务规则 | ✅ |
| 附录一 11 条预警规则 | ✅ |
| 附录四 M1–M4 | ✅ |

## 下一步

- [ ] 前端（React 18 + TypeScript + Ant Design 5）
- [ ] 邮件发送（SMTP 接入）
- [ ] 报表明细 zip 打包导出

---

## 迭代更新（v0.2）

### 1. 预警统计增强

`GET /api/alerts/stats?days=30` 新增 3 个字段：

| 字段 | 结构 | 用途 |
|---|---|---|
| `trend` | `[{date, total, red, orange, yellow}]` | 按日趋势折线图 |
| `by_major` | `[{major_id, major_name, total, red}]` Top10 | 按大项目分布 |
| `age_buckets` | `{1天内, 1-3天, 3-7天, 7天以上}` | 未处置时长分布 |

### 2. 报表及时率明细

**新增接口**：`GET /api/reports/timeliness/detail`

| 参数 | 说明 |
|---|---|
| `start` / `end` | 必填，统计区间 |
| `type` | 可选，DAILY/WEEKLY/MONTHLY |
| `major_id` | 可选，按大项目过滤 |
| `state` | 可选，MISSING/LATE/ON_TIME |
| `page` / `page_size` | 分页 |

返回每期一行：

```json
{
  "sub_id": 1, "sub_name": "实训场地改造",
  "major_id": 1, "college_id": 1,
  "type": "WEEKLY", "type_label": "周报",
  "period_start": "2026-10-05", "period_end": "2026-10-11",
  "deadline": "2026-10-12T12:00:00+00:00",
  "report_id": null,
  "state": "MISSING", "state_label": "缺交",
  "submitted_at": null, "overdue_days": 6
}
```

排序：缺交 > 迟交 > 按期，同状态按期间倒序。

**内部改动**：`timeliness()` 返回的 `detail_all` 为完整明细（供分页用），API 层显式 `pop` 掉，不暴露给前端。

### 3. 用户下拉接口（前一轮补充）

`GET /api/system/users/picker` —— 任意登录用户可用，只返回 `id/name/staff_no/college`，供项目负责人、预警转交等场景选人，避免普通用户调 `/system/users` 时 403。

### 4. 角色权限回显接口（前一轮补充）

`GET /api/system/roles/{role_id}/permissions` → `{role_id, role_code, permission_ids}`

### 接口总数

从 81 个增至 **84 个**。
