import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

api.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err.response?.status === 401) {
      localStorage.removeItem('token')
      if (window.location.pathname !== '/login') {
        window.location.href = '/login'
      }
    }
    return Promise.reject(err)
  },
)

export interface TokenResponse {
  access_token: string
  token_type: string
}

export interface UserInfo {
  id: number
  username: string
  status: string
  is_trading_enabled: boolean
}

export interface LeaderboardRow {
  symbol: string
  full_symbol: string
  spot_exchange: string
  derivative_exchange: string
  open_spread_pct: number
  close_spread_pct: number
  funding_rate_display: string
  funding_rate_raw: number
  sort_value: number
  index_spread_pct: number
  spot_volume: string
  deriv_volume: string
}

export interface LeaderboardPageData {
  items: LeaderboardRow[]
  total: number
  page: number
  page_size: number
}

export interface Strategy {
  id: number
  name: string
  strategy_type: string
  symbol_scope_json: string[]
  exchange_scope_json: string[]
  target_quote_amount: number
  open_spread_bps_threshold: number
  close_spread_bps_threshold: number
  is_enabled: boolean
}

export interface TaskItem {
  id: number
  task_uuid: string
  task_type: string
  symbol: string
  spot_exchange: string
  derivative_exchange: string
  target_notional: number
  expected_spread_bps: number
  status: string
  execution_status: string | null
  failure_reason: string | null
  created_at: string | null
  finished_at: string | null
}

export interface TaskPageData {
  items: TaskItem[]
  total: number
  page: number
  page_size: number
}

export async function login(username: string, password: string) {
  const { data } = await api.post<TokenResponse>('/auth/login', { username, password })
  return data
}

export async function register(username: string, password: string) {
  const { data } = await api.post<TokenResponse>('/auth/register', { username, password })
  return data
}

export async function getMe() {
  const { data } = await api.get<UserInfo>('/auth/me')
  return data
}

export async function getLeaderboard(params: {
  direction?: string
  page?: number
  page_size?: number
}) {
  const { data } = await api.get<LeaderboardPageData>('/opportunities/leaderboard', { params })
  return data
}

export async function getStrategies() {
  const { data } = await api.get<Strategy[]>('/strategies')
  return data
}

export async function createStrategy(body: {
  name: string
  symbol: string
  spot_exchange: string
  derivative_exchange: string
  target_quote_amount: number
  open_spread_bps_threshold: number
  close_spread_bps_threshold: number
}) {
  const { data } = await api.post<Strategy>('/strategies', body)
  return data
}

export async function updateStrategy(
  id: number,
  body: Partial<{
    name: string
    target_quote_amount: number
    open_spread_bps_threshold: number
    close_spread_bps_threshold: number
  }>,
) {
  const { data } = await api.put<Strategy>(`/strategies/${id}`, body)
  return data
}

export async function deleteStrategy(id: number) {
  await api.delete(`/strategies/${id}`)
}

export async function toggleStrategy(id: number) {
  const { data } = await api.patch<Strategy>(`/strategies/${id}/toggle`)
  return data
}

export async function getTasks(params: { page?: number; page_size?: number }) {
  const { data } = await api.get<TaskPageData>('/tasks', { params })
  return data
}
