#!/bin/sh
# Генерирует self-signed сертификат для локального HTTPS, если его ещё нет.
set -e
CERT_DIR=/etc/nginx/certs
if [ ! -f "$CERT_DIR/server.crt" ]; then
  mkdir -p "$CERT_DIR"
  openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
    -keyout "$CERT_DIR/server.key" -out "$CERT_DIR/server.crt" \
    -subj "/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1,IP:10.0.2.2"
  echo "Self-signed certificate generated"
fi
