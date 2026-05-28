import { useCallback, useEffect, useState } from 'react'
import {
  getSettings,
  updateProfile,
  createExchangeAccount,
  updateExchangeAccount,
  deleteExchangeAccount,
  getBalances,
  liquidateAssets,
  type UserSettings,
  type SmtpSettings,
  type BalancesData,
  type LiquidateResult,
} from '../api'

type Tab = 'notify' | 'api' | 'balance'

const TABS: { key: Tab; label: string; icon: string }[] = [
  { key: 'notify', label: '通知设置', icon: '🔔' },
  { key: 'api', label: 'API 管理', icon: '🔑' },
  { key: 'balance', label: '资产预览', icon: '💰' },
]

const EXCHANGES = ['binance', 'okx', 'bybit', 'gate', 'bitget']
const ENV_MODES: { value: string; label: string }[] = [
  { value: 'testnet', label: '模拟盘' },
  { value: 'mainnet', label: '实盘' },
]

export function SettingsPage() {
  const [tab, setTab] = useState<Tab>('notify')
  const [settings, setSettings] = useState<UserSettings | null>(null)

  const [email, setEmail] = useState('')
  const [feishu, setFeishu] = useState('')
  const [smtp, setSmtp] = useState<SmtpSettings>({ host: '', port: 465, username: '', password: '' })
  const [saving, setSaving] = useState(false)
  const [toast, setToast] = useState('')

  const [editEx, setEditEx] = useState<string | null>(null)
  const [exForm, setExForm] = useState({ api_key: '', secret: '', passphrase: '', env_mode: 'testnet' })

  const [balances, setBalances] = useState<BalancesData | null>(null)
  const [loadingBal, setLoadingBal] = useState(false)

  const [liquidating, setLiquidating] = useState(false)
  const [liquidateResult, setLiquidateResult] = useState<LiquidateResult | null>(null)
  const [showLiqConfirm, setShowLiqConfirm] = useState(false)

  const showToast = (msg: string) => {
    setToast(msg)
    setTimeout(() => setToast(''), 2000)
  }

  const reloadSettings = useCallback(() => {
    getSettings().then((s) => {
      setSettings(s)
      setEmail(s.email || '')
      setFeishu(s.feishu_webhook_url || '')
      setSmtp(s.smtp || { host: '', port: 465, username: '', password: '' })
    })
  }, [])

  useEffect(() => { reloadSettings() }, [reloadSettings])

  const refreshBalance = useCallback(async () => {
    setLoadingBal(true)
    try {
      const b = await getBalances()
      setBalances(b)
    } catch {
      showToast('资产加载失败，请检查 API 配置')
    } finally {
      setLoadingBal(false)
    }
  }, [])

  useEffect(() => { if (tab === 'balance') refreshBalance() }, [tab, refreshBalance])

  const saveProfile = async () => {
    setSaving(true)
    try {
      await updateProfile({ email: email || null, feishu_webhook_url: feishu || null, smtp })
      showToast('通知设置已保存')
      reloadSettings()
    } finally {
      setSaving(false)
    }
  }

  const handleExchangeSave = async () => {
    if (!editEx) return
    const existing = settings?.exchange_accounts.find((a) => a.exchange === editEx)
    try {
      if (existing) {
        await updateExchangeAccount(existing.id, { api_key: exForm.api_key, secret: exForm.secret, passphrase: exForm.passphrase || undefined })
      } else {
        await createExchangeAccount({
          exchange: editEx,
          api_key: exForm.api_key,
          secret: exForm.secret,
          passphrase: exForm.passphrase || undefined,
          env_mode: exForm.env_mode,
        })
      }
      setEditEx(null)
      showToast(existing ? 'API 已更新' : 'API 已添加')
      reloadSettings()
    } catch {
      showToast('保存失败，请重试')
    }
  }

  const handleDelete = async (id: number) => {
    if (!confirm('确定删除此 API Key？')) return
    try {
      await deleteExchangeAccount(id)
      showToast('已删除')
      reloadSettings()
    } catch {
      showToast('删除失败')
    }
  }

  const handleLiquidate = async () => {
    setShowLiqConfirm(false)
    setLiquidating(true)
    setLiquidateResult(null)
    try {
      const res = await liquidateAssets()
      setLiquidateResult(res)
      showToast(`清仓完成: ${res.summary.orders_placed} 笔, $${res.summary.total_sold_usdt.toLocaleString()}`)
    } catch {
      showToast('清仓请求失败')
    } finally {
      setLiquidating(false)
    }
  }

  if (!settings) return <div className="p-6 text-gray-500">加载中...</div>

  return (
    <div className="mx-auto max-w-3xl px-4 py-6">
      <h2 className="mb-6 text-xl font-bold">个人设置</h2>

      {toast && (
        <div className="fixed top-4 right-4 z-50 rounded-lg bg-emerald-800 px-4 py-2 text-sm text-white shadow-lg">
          {toast}
        </div>
      )}

      <div className="mb-6 flex rounded-lg border border-gray-800 bg-gray-900 p-1">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`flex-1 rounded-md px-3 py-2 text-sm font-medium transition ${
              tab === t.key
                ? 'bg-emerald-600 text-white shadow'
                : 'text-gray-400 hover:text-white'
            }`}
          >
            <span className="mr-1.5">{t.icon}</span>
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'notify' && <NotifyTab email={email} setEmail={setEmail} feishu={feishu} setFeishu={setFeishu} smtp={smtp} setSmtp={setSmtp} saving={saving} onSave={saveProfile} />}
      {tab === 'api' && <ApiTab settings={settings} editEx={editEx} setEditEx={setEditEx} exForm={exForm} setExForm={setExForm} onSave={handleExchangeSave} onDelete={handleDelete} />}
      {tab === 'balance' && <BalanceTab balances={balances} loadingBal={loadingBal} onRefresh={refreshBalance} liquidating={liquidating} liquidateResult={liquidateResult} showLiqConfirm={showLiqConfirm} setShowLiqConfirm={setShowLiqConfirm} onLiquidate={handleLiquidate} closeResult={() => setLiquidateResult(null)} />}
    </div>
  )
}

