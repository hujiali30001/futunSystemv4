import { useEffect, useState } from 'react'
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

const EXCHANGES = ['binance', 'okx', 'bybit', 'gate', 'bitget']

export function SettingsPage() {
  const [settings, setSettings] = useState<UserSettings | null>(null)
  const [email, setEmail] = useState('')
  const [feishu, setFeishu] = useState('')
  const [saving, setSaving] = useState(false)
  const [editEx, setEditEx] = useState<string | null>(null)
  const [exForm, setExForm] = useState({ api_key: '', secret: '', passphrase: '' })

  const [balances, setBalances] = useState<BalancesData | null>(null)
  const [loadingBal, setLoadingBal] = useState(false)

  useEffect(() => {
    getSettings().then((s) => {
      setSettings(s)
      setEmail(s.email || '')
      setFeishu(s.feishu_webhook_url || '')
    })
  }, [])

  const refreshBalance = async () => {
    setLoadingBal(true)
    try {
      const b = await getBalances()
      setBalances(b)
    } finally {
      setLoadingBal(false)
    }
  }

  useEffect(() => { refreshBalance() }, [])

  const saveProfile = async () => {
    setSaving(true)
    await updateProfile({ email: email || null, feishu_webhook_url: feishu || null })
    setSaving(false)
  }

  const handleExchangeSave = async () => {
    if (!editEx) return
    const existing = settings?.exchange_accounts.find((a) => a.exchange === editEx)
    if (existing) {
      await updateExchangeAccount(existing.id, exForm)
    } else {
      await createExchangeAccount({ exchange: editEx, ...exForm })
    }
    setEditEx(null)
    getSettings().then((s) => setSettings(s))
  }

  if (!settings) return <div className="p-6 text-gray-500">加载中...</div>

  return (
    <div className="mx-auto max-w-3xl px-4 py-6">
      <h2 className="mb-6 text-lg font-semibold">个人设置</h2>

      <div className="mb-6 rounded-lg border border-gray-800 p-4">
        <h3 className="mb-3 text-sm text-gray-400">通知渠道</h3>
        <div className="mb-3">
          <label className="mb-1 block text-xs text-gray-500">邮箱</label>
          <input
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full rounded bg-gray-800 border border-gray-700 px-3 py-2 text-gray-300"
            placeholder="user@example.com"
          />
        </div>
        <div className="mb-3">
          <label className="mb-1 block text-xs text-gray-500">飞书 Webhook URL</label>
          <input
            value={feishu}
            onChange={(e) => setFeishu(e.target.value)}
            className="w-full rounded bg-gray-800 border border-gray-700 px-3 py-2 text-gray-300"
            placeholder="https://open.feishu.cn/..."
          />
        </div>
        <button
          onClick={saveProfile}
          disabled={saving}
          className="rounded bg-emerald-600 px-4 py-2 text-sm font-medium hover:bg-emerald-500 disabled:opacity-50"
        >
          {saving ? '保存中...' : '保存通知设置'}
        </button>
      </div>

      <div className="rounded-lg border border-gray-800 p-4">
        <h3 className="mb-3 text-sm text-gray-400">交易所 API</h3>
        {EXCHANGES.map((ex) => {
          const acct = settings.exchange_accounts.find((a) => a.exchange === ex)
          const editing = editEx === ex
          return (
            <div key={ex} className="mb-2 border-b border-gray-800 pb-2">
              <div className="flex items-center justify-between">
                <span className="font-mono text-sm text-gray-300">{ex}</span>
                <span className="text-xs text-gray-500">
                  {acct ? `${acct.api_key_masked} | ${acct.env_mode}` : '未配置'}
                </span>
                <button
                  onClick={() => {
                    setEditEx(editing ? null : ex)
                    setExForm({ api_key: '', secret: '', passphrase: '' })
                  }}
                  className="text-xs text-gray-400 hover:text-white"
                >
                  {editing ? '取消' : acct ? '修改' : '添加'}
                </button>
                {acct && (
                  <button
                    onClick={async () => {
                      await deleteExchangeAccount(acct.id)
                      getSettings().then((s) => setSettings(s))
                    }}
                    className="text-xs text-red-400 hover:text-red-300"
                  >
                    删除
                  </button>
                )}
              </div>
              {editing && (
                <div className="mt-2 grid grid-cols-3 gap-2">
                  <input
                    placeholder="API Key"
                    value={exForm.api_key}
                    onChange={(e) => setExForm({ ...exForm, api_key: e.target.value })}
                    className="rounded bg-gray-800 border border-gray-700 px-2 py-1 text-xs text-gray-300"
                  />
                  <input
                    placeholder="Secret"
                    value={exForm.secret}
                    onChange={(e) => setExForm({ ...exForm, secret: e.target.value })}
                    className="rounded bg-gray-800 border border-gray-700 px-2 py-1 text-xs text-gray-300"
                  />
                  <input
                    placeholder="Passphrase (可选)"
                    value={exForm.passphrase}
                    onChange={(e) => setExForm({ ...exForm, passphrase: e.target.value })}
                    className="rounded bg-gray-800 border border-gray-700 px-2 py-1 text-xs text-gray-300"
                  />
                  <button
                    onClick={handleExchangeSave}
                    className="col-span-3 rounded bg-emerald-600 py-1 text-xs hover:bg-emerald-500"
                  >
                    保存
                  </button>
                </div>
              )}
            </div>
          )
        })}
      </div>

      <div className="rounded-lg border border-gray-800 p-4">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-sm text-gray-400">资产概览</h3>
          <button
            onClick={refreshBalance}
            disabled={loadingBal}
            className="rounded bg-gray-800 px-3 py-1 text-xs text-gray-400 hover:text-white disabled:opacity-50"
          >
            {loadingBal ? '刷新中...' : '刷新'}
          </button>
        </div>
        {balances ? (
          <div>
            <div className="mb-3 text-center">
              <span className="text-2xl font-bold text-emerald-400">
                ${balances.total_usdt.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </span>
              <p className="text-xs text-gray-500">资产总额 (USDT)</p>
            </div>
            {balances.exchanges.length === 0 ? (
              <p className="text-center text-xs text-gray-600">请先配置交易所 API</p>
            ) : (
              balances.exchanges.map((ex) => (
                <div key={ex.exchange} className="mb-3 rounded border border-gray-700 p-3">
                  <div className="mb-1 flex items-center justify-between">
                    <span className="font-mono text-sm text-gray-300">{ex.exchange}</span>
                    <span className="text-sm text-gray-400">
                      {ex.error ? (
                        <span className="text-red-400 text-xs">{ex.error}</span>
                      ) : (
                        `${ex.env_mode} · $${ex.total_usdt.toLocaleString('en-US', { minimumFractionDigits: 2 })}`
                      )}
                    </span>
                  </div>
                  {ex.assets.length > 0 && (
                    <div className="mt-2 overflow-x-auto">
                      <table className="w-full text-xs">
                        <thead>
                          <tr className="text-gray-500">
                            <th className="py-1 text-left">币种</th>
                            <th className="py-1 text-right">可用</th>
                            <th className="py-1 text-right">冻结</th>
                            <th className="py-1 text-right">估值(USDT)</th>
                          </tr>
                        </thead>
                        <tbody>
                          {ex.assets.map((a) => (
                            <tr key={a.currency} className="border-t border-gray-800">
                              <td className="py-1 text-gray-300">{a.currency}</td>
                              <td className="py-1 text-right text-gray-400">{a.free < 0.0001 ? a.free.toFixed(8) : a.free.toFixed(4)}</td>
                              <td className="py-1 text-right text-gray-500">{a.used < 0.0001 ? a.used.toFixed(8) : a.used.toFixed(4)}</td>
                              <td className="py-1 text-right text-gray-300">${a.usdt_value.toFixed(2)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        ) : loadingBal ? (
          <p className="text-center text-xs text-gray-500">加载中...</p>
        ) : null}
      </div>
    </div>
  )
}
