import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

api.interceptors.request.use((config) => {
  const adminToken = localStorage.getItem('admin_token')
  if (adminToken && config.url?.includes('/admin')) {
    config.headers.Authorization = `Bearer ${adminToken}`
    return config
  }
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
      const path = window.location.pathname
      if (path.startsWith('/admin')) {
        localStorage.removeItem('admin_token')
        localStorage.removeItem('admin_role')
        if (path !== '/admin/login') {
          window.location.href = '/admin/login'
        }
      } else {
        localStorage.removeItem('token')
        if (path !== '/login') {
          window.location.href = '/login'
        }
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
  node_id?: string
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
  spot_price: string
  deriv_price: string
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
  open_tiers_json: { spread_bps: number; ratio: number }[]
  close_tiers_json: { spread_bps: number; ratio: number }[]
  max_single_task_notional: number
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
  open_tiers_json?: { spread_bps: number; ratio: number }[]
  close_tiers_json?: { spread_bps: number; ratio: number }[]
  max_single_task_notional?: number
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
    open_tiers_json: { spread_bps: number; ratio: number }[]
    close_tiers_json: { spread_bps: number; ratio: number }[]
    max_single_task_notional: number
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

export interface RiskLimitRule {
  id: number
  scope_type: string
  scope_id: string
  limit_type: string
  limit_value: number
  enabled: boolean
  priority: number
  user_id?: number
}

export interface PlatformSwitch {
  switch_key: string
  scope_type: string
  scope_id: string
  enabled: boolean
}

export interface Announcement {
  id: number
  title: string
  content: string
  status: string
  is_pinned: boolean
  created_at?: string
  updated_at?: string
}

export interface AuditLogItem {
  id: number
  admin_user_id: number
  action_type: string
  target_type: string
  target_id: string
  before_json: any
  after_json: any
  created_at?: string
}

export async function getLimits() {
  const { data } = await api.get<{ limits: RiskLimitRule[] }>('/admin/limits')
  return data
}

export async function createLimit(body: {
  scope_type: string
  scope_id: string
  limit_type: string
  limit_value: number
}) {
  const { data } = await api.post<RiskLimitRule>('/admin/limits', body)
  return data
}

export async function updateLimit(
  id: number,
  body: Partial<{
    enabled: boolean
    limit_value: number
    priority: number
  }>,
) {
  const { data } = await api.put<RiskLimitRule>(`/admin/limits/${id}`, body)
  return data
}

export async function deleteLimit(id: number) {
  await api.delete(`/admin/limits/${id}`)
}

export async function getSwitches() {
  const { data } = await api.get<{ switches: PlatformSwitch[] }>('/admin/switches')
  return data
}

export async function putSwitch(switch_id: string, enabled: boolean) {
  const { data } = await api.put<PlatformSwitch>(`/admin/switches/${switch_id}`, { enabled })
  return data
}

export async function deleteSwitch(switch_id: string) {
  await api.delete(`/admin/switches/${switch_id}`)
}

export async function getAnnouncements() {
  const { data } = await api.get<{ announcements: Announcement[] }>('/admin/announcements')
  return data
}

export async function createAnnouncement(body: {
  title: string
  content: string
  status: string
}) {
  const { data } = await api.post<Announcement>('/admin/announcements', body)
  return data
}

export async function updateAnnouncement(
  id: number,
  body: Partial<{
    title: string
    content: string
    status: string
    is_pinned: boolean
  }>,
) {
  const { data } = await api.put<Announcement>(`/admin/announcements/${id}`, body)
  return data
}

export async function deleteAnnouncement(id: number) {
  await api.delete(`/admin/announcements/${id}`)
}

export async function sendAnnouncement(id: number) {
  const { data } = await api.post<{ ok: boolean; results: Record<string, string> }>(`/admin/announcements/${id}/send`)
  return data
}

export async function getAudit(params: { page_size?: number }) {
  const { data } = await api.get<{ items: AuditLogItem[] }>('/admin/audit', { params })
  return data
}

export async function getAdminUsers(params: { page_size?: number }) {
  const { data } = await api.get<{ items: UserInfo[] }>('/admin/users', { params })
  return data
}

export interface AdminLoginResponse {
  access_token: string
  role: string
}

export interface AdminMeResponse {
  id: number
  username: string
  role: string
}

export async function adminLogin(username: string, password: string) {
  const { data } = await api.post<AdminLoginResponse>('/admin/login', { username, password })
  return data
}

export async function getAdminMe() {
  const { data } = await api.get<AdminMeResponse>('/admin/me')
  return data
}

export interface PositionItem {
  id: number
  task_uuid: string
  task_type: string
  symbol: string
  spot_exchange: string
  derivative_exchange: string
  target_notional: number
  expected_spread_bps: number
  expected_funding_bps: number
  status: string
  execution_status: string | null
  auto_recovery_status: string
  failure_reason: string | null
  filled_notional: number
  realized_pnl: number | null
  unrealized_pnl: number | null
  total_fee: number
  created_at: string | null
  finished_at: string | null
}

export interface PositionPageData {
  items: PositionItem[]
  total: number
  page: number
  page_size: number
}

export async function getPositions(params: { page?: number; page_size?: number }) {
  const { data } = await api.get<PositionPageData>('/positions', { params })
  return data
}

export interface PnlPoint {
  date: string
  cumulative_pnl: number
}

export async function getPnlHistory(days: number = 30) {
  const { data } = await api.get<{ points: PnlPoint[]; total_realized_pnl: number }>('/positions/pnl-history', { params: { days } })
  return data
}

export interface ExchangeAccount {
  id: number
  exchange: string
  account_label: string
  env_mode: string
  api_key_masked: string
  secret_masked: string
  passphrase_masked: string
  secret_set: boolean
  passphrase_set: boolean
}

export interface SmtpSettings {
  host: string
  port: number
  username: string
  password: string
}

export interface UserSettings {
  email: string | null
  feishu_webhook_url: string | null
  smtp: SmtpSettings
  exchange_accounts: ExchangeAccount[]
}

export async function getSettings() {
  const { data } = await api.get<UserSettings>('/settings')
  return data
}

export async function updateProfile(body: {
  email: string | null
  feishu_webhook_url: string | null
  smtp?: SmtpSettings
}) {
  const { data } = await api.put<UserSettings>('/settings/profile', body)
  return data
}

export async function createExchangeAccount(body: {
  exchange: string
  api_key: string
  secret: string
  passphrase?: string
  env_mode?: string
}) {
  const { data } = await api.post<ExchangeAccount>('/settings/exchange', body)
  return data
}

export async function updateExchangeAccount(
  id: number,
  body: { api_key: string; secret: string; passphrase?: string },
) {
  const { data } = await api.put<ExchangeAccount>(`/settings/exchange/${id}`, body)
  return data
}

export async function deleteExchangeAccount(id: number) {
  await api.delete(`/settings/exchange/${id}`)
}

export interface AssetItem {
  currency: string
  free: number
  used: number
  total: number
  usdt_value: number
}

export interface ExchangeBalance {
  exchange: string
  env_mode: string
  error: string | null
  assets: AssetItem[]
  total_usdt: number
}

export interface BalancesData {
  exchanges: ExchangeBalance[]
  total_usdt: number
}

export async function getBalances() {
  const { data } = await api.get<BalancesData>('/settings/balances')
  return data
}

export interface TradeToggleResponse {
  is_trading_enabled: boolean
}

export function tradeToggle(): Promise<TradeToggleResponse> {
  return api.patch('/auth/me/trade-toggle').then((res) => res.data)
}

export { api }
