export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export async function api<T = void>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init.headers },
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new ApiError(response.status, body?.detail ?? response.statusText)
  }
  return response.status === 204 ? (undefined as T) : response.json()
}

export interface User {
  id: number
  name: string
}

export const auth = {
  me: () => api<User>('/auth/me'),
  login: (password: string) =>
    api('/auth/login', { method: 'POST', body: JSON.stringify({ password }) }),
  logout: () => api('/auth/logout', { method: 'POST' }),
}
