# 씨네로케 웹 이미지 (COMP-01). 1단계에서 빌드하고, 2단계 nginx가 정적 파일을 제공한다.

# 1단계: React 빌드 (팀 표준 Node 24)
FROM node:24-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# 2단계: nginx로 정적 파일 제공 + /api를 api 컨테이너로 전달
FROM nginx:stable-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
