import { useCallback, useEffect, useState } from 'react'
import {
  getSettings,
  updateProfile,
  createExchangeAccount,
  updateExchangeAccount,
  deleteExchangeAccount,
  getBalances,
  type UserSettings,
  type BalancesData,
} from '../api'

type Tab = 'notify' | 'api' | 'balance'

const TABS: { key: Tab; label: string; icon: string }[] = [
  { key: 'notify', label: '通知设置', icon: '🔔' },
  { key: 'api', label: 'API 管理', icon: '🔑' },
  { key: 'balance', label: '资产预览', icon: '💰' },
]

const EXCHANGES = ['binance', 'okx', 'bybit', 'gate', 'bitget']
const ENV_MODES = ['testnet', 'mainnet']

export function SettingsPage() {
  const [tab, setTab] = useState<Tab>('notify')
  const [settings, setSettings] = useState<UserSettings | null>(null)

  const [email, setEmail] = useState('')
  const [feishu, setFeishu] = useState('')
  const [saving, setSaving] = useState(false)
  const [toast, setToast] = useState('')

  const [editEx, setEditEx] = useState<string | null>(null)
  const [exForm, setExForm] = useState({ api_key: '', secret: '', passphrase: '', env_mode: 'testnet' })

  const [balances, setBalances] = useState<BalancesData | null>(null)
  const [loadingBal, setLoadingBal] = useState(false)

  const showToast = (msg: string) => {
    setToast(msg)
    setTimeout(() => setToast(''), 2000)
  }

  const reloadSettings = useCallback(() => {
    getSettings().then((s) => {
      setSettings(s)
      setEmail(s.email || '')
      setFeishu(s.feishu_webhook_url || '')
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
      await updateProfile({ email: email || null, feishu_webhook_url: feishu || null })
      showToast('通知设置已保存')
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

      {tab === 'notify' && <NotifyTab email={email} setEmail={setEmail} feishu={feishu} setFeishu={setFeishu} saving={saving} onSave={saveProfile} />}
      {tab === 'api' && <ApiTab settings={settings} editEx={editEx} setEditEx={setEditEx} exForm={exForm} setExForm={setExForm} onSave={handleExchangeSave} onDelete={handleDelete} />}
      {tab === 'balance' && <BalanceTab balances={balances} loadingBal={loadingBal} onRefresh={refreshBalance} />}
    </div>
  )
}

function NotifyTab({
  email, setEmail, feishu, setFeishu, saving, onSave,
}: {
  email: string; setEmail: (v: string) => void
  feishu: string; setFeishu: (v: string) => void
  saving: boolean; onSave: () => void
}) {
  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
      <p className="mb-4 text-xs text-gray-500">配置通知渠道后，系统将在关键事件发生时向您推送通知。</p>
      <div className="mb-4">
        <label className="mb-1.5 block text-sm font-medium text-gray-300">邮箱地址</label>
        <input
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="w-full rounded border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-emerald-500 focus:outline-none"
          placeholder="user@example.com"
        />
        <p className="mt-1 text-xs text-gray-600">用于接收开仓/平仓/风控通知</p>
      </div>
      <div className="mb-5">
        <label className="mb-1.5 block text-sm font-medium text-gray-300">飞书 Webhook URL</label>
        <input
          value={feishu}
          onChange={(e) => setFeishu(e.target.value)}
          className="w-full rounded border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-emerald-500 focus:outline-none"
          placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..."
        />
        <p className="mt-1 text-xs text-gray-600">创建飞书群机器人 → 复制 Webhook 地址</p>
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
        添加交易所 API Key 后即可进行自动交易和资产查询。建议使用只设置交易权限的子账户。
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
                  <span className="rounded bg-gray-800 px-2 py-0.5 text-xs text-gray-400">{acct.env_mode}</span>
                )}
              </div>
              <div className="flex items-center gap-2">
                {!editing && (
                  <span className="text-xs text-gray-500">
                    {acct ? acct.api_key_masked : '未配置'}
                  </span>
                )}
                <button
                  onClick={() => {
                    setEditEx(editing ? null : ex)
                    if (editing) return
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
                <div>
                  <label className="mb-1 block text-xs text-gray-500">环境模式</label>
                  <div className="flex gap-2">
                    {ENV_MODES.map((mode) => (
                      <button
                        key={mode}
                        onClick={() => setExForm({ ...exForm, env_mode: mode })}
                        className={`rounded px-3 py-1 text-xs font-medium border ${
                          exForm.env_mode === mode
                            ? 'border-emerald-600 bg-emerald-900/50 text-emerald-400'
                            : 'border-gray-700 text-gray-500 hover:text-gray-300'
                        }`}
                      >
                        {mode === 'testnet' ? '测试网' : '主网'}
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
                      placeholder="输入 API Key"
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-xs text-gray-500">Secret Key</label>
                    <input
                      type="password"
                      value={exForm.secret}
                      onChange={(e) => setExForm({ ...exForm, secret: e.target.value })}
                      className="w-full rounded border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-emerald-500 focus:outline-none"
                      placeholder="输入 Secret"
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-xs text-gray-500">Passphrase {ex !== 'okx' && '(可选)'}</label>
                    <input
                      type="password"
                      value={exForm.passphrase}
                      onChange={(e) => setExForm({ ...exForm, passphrase: e.target.value })}
                      className="w-full rounded border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white focus:border-emerald-500 focus:outline-none"
                      placeholder={ex === 'okx' ? 'OKX 必须填写 Passphrase' : '可选'}
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
}: {
  balances: BalancesData | null
  loadingBal: boolean
  onRefresh: () => void
}) {
  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h3 className="text-sm font-medium text-gray-300">资产概览</h3>
          <p className="text-xs text-gray-600">实时查询各交易所账户余额</p>
        </div>
        <button
          onClick={onRefresh}
          disabled={loadingBal}
          className="rounded bg-gray-800 px-4 py-1.5 text-xs font-medium text-gray-300 hover:bg-gray-700 disabled:opacity-50"
        >
          {loadingBal ? '查询中...' : '刷新'}
        </button>
      </div>

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