function NotifyTab({
  email, setEmail, feishu, setFeishu, smtp, setSmtp, saving, onSave,
}: {
  email: string; setEmail: (v: string) => void
  feishu: string; setFeishu: (v: string) => void
  smtp: SmtpSettings; setSmtp: (v: SmtpSettings) => void
  saving: boolean; onSave: () => void
}) {
  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
      <p className="mb-4 text-xs text-gray-500">配置通知渠道后，系统将在关键事件发生时向您推送通知。</p>

      <div className="mb-5 pb-5 border-b border-gray-800">
        <h4 className="mb-3 text-sm font-medium text-gray-400">邮件通知</h4>
        <div className="mb-3">
          <label className="mb-1 block text-xs text-gray-500">接收邮箱</label>
          <input
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full rounded border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-emerald-500 focus:outline-none"
            placeholder="user@example.com"
          />
        </div>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <div>
            <label className="mb-1 block text-xs text-gray-500">SMTP 服务器</label>
            <input
              value={smtp.host}
              onChange={(e) => setSmtp({ ...smtp, host: e.target.value })}
              className="w-full rounded border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-emerald-500 focus:outline-none"
              placeholder="smtp.qq.com"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-gray-500">端口</label>
            <input
              type="number"
              value={smtp.port}
              onChange={(e) => setSmtp({ ...smtp, port: Number(e.target.value) })}
              className="w-full rounded border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-emerald-500 focus:outline-none"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-gray-500">SMTP 用户名</label>
            <input
              value={smtp.username}
              onChange={(e) => setSmtp({ ...smtp, username: e.target.value })}
              className="w-full rounded border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-emerald-500 focus:outline-none"
              placeholder="你的邮箱地址"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-gray-500">SMTP 密码/授权码</label>
            <input
              type="password"
              value={smtp.password}
              onChange={(e) => setSmtp({ ...smtp, password: e.target.value })}
              className="w-full rounded border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-emerald-500 focus:outline-none"
              placeholder="16位授权码"
            />
          </div>
        </div>
      </div>

      <div className="mb-5">
        <h4 className="mb-3 text-sm font-medium text-gray-400">飞书通知</h4>
        <div>
          <label className="mb-1 block text-xs text-gray-500">飞书 Webhook URL</label>
          <input
            value={feishu}
            onChange={(e) => setFeishu(e.target.value)}
            className="w-full rounded border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-emerald-500 focus:outline-none"
            placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..."
          />
          <p className="mt-1 text-xs text-gray-600">创建飞书群机器人 → 复制 Webhook 地址</p>
        </div>
      </div>

      <button
        onClick={onSave}
        disabled={saving}
        className="w-full rounded bg-emerald-600 px-4 py-2.5 text-sm font-medium hover:bg-emerald-500 disabled:opacity-50"
      >
        {saving ? '保存中...' : '保存通知设置'}
      </button>
    </div>
  )
}

