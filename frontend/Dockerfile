# 씨네로케 웹 이미지 (COMP-01). 1단계에서 빌드하고, 2단계 nginx가 정적 파일을 제공한다.

# 1단계: React 빌드 (팀 표준 Node 24)
FROM node:24-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# 화면 설정(VITE_*)은 빌드 때 코드에 들어간다. 루트 .env는 빌드 문맥(./frontend) 밖이라 읽히지 않으므로
# 값을 여기서 정한다. compose로 띄우면 api 컨테이너가 함께 있으니 목업 대신 실제 API(/api)를 쓴다.
ARG VITE_API_MOCK=false
# 카카오 지도 JavaScript 키(FR-204 마커 묶음, FR-706). 공개 키라 화면 코드에 들어가며, 등록한 도메인에서만 동작한다.
# 비우면 OpenStreetMap으로 대체 표시. compose는 루트 .env의 VITE_KAKAO_JS_KEY를 build args로 넘긴다.
ARG VITE_KAKAO_JS_KEY=
ENV VITE_API_MOCK=$VITE_API_MOCK VITE_KAKAO_JS_KEY=$VITE_KAKAO_JS_KEY
RUN npm run build

# 2단계: nginx로 정적 파일 제공 + /api를 api 컨테이너로 전달
FROM nginx:stable-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
