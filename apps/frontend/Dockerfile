FROM node:20 AS build
WORKDIR /app
COPY package*.json ./
RUN npm install --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginx:1.29.1-alpine
RUN apk add --no-cache curl
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY nginx/includes /etc/nginx/includes
COPY nginx.global.conf /etc/nginx/nginx.conf
COPY nginx.tmpl.conf /opt/nginx/templates/nginx.tmpl.conf
COPY nginx.local.tmpl.conf /opt/nginx/templates/nginx.local.tmpl.conf
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

ENV API_URL="" \
    API_BEARER_TOKEN=""

CMD ["/bin/sh", "/docker-entrypoint.sh"]