function ApiTab({
  settings, editEx, setEditEx, exForm, setExForm, onSave, onDelete,
}: {
  settings: UserSettings
  editEx: string | null; setEditEx: (v: string | null) => void
  exForm: { api_key: string; secret: string; passphrase: string; env_mode: string }
  setExForm: (v: typeof exForm) => void
  onSave: () => void; onDelete: (id: number) => void
}) {
  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
      <p className="mb-4 text-xs text-gray-500">
        添加交易所 API Key 后即可进行自动交易和资产查询。建议使用子账户并开通交易权限。
      </p>
      {EXCHANGES.map((ex) => {
        const acct = settings.exchange_accounts.find((a) => a.exchange === ex)
        const editing = editEx === ex
        return (
          <div key={ex} className={`${editing ? 'rounded border border-emerald-700 bg-gray-800/50 p-3 mb-3' : 'border-b border-gray-800 last:border-b-0 pb-3 mb-3'}`}>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <span className="text-sm font-medium uppercase text-gray-200">{ex}</span>
                {acct && !editing && (
                  <>
                    <span className="rounded bg-gray-800 px-2 py-0.5 text-xs text-gray-400">
                      {acct.env_mode === 'testnet' ? '模拟盘' : '实盘'}
                    </span>
                    <span className="text-xs text-gray-600">{acct.api_key_masked}</span>
                  </>
                )}
              </div>
              <div className="flex items-center gap-2">
                {!acct && !editing && (
                  <span className="text-xs text-gray-600">未配置</span>
                )}
                <button
                  onClick={() => {
                    if (editing) { setEditEx(null); return }
                    setEditEx(ex)
                    setExForm({
                      api_key: '',
                      secret: '',
                      passphrase: '',
                      env_mode: acct?.env_mode || 'testnet',
                    })
                  }}
                  className="rounded px-3 py-1 text-xs font-medium transition bg-gray-800 text-gray-300 hover:bg-gray-700 hover:text-white"
                >
                  {editing ? '取消' : acct ? '修改' : '配置'}
                </button>
                {acct && !editing && (
                  <button
                    onClick={() => onDelete(acct.id)}
                    className="rounded px-2 py-1 text-xs text-gray-600 hover:bg-red-900/30 hover:text-red-400 transition"
                  >
                    删除
                  </button>
                )}
              </div>
            </div>

            {editing && (
              <div className="mt-3 space-y-3">
                {acct && (
                  <div className="rounded bg-gray-800/70 px-3 py-2 text-xs text-gray-400">
                    <span>当前：Key {acct.api_key_masked}</span>
                    {acct.secret_masked && <span className="ml-3">Secret {acct.secret_masked}</span>}
                    {acct.passphrase_masked && <span className="ml-3">Passphrase {acct.passphrase_masked}</span>}
                    <span className="ml-3">· {acct.env_mode === 'testnet' ? '模拟盘' : '实盘'}</span>
                    <p className="mt-1 text-gray-600">下方填写新 Key 将覆盖当前配置</p>
                  </div>
                )}
                <div>
                  <label className="mb-1 block text-xs text-gray-500">环境</label>
                  <div className="flex gap-2">
                    {ENV_MODES.map((mode) => (
                      <button
                        key={mode.value}
                        type="button"
                        onClick={() => setExForm({ ...exForm, env_mode: mode.value })}
                        className={`rounded px-3 py-1 text-xs font-medium border ${
                          exForm.env_mode === mode.value
                            ? 'border-emerald-600 bg-emerald-900/50 text-emerald-400'
                            : 'border-gray-700 text-gray-500 hover:text-gray-300'
                        }`}
                      >
                        {mode.label}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="grid grid-cols-1 gap-3">
                  <div>
                    <label className="mb-1 block text-xs text-gray-500">API Key</label>
                    <input
                      value={exForm.api_key}
                      onChange={(e) => setExForm({ ...exForm, api_key: e.target.value })}
                      className="w-full rounded border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-emerald-500 focus:outline-none"
                      placeholder={acct ? '留空则不修改' : '输入 API Key'}
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-xs text-gray-500">Secret Key</label>
                    <input
                      type="password"
                      value={exForm.secret}
                      onChange={(e) => setExForm({ ...exForm, secret: e.target.value })}
                      className="w-full rounded border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-emerald-500 focus:outline-none"
                      placeholder={acct ? '留空则不修改' : '输入 Secret'}
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-xs text-gray-500">Passphrase {ex !== 'okx' && '(可选)'}</label>
                    <input
                      type="password"
                      value={exForm.passphrase}
                      onChange={(e) => setExForm({ ...exForm, passphrase: e.target.value })}
                      className="w-full rounded border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-emerald-500 focus:outline-none"
                      placeholder={acct ? '留空则不修改' : ex === 'okx' ? 'OKX 必填 Passphrase' : '可选'}
                    />
                  </div>
                </div>
                <button
                  onClick={onSave}
                  className="w-full rounded bg-emerald-600 py-2 text-sm font-medium hover:bg-emerald-500"
                >
                  保存 {ex} API
                </button>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

function BalanceTab({
  balances, loadingBal, onRefresh,
  liquidating, liquidateResult, showLiqConfirm, setShowLiqConfirm, onLiquidate, closeResult,
}: {
  balances: BalancesData | null
  loadingBal: boolean
  onRefresh: () => void
  liquidating: boolean
  liquidateResult: LiquidateResult | null
  showLiqConfirm: boolean
  setShowLiqConfirm: (v: boolean) => void
  onLiquidate: () => void
  closeResult: () => void
}) {
  const nonUsdtCount = balances?.exchanges.reduce((c, e) => c + e.assets.filter(a => a.currency !== 'USDT').length, 0) ?? 0
  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h3 className="text-sm font-medium text-gray-300">资产概览</h3>
          <p className="text-xs text-gray-600">实时查询各交易所账户余额</p>
        </div>
        <div className="flex items-center gap-2">
          {nonUsdtCount > 0 && (
            <button
              onClick={() => setShowLiqConfirm(true)}
              disabled={liquidating}
              className="rounded bg-red-900/40 px-3 py-1.5 text-xs font-medium text-red-400 hover:bg-red-800/60 disabled:opacity-40 border border-red-800/50"
            >
              {liquidating ? '清仓中...' : '一键清仓'}
            </button>
          )}
          <button
            onClick={onRefresh}
            disabled={loadingBal}
            className="rounded bg-gray-800 px-4 py-1.5 text-xs font-medium text-gray-300 hover:bg-gray-700 disabled:opacity-50"
          >
            {loadingBal ? '查询中...' : '刷新'}
          </button>
        </div>
      </div>

      {showLiqConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="mx-4 w-full max-w-md rounded-lg border border-gray-700 bg-gray-900 p-6 shadow-xl">
            <h3 className="mb-2 text-lg font-semibold text-red-400">确认一键清仓</h3>
            <p className="mb-4 text-sm text-gray-400">
              将对所有交易所的非 USDT 资产按当前市价卖出，换成 USDT。<br />
              此操作不可撤销，确定继续？
            </p>
            <div className="flex gap-3 justify-end">
              <button onClick={() => setShowLiqConfirm(false)} className="rounded bg-gray-800 px-4 py-2 text-sm text-gray-300 hover:bg-gray-700">取消</button>
              <button onClick={onLiquidate} className="rounded bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-500">确认清仓</button>
            </div>
          </div>
        </div>
      )}

      {liquidateResult && (
        <div className="mb-5 rounded-lg border border-gray-700 bg-gray-800/50 p-4">
          <div className="flex items-center justify-between mb-3">
            <h4 className="text-sm font-medium text-gray-300">清仓结果</h4>
            <button onClick={closeResult} className="text-xs text-gray-500 hover:text-gray-300">关闭</button>
          </div>
          <div className="flex gap-4 mb-3 text-xs">
            <span className="text-gray-400">成交 <span className="text-emerald-400 font-bold">{liquidateResult.summary.orders_placed}</span> 笔</span>
            <span className="text-gray-400">卖出 <span className="text-emerald-400 font-bold">${liquidateResult.summary.total_sold_usdt.toLocaleString()}</span></span>
            {liquidateResult.summary.errors > 0 && <span className="text-red-400">失败 {liquidateResult.summary.errors} 笔</span>}
          </div>
          {liquidateResult.exchanges.map((ex) => ex.orders.length > 0 && (
            <div key={ex.exchange} className="mb-2 last:mb-0">
              <div className="text-xs text-gray-500 mb-1 uppercase">{ex.exchange} {ex.env_mode}</div>
              <div className="flex flex-wrap gap-1.5">
                {ex.orders.map((o, i) => (
                  <span key={i} className={`rounded px-2 py-0.5 text-xs font-mono ${o.status === 'closed' || o.status === 'open' ? 'bg-emerald-900/40 text-emerald-400' : o.status === 'error' ? 'bg-red-900/30 text-red-400' : 'bg-gray-800 text-gray-500'}`}>
                    {o.symbol} {o.status === 'closed' || o.status === 'open' ? `$${(o.cost ?? 0).toLocaleString()}` : (o.reason ?? o.status)}
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {balances ? (
        <div>
          <div className="mb-5 rounded-lg bg-gray-800/50 p-4 text-center">
            <p className="text-xs text-gray-500 mb-1">资产总额</p>
            <span className="text-3xl font-bold text-emerald-400">
              ${balances.total_usdt.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </span>
            <p className="mt-1 text-xs text-gray-600">折算 USDT</p>
          </div>

          {balances.exchanges.length === 0 ? (
            <div className="rounded-lg border border-dashed border-gray-700 py-8 text-center">
              <p className="text-sm text-gray-500">暂未配置交易所 API</p>
              <p className="mt-1 text-xs text-gray-600">前往「API 管理」添加交易所密钥</p>
            </div>
          ) : (
            <div className="space-y-3">
              {balances.exchanges.map((ex) => (
                <div key={ex.exchange} className="rounded-lg border border-gray-700 bg-gray-800/30 overflow-hidden">
                  <div className="flex items-center justify-between bg-gray-800/50 px-4 py-2.5">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-semibold uppercase text-gray-200">{ex.exchange}</span>
                      <span className="rounded bg-gray-700 px-1.5 py-0.5 text-xs text-gray-400">{ex.env_mode}</span>
                    </div>
                    <span className={`text-sm font-medium ${ex.total_usdt > 0 ? 'text-gray-200' : 'text-gray-500'}`}>
                      ${ex.total_usdt.toLocaleString('en-US', { minimumFractionDigits: 2 })}
                    </span>
                  </div>

                  {ex.error ? (
                    <div className="px-4 py-3 text-xs text-red-400">{ex.error}</div>
                  ) : ex.assets.length === 0 ? (
                    <div className="px-4 py-3 text-xs text-gray-600">暂无资产</div>
                  ) : (
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="border-b border-gray-700/50 text-gray-500">
                          <th className="px-4 py-2 text-left font-medium">币种</th>
                          <th className="px-4 py-2 text-right font-medium">可用</th>
                          <th className="px-4 py-2 text-right font-medium">冻结</th>
                          <th className="px-4 py-2 text-right font-medium">估值 (USDT)</th>
                        </tr>
                      </thead>
                      <tbody>
                        {ex.assets.map((a, i) => {
                          const pct = ex.total_usdt > 0 ? ((a.usdt_value / ex.total_usdt) * 100).toFixed(0) : '0'
                          return (
                            <tr key={a.currency} className={`${i % 2 === 0 ? 'bg-gray-800/20' : ''}`}>
                              <td className="px-4 py-2 font-medium text-gray-300">{a.currency}</td>
                              <td className="px-4 py-2 text-right text-gray-400 font-mono">
                                {a.free < 0.0001 ? a.free.toFixed(8) : a.free.toLocaleString('en-US', { maximumFractionDigits: 4 })}
                              </td>
                              <td className="px-4 py-2 text-right text-gray-500 font-mono">
                                {a.used < 0.0001 ? a.used.toFixed(8) : a.used.toLocaleString('en-US', { maximumFractionDigits: 4 })}
                              </td>
                              <td className="px-4 py-2 text-right text-gray-300">
                                <span className="font-mono">${a.usdt_value.toFixed(2)}</span>
                                <span className="ml-1.5 text-gray-600">{pct}%</span>
                              </td>
                            </tr>
                          )
                        })}
                      </tbody>
                    </table>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      ) : loadingBal ? (
        <div className="flex items-center justify-center py-12 text-gray-500">
          <div className="h-6 w-6 animate-spin rounded-full border-2 border-gray-700 border-t-emerald-500" />
          <span className="ml-3 text-sm">正在查询余额...</span>
        </div>
      ) : (
        <div className="py-8 text-center text-gray-500 text-sm">点击刷新加载资产</div>
      )}
    </div>
  )
}
