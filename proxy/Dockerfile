FROM nginx:1.27-alpine

RUN apk add --no-cache openssl

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY generate-cert.sh /docker-entrypoint.d/05-generate-cert.sh
RUN chmod +x /docker-entrypoint.d/05-generate-cert.sh
