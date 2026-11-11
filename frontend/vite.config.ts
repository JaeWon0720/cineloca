import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // 개발 서버(npm run dev)에서 /api 요청을 로컬 API 서버로 전달한다.
    // Docker의 web 컨테이너에서는 nginx가 같은 역할을 한다.
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
