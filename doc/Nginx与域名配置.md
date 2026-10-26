# 内网域名 · HTTPS · Nginx 配置

面向**学校局域网**部署。校内外网隔离、没有公网域名和证书，因此重点在：

- 用**内部 DNS 或 hosts** 让老师能记住一个好记的域名
- 用**自建 CA 或自签证书**开启 HTTPS
- nginx 反代 + 前端静态托管

> 若将来有公网域名，第四节给了 Let's Encrypt 的做法，二者可以共存。

---

## 一、先想清楚：到底需不需要 HTTPS

| 场景 | 建议 |
|---|---|
| 纯内网、物理隔离、只传输非敏感排课/项目数据 | HTTP 可接受，但**登录口令仍是明文**，建议上 HTTPS |
| 内网有无线网、学生可接入同网段 | **必须 HTTPS**，否则抓包即得账号 |
| 有等保/安全合规要求 | **必须 HTTPS**，且建议自建 CA 而非自签 |

本项目登录用 JWT 存在请求头里，**没有 cookie**，所以 HTTPS 只需保证传输加密，
不涉及 `Secure`/`SameSite` 等 cookie 属性调整——这点比常见系统简单。

---

## 二、内部域名

局域网没有公网 DNS，有三种做法，按推荐排序：

### 2.1 内部 DNS 服务器（推荐，一次配置全校可用）

学校一般已有 DNS（Windows AD DNS 或 bind9）。加一条 A 记录：

| 主机记录 | 类型 | 值 |
|---|---|---|
| `pm.school.edu.cn` | A | `192.168.10.50`（服务器内网 IP） |

AD DNS 图形界面：DNS 管理器 → 正向查找区域 → 新建主机。
bind9：在区域文件里加 `pm    IN    A    192.168.10.50`，`rndc reload`。

验证（在任意一台内网机器）：

```bash
nslookup pm.school.edu.cn
```

### 2.2 服务器 hosts 下发脚本（无 DNS 管理权限时）

如果碰不到 DNS，可以让客户端改 hosts。给用户一个 `.bat`：

```bat
@echo off
echo 192.168.10.50  pm.school.edu.cn >> %SystemRoot%\System32\drivers\etc\hosts
echo 已添加内部域名，请重新打开浏览器
pause
```

> 缺点：每台机器都要跑一次，且主机换 IP 后要重刷。只适合小范围。

### 2.3 直接记 IP + 端口（最省事，但没有域名）

`http://192.168.10.50:8000`。

**如果不上 nginx、不要 HTTPS，这就够了**，不必折腾域名。

> ⚠️ 用域名（而非 IP）的主要价值：
> 1. 换服务器 IP 时用户书签不失效
> 2. 证书必须绑定域名，自签证书对 IP 也行但更麻烦
> 3. SSO 回调地址需要稳定的主机名

---

## 三、HTTPS（局域网自建证书）

Let's Encrypt 需要公网可达，内网用不了。两个选择：

### 3.1 自建 CA（推荐，多台服务器/长期使用）

自己当 CA 签发证书，把**根证书**推送到所有客户端，浏览器就不再报警。

**第一步：生成根 CA（只做一次，妥善保管）**

```bash
sudo mkdir -p /etc/nginx/ssl && cd /etc/nginx/ssl

# 根私钥（务必离线备份，泄露等于所有证书作废）
sudo openssl genrsa -out pm-ca.key 4096

# 根证书，有效期 10 年
sudo openssl req -x509 -new -nodes -key pm-ca.key -sha256 -days 3650 \
  -out pm-ca.crt \
  -subj "/C=CN/O=XX School/CN=XX School Internal CA"
```

**第二步：签发服务器证书**

```bash
# 服务器私钥
sudo openssl genrsa -out pm.key 2048

# 证书请求（CN 必须与访问用的域名完全一致）
sudo openssl req -new -key pm.key -out pm.csr \
  -subj "/C=CN/O=XX School/CN=pm.school.edu.cn"

# 用 CA 签发，注意 subjectAltName 要写域名
sudo openssl x509 -req -in pm.csr -CA pm-ca.crt -CAkey pm-ca.key \
  -CAcreateserial -out pm.crt -days 1825 -sha256 \
  -extfile <(printf "subjectAltName=DNS:pm.school.edu.cn,DNS:pm,IP:192.168.10.50")
```

> `subjectAltName` 里同时写域名和 IP，这样用 `https://192.168.10.50` 访问也不报错。

**第三步：推送根证书到客户端**

把 `pm-ca.crt` 分发到各终端：

- **Windows**：双击 → 安装证书 → 本地计算机 → 「受信任的根证书颁发机构」
  或域环境用组策略（GPO）统一推送
- **macOS**：钥匙串访问 → 导入 → 设为「始终信任」
- **火狐**：独立证书库，需在「隐私与安全 → 证书 → 查看证书 → 导入」单独加

### 3.2 自签证书（图快，但每个浏览器都会警告）

```bash
sudo openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
  -keyout /etc/nginx/ssl/pm.key \
  -out    /etc/nginx/ssl/pm.crt \
  -subj "/C=CN/O=XX School/CN=pm.school.edu.cn" \
  -addext "subjectAltName=DNS:pm.school.edu.cn,IP:192.168.10.50"
```

浏览器会显示「不安全」，用户需点「高级 → 继续前往」。**不推荐给非技术用户**。

### 3.3 nginx 配置

