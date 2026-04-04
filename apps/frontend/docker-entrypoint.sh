#!/bin/sh
set -e

cat > /usr/share/nginx/html/transit-sentinel-config.js <<'CONFIGEOF'
(function(){
  window.__TRANSIT_SENTINEL_CONFIG__ = Object.assign({}, window.__TRANSIT_SENTINEL_CONFIG__ || {}, {
    API_URL: "__API_URL__",
    API_BEARER_TOKEN: "__API_BEARER_TOKEN__"
  });
})();
CONFIGEOF

sed -i \
  -e "s#__API_URL__#${API_URL:-}#g" \
  -e "s#__API_BEARER_TOKEN__#${API_BEARER_TOKEN:-}#g" \
  /usr/share/nginx/html/transit-sentinel-config.js

TEMPLATES_DIR="/opt/nginx/templates"
SERVER_NAME_DEFAULT="${SERVER_NAME:-transit-sentinel.local}"
BACKEND_UPSTREAM_DEFAULT="${BACKEND_UPSTREAM:-http://api:8000}"
CERT_BASE_DEFAULT="${CERT_BASE:-/etc/letsencrypt/live/${SERVER_NAME_DEFAULT}}"

USE_LOCAL=0
if [ "${USE_LOCAL_NGINX:-}" = "1" ]; then
  USE_LOCAL=1
fi
if [ ! -f "${CERT_BASE_DEFAULT}/fullchain.pem" ]; then
  USE_LOCAL=1
fi

if [ "${USE_LOCAL}" = "1" ]; then
  SRC_TMPL="${TEMPLATES_DIR}/nginx.local.tmpl.conf"
else
  SRC_TMPL="${TEMPLATES_DIR}/nginx.tmpl.conf"
fi

if [ -f "${SRC_TMPL}" ]; then
  sed \
    -e "s#__SERVER_NAME__#${SERVER_NAME_DEFAULT}#g" \
    -e "s#__BACKEND_UPSTREAM__#${BACKEND_UPSTREAM_DEFAULT}#g" \
    -e "s#__CERT_BASE__#${CERT_BASE_DEFAULT}#g" \
    "${SRC_TMPL}" > /etc/nginx/conf.d/default.conf
fi

exec nginx -g "daemon off;"
