# 腾讯云 EdgeOne 配置指引

## 当前问题

`chemicalagent.site` 经过腾讯云 EdgeOne（CDN），但**回源到了错误的服务器**：

| 访问方式 | 返回内容 | 说明 |
|---|---|---|
| `http://chemicalagent.site/` | 16313 字节 HTML（`Server: TencentEdgeOne`） | ❌ 回源到了别的站点 |
| `http://123.206.121.190/api/health` | `{"code":0,...}` | ✅ 直达我们的应用 |

导致的结果：
1. 域名访问看到的不是本系统
2. Let's Encrypt 证书无法申请（验证请求打到了 CDN 而不是源站）

---

## 需要在 EdgeOne 控制台做的事

### 一、修改回源地址

登录腾讯云控制台 → **边缘安全加速平台 EdgeOne** → 找到 `chemicalagent.site` 站点 →
**站点加速** → **域名管理** → `chemicalagent.site` → **回源配置**

把回源地址改为：

```
类型：IP 或域名
地址：123.206.121.190
端口：80
```

同样为 `www.chemicalagent.site` 配置。

> 如果当前回源指向的是其他 IP / 域名，删掉旧的，换成 `123.206.121.190:80`。

### 二、关闭缓存（重要）

EdgeOne 默认会缓存静态资源，但**API 请求不能被缓存**，否则登录、提交数据都会出错。

在 **规则引擎** 或 **缓存配置** 中添加：

| 匹配条件 | 操作 |
|---|---|
| 路径 匹配 `/api/*` | 缓存过期时间设为 **不缓存** |
| 路径 匹配 `/api/*` | 关闭「缓存」开关 |
| 路径 匹配 `/assets/*` | 缓存 30 天（可选，加速静态资源） |
| 路径 匹配 `/` | 缓存 不缓存（HTML 要实时） |

### 三、确认源站协议

**回源协议** 设为 `HTTP`（源站是 80 端口，没有 HTTPS）。

如果 EdgeOne 强制用 HTTPS 回源会失败。

### 四、开启 HTTPS

EdgeOne 可以自动签发并托管证书：

**站点加速 → 域名管理 → `chemicalagent.site` → HTTPS 配置**

| 项 | 设置 |
|---|---|
| HTTPS | 开启 |
| 证书类型 | 免费证书（EdgeOne 自动申请）或上传自有证书 |
| 强制 HTTPS 跳转 | 开启 |
| 最低 TLS 版本 | TLS 1.2 |

配置完成后：
```
https://chemicalagent.site/        → 自动跳转
https://chemicalagent.site/api/*   → 回源到 123.206.121.190:80
```

> **注意**：EdgeOne 的免费证书由腾讯云签发，不需要 Let's Encrypt，
> 国内服务器申请不存在验证失败的问题。

### 五、源站安全（建议）

源站 `123.206.121.190:80` 目前对公网开放。建议加白名单只允许 EdgeOne 回源 IP：

在服务器上执行：

```bash
# EdgeOne 回源 IP 段（腾讯云官方）
# 参考：https://cloud.tencent.com/document/product/1552/84323
iptables -A INPUT -p tcp --dport 80 -s <EdgeOne-IP段> -j ACCEPT
iptables -A INPUT -p tcp --dport 80 -j DROP
```

> 这一步可选。不做也能用，只是源站可被直接访问。

---

## 配置完成后如何验证

### 1. 域名访问

```bash
curl -I http://chemicalagent.site/
# 期望：Server: TencentEdgeOne，Content-Type: text/html

curl http://chemicalagent.site/api/health
# 期望：{"code":0,"message":"ok",...}

curl -I https://chemicalagent.site/
# 期望：HTTP/2 200
```

### 2. 登录测试

浏览器打开 `https://chemicalagent.site`

账号 `admin` / `Admin@123`

### 3. 检查 API 是否被缓存

```bash
# 连发两次，第二次也应正常返回（不能是 HIT 的旧数据）
curl -s https://chemicalagent.site/api/health
curl -s https://chemicalagent.site/api/health
```

如果第二次返回 `EO-Cache-Status: HIT` 且数据不对，说明缓存没关掉。

---

## 临时访问方式（配置期间可用）

在 EdgeOne 配置完成之前，可以直接用 IP 访问：

```
http://123.206.121.190
```

账号 `admin` / `Admin@123`

> 注意：这个地址是裸 IP，没有 HTTPS，仅用于临时验证。

---

## 服务器端当前状态

| 项 | 状态 |
|---|---|
| 应用服务 | `systemctl status pm` → active |
| Nginx | 反代 127.0.0.1:8000 |
| 数据库 | SQLite `/data/pm/pm.db` |
| 后端 API | ✅ 健康检查正常 |
| 前端 | ✅ 已构建（2.2 MB） |
| 监听端口 | 80（nginx）、8000（uvicorn，仅本地） |
| 内存 | 615 MB / 1.9 GB |
| 磁盘 | 43 GB 可用 |

**服务器端已就绪，只差 EdgeOne 回源配置。**
