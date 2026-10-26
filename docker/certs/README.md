# 证书目录

`PM_TLS_MODE=file` 时，把 PEM 格式的证书链和私钥放在本目录：

| 文件 | 说明 |
|---|---|
| `server.crt` | 服务器证书 + 中间证书（fullchain），PEM 格式 |
| `server.key` | 私钥，PEM 格式，不要加密 |

也可以在 `.env` 里改 `PM_TLS_CERT_NAME` / `PM_TLS_KEY_NAME` 使用其他文件名。

## 检查证书

```bash
openssl x509 -in certs/server.crt -noout -subject -issuer -dates
```

## 权限

私钥建议 `chmod 600`。Caddy 容器只读挂载本目录，不需要写权限。

## 其他模式

- `PM_TLS_MODE=auto`：Caddy 自动申请/续期 Let's Encrypt，不需要本目录。
- `PM_TLS_MODE=internal`：Caddy 内部 CA 自签，不需要本目录。
- `PM_TLS_MODE=off`：不启用 TLS，由前置网关终止 HTTPS。