```nginx
server {
    listen 443 ssl;
    http2 on;
    server_name pm.school.edu.cn;

    ssl_certificate     /etc/nginx/ssl/pm.crt;
    ssl_certificate_key /etc/nginx/ssl/pm.key;

    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;
    ssl_session_cache   shared:SSL:10m;
    ssl_session_timeout 1d;

    # 其余 location 见第四节
}

# HTTP 强制跳 HTTPS
server {
    listen 80;
    server_name pm.school.edu.cn;
    return 301 https://$host$request_uri;
}
```

### 3.4 应用侧要改的配置

```ini
# /opt/pm/backend/.env
# 前端对外地址：SSO 回调落地页拼接用，必须是用户浏览器能访问的地址
PM_FRONTEND_BASE_URL=https://pm.school.edu.cn
```

```bash
sudo systemctl restart pm
```

> 不设这一项时，应用会按请求头推导；但走反代时推导可能不准，
> **明确写上最稳**。

### 3.5 CORS

前后端同域（都由 nginx 的 443 提供）时**无需配置 CORS**。
只有前端单独跑在别的端口时才需要：

```ini
PM_CORS_ORIGINS=["https://pm.school.edu.cn"]
```

---

## 四、Nginx 完整配置（内网 + HTTPS）

`/etc/nginx/conf.d/pm.conf`：

```nginx
upstream pm_backend {
    server 127.0.0.1:8000;
    keepalive 16;
}

server {
    listen 80;
    server_name pm.school.edu.cn;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    http2 on;
    server_name pm.school.edu.cn;

    ssl_certificate     /etc/nginx/ssl/pm.crt;
    ssl_certificate_key /etc/nginx/ssl/pm.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;
    ssl_session_cache   shared:SSL:10m;

    # 附件上传上限，需 ≥ PM_UPLOAD_MAX_SIZE_MB
    client_max_body_size 60m;

    access_log /var/log/nginx/pm.access.log;
    error_log  /var/log/nginx/pm.error.log;

    location = /healthz {
        access_log off;
        return 200 "ok\n";
    }

    # 带 hash 的前端资源长期缓存
    location /assets/ {
        root /opt/pm/frontend/dist;
        expires 1y;
        add_header Cache-Control "public, immutable";
        access_log off;
    }

    # SPA history 路由回退
    location / {
        root /opt/pm/frontend/dist;
        try_files $uri $uri/ /index.html;

        location = /index.html {
            add_header Cache-Control "no-cache, no-store, must-revalidate";
        }
    }

    # 后端 API
    location /api/ {
        proxy_pass http://pm_backend;
        proxy_http_version 1.1;
        proxy_set_header Connection        "";

        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;   # 必须：SSO 回调地址拼接
        proxy_set_header X-Forwarded-Host  $host;     # 必须
        proxy_set_header X-Forwarded-Port  $server_port;

        proxy_read_timeout 300s;    # 报表导出可能较慢
        proxy_send_timeout 300s;
        proxy_buffering    off;
    }
}
```

验证并生效：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

---

## 五、应用切到「nginx 托管前端」模式

`deploy.sh` 默认让应用自己托管前端（监听 8000）。用 nginx 时要改：

```ini
# /opt/pm/backend/.env
PM_FRONTEND_SERVE=false
PM_FRONTEND_DIR=/opt/pm/frontend/dist
PM_FRONTEND_BASE_URL=https://pm.school.edu.cn
```

```bash
sudo systemctl restart pm
# nginx 需能读前端文件
sudo chmod -R a+rX /opt/pm/frontend/dist
```

> 部署时可一步到位：
> ```bash
> sudo bash deploy.sh --domain pm.school.edu.cn
> ```
> 会自动装 nginx、生成 HTTP 配置并把 `PM_FRONTEND_SERVE` 设为 `false`。
> HTTPS 部分需按第三节手工加证书（脚本不代替你签发证书）。

---

## 六、防火墙

```bash
# 只对内网开放
sudo firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="192.168.0.0/16" port port="443" protocol="tcp" accept'
sudo firewall-cmd --reload
```

同时**不要**对外暴露 8000，只让 nginx 从本机访问：

```bash
# .env 里也可显式绑回环
# ExecStart 用 --host 127.0.0.1
```

> `deploy.sh` 当前让 uvicorn 监听 `0.0.0.0`，若走 nginx 建议改成 `127.0.0.1`，
> 避免绕过 nginx 直连。

---

## 七、排查

| 现象 | 检查 |
|---|---|
| 域名解析不到 | `nslookup pm.school.edu.cn`；DNS 记录是否生效 |
| 浏览器证书警告 | 根证书未装到该终端；或 CN/SAN 与访问地址不符 |
| 502 Bad Gateway | `systemctl status pm`；`curl http://127.0.0.1:8000/api/health` |
| 刷新子路由 404 | `try_files $uri $uri/ /index.html` 缺失 |
| 发版后还是旧页面 | `index.html` 未设 no-cache |
| 单点登录回调失败 | 未转发 `X-Forwarded-Proto` / `X-Forwarded-Host`；或 `PM_FRONTEND_BASE_URL` 没写 |
| 上传附件 413 | `client_max_body_size` 小于 `PM_UPLOAD_MAX_SIZE_MB` |

```bash
sudo nginx -t
sudo tail -f /var/log/nginx/pm.error.log
sudo journalctl -u pm -f
```

---

## 八、（可选）将来有公网域名时

- 域名 A 记录指向公网 IP
- `sudo certbot --nginx -d pm.school.edu.cn` 自动签发免费证书
- 内网与外网可共用同一套 nginx，仅证书来源不同
- 若前面还挂了 CDN，HTTPS 建议在 CDN 侧配置，回源保持 HTTP