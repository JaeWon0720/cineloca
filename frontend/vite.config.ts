import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // 저장소 루트의 .env를 읽는다(.env.example 참고). VITE_로 시작하는 값만 화면 코드에 들어간다.
  envDir: '..',
  server: {
    // VITE_API_MOCK=false일 때 /api 요청을 로컬 FastAPI(uvicorn 기본 포트)로 넘긴다
    proxy: { '/api': { target: process.env.API_URL ?? 'http://localhost:8000', changeOrigin: true } },
  },
})
