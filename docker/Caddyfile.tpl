# 此文件由 deploy.sh 根据 .env 自动生成，请勿手工修改。
{
    admin off
__ACME_EMAIL__
}

__SITE_ADDRESS__ {
__TLS_BLOCK__
    encode zstd gzip

    # 前端带 hash 的静态资源：长缓存
    @assets path /assets/*
    header @assets Cache-Control "public, max-age=31536000, immutable"

    header {
        X-Content-Type-Options nosniff
        X-Frame-Options SAMEORIGIN
        Referrer-Policy strict-origin-when-cross-origin
    }

    # 前端与 API 都由 FastAPI 托管，Caddy 只做 TLS 终止与反向代理。
    # Caddy 会自动转发 X-Forwarded-Proto / X-Forwarded-Host，OIDC/CAS 回调地址才能拼对。
    reverse_proxy app:8000
}
