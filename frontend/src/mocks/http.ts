import type { ApiResponse } from '../api/client.ts'

export type HandlerInput = { params: string[]; query: URLSearchParams; body: unknown; headers: Record<string, string> }
export type Handler = { method: 'GET' | 'POST' | 'DELETE'; path: RegExp; handle: (input: HandlerInput) => ApiResponse }

export const ok = (data: unknown): ApiResponse => ({ status: 200, data })
export const fail = (status: number, code: string, message: string): ApiResponse => ({
  status,
  data: { error: { code, message, retry_after: null } },
})
