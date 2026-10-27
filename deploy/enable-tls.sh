#!/usr/bin/env bash
# 把 nginx 配置里带 #TLS# 的行放开（443 监听、证书路径、域名跳 HTTPS、www 跳裸域），
# 校验后 reload。重复执行是安全的：没有 #TLS# 标记时只做校验和 reload。
#
# 用法（服务器上，root）：
#   sudo bash enable-tls.sh
#
# 前置条件：
#   1) /etc/nginx/sites-available/sleep-schedule 是 deploy/nginx-sleep-schedule.conf 装的版本
#   2) 证书已签发到 /etc/letsencrypt/live/yiuki.me/：
#        sudo certbot certonly --webroot -w /var/www/certbot \
#             -d yiuki.me -d www.yiuki.me --deploy-hook "systemctl reload nginx"
#
# 为什么要这么绕：ssl_certificate 指向的文件不存在时 nginx -t 直接失败，
# 配置文件不能提前带 443 上线，只能等证书落地后再放开。
set -euo pipefail

VHOST=/etc/nginx/sites-available/sleep-schedule
CERT=/etc/letsencrypt/live/yiuki.me/fullchain.pem
KEY=/etc/letsencrypt/live/yiuki.me/privkey.pem

if [ "$(id -u)" -ne 0 ]; then
  echo "!! 需要 root：sudo bash $0"
  exit 1
fi

if [ ! -f "$VHOST" ]; then
  echo "!! 找不到 $VHOST，先把 deploy/nginx-sleep-schedule.conf 装上去"
  exit 1
fi

if [ ! -f "$CERT" ] || [ ! -f "$KEY" ]; then
  echo "!! 证书不存在：$CERT"
  echo "   先签发："
  echo "   certbot certonly --webroot -w /var/www/certbot -d yiuki.me -d www.yiuki.me \\"
  echo "        --deploy-hook 'systemctl reload nginx'"
  exit 1
fi

if ! grep -q '#TLS#' "$VHOST"; then
  echo "== 没有 #TLS# 标记（已经开过 HTTPS），只做校验和 reload"
  nginx -t
  systemctl reload nginx
  echo "tls already enabled: OK"
  exit 0
fi

backup="$VHOST.bak.$(date +%Y%m%d%H%M%S)"
cp "$VHOST" "$backup"
echo "== 备份：$backup"

# 只放开“行首缩进 + #TLS# + 空格”的行，注释里提到 #TLS# 的说明文字不受影响
sed -i 's/^\([[:space:]]*\)#TLS# /\1/' "$VHOST"

if ! nginx -t; then
  echo "!! nginx -t 失败，回滚到 $backup"
  cp "$backup" "$VHOST"
  exit 1
fi

systemctl reload nginx
# reload 是优雅重启：旧 worker 会短暂继续用旧配置，立刻验证可能拿到 302/旧行为
sleep 2
echo "== 已启用 HTTPS"

echo "== 本地自检（绕过备案拦截，直接问 127.0.0.1）："
curl -s -o /dev/null -w "   http://yiuki.me   -> %{http_code}\n" -H "Host: yiuki.me" http://127.0.0.1/ || true
curl -sk -o /dev/null -w "   https://yiuki.me  -> %{http_code}\n" --resolve yiuki.me:443:127.0.0.1 https://yiuki.me/ || true
