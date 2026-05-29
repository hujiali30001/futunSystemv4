import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  createStrategy,
  deleteStrategy,
  getStrategies,
  toggleStrategy,
  updateStrategy,
  type Strategy,
} from '../api'
import { TierBar } from '../components/TierBar'

type TierRow = { spread_bps: number; ratio: number }

function resetForm(presetSymbol = '', presetSpot = '', presetDeriv = '', presetOpen = 0, presetClose = 0, presetName = '') {
  return {
    name: presetName,
    symbol: presetSymbol,
    spot: presetSpot,
    deriv: presetDeriv,
    amount: 10,
    openBps: presetOpen || 100,
    closeBps: presetClose || 10,
    tiers: false,
    openTiers: [{ spread_bps: presetOpen || 100, ratio: 1.0 }] as TierRow[],
    closeTiers: [{ spread_bps: presetClose || 10, ratio: 1.0 }] as TierRow[],
  }
}

export function StrategiesPage() {
  const [strategies, setStrategies] = useState<Strategy[]>([])
  const [loading, setLoading] = useState(true)
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()

  const load = useCallback(() => {
    setLoading(true)
    getStrategies()
      .then(setStrategies)
      .catch(() => navigate('/login'))
      .finally(() => setLoading(false))
  }, [navigate])

  useEffect(() => { load() }, [load])

  const presetSymbol = searchParams.get('symbol')
  const presetSpot = searchParams.get('spot_exchange')
  const presetDeriv = searchParams.get('derivative_exchange')
  const presetOpen = Number(searchParams.get('open_spread_bps'))
  const presetClose = Number(searchParams.get('close_spread_bps'))
  const presetName = searchParams.get('name') || ''

  const [editingId, setEditingId] = useState<number | null>(null)
  const [showForm, setShowForm] = useState(!!presetSymbol)
  const [form, setForm] = useState(resetForm(presetSymbol || '', presetSpot || '', presetDeriv || '', presetOpen, presetClose, presetName))
  const [submitting, setSubmitting] = useState(false)

  const openNew = () => {
    setEditingId(null)
    setForm(resetForm())
    setShowForm(true)
  }

  const openEdit = (s: Strategy) => {
    const symbol = s.symbol_scope_json?.[0] || ''
    const spot = s.exchange_scope_json?.[0] || ''
    const deriv = s.exchange_scope_json?.[1] || ''
    const hasTiers = (s.open_tiers_json?.length || 0) > 1 || (s.close_tiers_json?.length || 0) > 1
    setEditingId(s.id)
    setForm({
      name: s.name,
      symbol,
      spot,
      deriv,
      amount: s.target_quote_amount,
      openBps: s.open_spread_bps_threshold,
      closeBps: s.close_spread_bps_threshold,
      tiers: hasTiers,
      openTiers: s.open_tiers_json?.length
        ? s.open_tiers_json.map((t: any) => ({ spread_bps: t.spread_bps, ratio: t.ratio }))
        : [{ spread_bps: s.open_spread_bps_threshold, ratio: 1.0 }],
      closeTiers: s.close_tiers_json?.length
        ? s.close_tiers_json.map((t: any) => ({ spread_bps: t.spread_bps, ratio: t.ratio }))
        : [{ spread_bps: s.close_spread_bps_threshold, ratio: 1.0 }],
    })
    setShowForm(true)
  }

  const closeForm = () => {
    setShowForm(false)
    setEditingId(null)
    navigate('/strategies', { replace: true })
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!form.name || !form.symbol || !form.spot || !form.deriv) return
    setSubmitting(true)
    try {
      const body: any = {
        name: form.name,
        target_quote_amount: form.amount,
        open_spread_bps_threshold: form.openBps,
        close_spread_bps_threshold: form.closeBps,
      }
      if (!editingId) {
        body.symbol = form.symbol
        body.spot_exchange = form.spot
        body.derivative_exchange = form.deriv
      }
      if (form.tiers) {
        body.open_tiers_json = form.openTiers.filter(t => t.ratio > 0)
        body.close_tiers_json = form.closeTiers.filter(t => t.ratio > 0)
      }
      if (editingId) {
        await updateStrategy(editingId, body)
      } else {
        await createStrategy(body)
      }
      closeForm()
      load()
    } finally {
      setSubmitting(false)
    }
  }

  const handleToggle = async (id: number) => {
    await toggleStrategy(id)
    load()
  }

  const handleDelete = async (id: number) => {
    if (!confirm('确定删除？')) return
    await deleteStrategy(id)
    load()
  }

  const formatBps = (bps: number) => `${(bps / 100).toFixed(2)}%`

  return (
    <div className="mx-auto max-w-5xl px-4 py-6">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-bold">我的策略</h1>
        <button
          onClick={() => showForm ? closeForm() : openNew()}
          className="rounded bg-emerald-600 px-4 py-1.5 text-sm font-medium hover:bg-emerald-500"
        >
          {showForm ? '取消' : '新建策略'}
        </button>
      </div>

      {showForm && (
        <form
          onSubmit={handleSubmit}
          className="mb-6 rounded-lg border border-gray-800 bg-gray-900 p-4"
        >
          <h3 className="mb-3 text-sm text-gray-400">{editingId ? '编辑策略' : '新建策略'}</h3>
          <div className="grid grid-cols-2 gap-4 md:grid-cols-3">
            <div>
              <label className="mb-1 block text-xs text-gray-400">策略名称</label>
              <input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                className="w-full rounded border border-gray-700 bg-gray-800 px-2 py-1.5 text-sm text-white focus:border-emerald-500 focus:outline-none"
                placeholder="如 BTC 期现"
                required
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-gray-400">交易对</label>
              <input
                value={form.symbol}
                onChange={(e) => setForm({ ...form, symbol: e.target.value })}
                className="w-full rounded border border-gray-700 bg-gray-800 px-2 py-1.5 text-sm text-white focus:border-emerald-500 focus:outline-none"
                placeholder="BTC/USDT"
                required
                disabled={!!editingId}
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-gray-400">现货交易所</label>
              <input
                value={form.spot}
                onChange={(e) => setForm({ ...form, spot: e.target.value })}
                className="w-full rounded border border-gray-700 bg-gray-800 px-2 py-1.5 text-sm text-white focus:border-emerald-500 focus:outline-none"
                placeholder="binance"
                required
                disabled={!!editingId}
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-gray-400">合约交易所</label>
              <input
                value={form.deriv}
                onChange={(e) => setForm({ ...form, deriv: e.target.value })}
                className="w-full rounded border border-gray-700 bg-gray-800 px-2 py-1.5 text-sm text-white focus:border-emerald-500 focus:outline-none"
                placeholder="bybit"
                required
                disabled={!!editingId}
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-gray-400">投入 USDT</label>
              <input
                type="number"
                value={form.amount}
                onChange={(e) => setForm({ ...form, amount: Number(e.target.value) })}
                className="w-full rounded border border-gray-700 bg-gray-800 px-2 py-1.5 text-sm text-white focus:border-emerald-500 focus:outline-none"
                min={1}
                required
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-gray-400">开仓价差 %</label>
              <input
                type="number"
                step="0.1"
                value={form.openBps / 100}
                onChange={(e) => setForm({ ...form, openBps: Number(e.target.value) * 100 })}
                className="w-full rounded border border-gray-700 bg-gray-800 px-2 py-1.5 text-sm text-white focus:border-emerald-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-gray-400">清仓价差 %</label>
              <input
                type="number"
                step="0.01"
                value={form.closeBps / 100}
                onChange={(e) => setForm({ ...form, closeBps: Number(e.target.value) * 100 })}
                className="w-full rounded border border-gray-700 bg-gray-800 px-2 py-1.5 text-sm text-white focus:border-emerald-500 focus:outline-none"
              />
            </div>
            <div className="flex items-end">
              <button
                type="submit"
                disabled={submitting}
                className="w-full rounded bg-emerald-600 py-1.5 text-sm font-medium hover:bg-emerald-500 disabled:opacity-50"
              >
                {submitting ? '保存中...' : editingId ? '保存修改' : '保存并启动'}
              </button>
            </div>
          </div>

          <div className="mt-3 border-t border-gray-800 pt-3">
            <label className="flex items-center gap-2 text-sm text-gray-400 cursor-pointer">
              <input
                type="checkbox"
                checked={form.tiers}
                onChange={(e) => setForm({ ...form, tiers: e.target.checked })}
              />
              多级开清仓
            </label>
            {form.tiers && (
              <div className="mt-3">
                <div className="mb-2 rounded border border-gray-800 bg-gray-800/50 p-2 space-y-1">
                  <TierBar tiers={form.openTiers} color="green" label="开" />
                  <TierBar tiers={form.closeTiers} color="red" label="清" />
                </div>
                <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                <div>
                  <div className="mb-1 flex items-center justify-between">
                    <span className="text-xs text-gray-500">开仓梯度</span>
                    <button type="button" onClick={() => setForm({ ...form, openTiers: [...form.openTiers, { spread_bps: 100, ratio: 1.0 }] })} className="text-xs text-emerald-400 hover:underline">+ 添加</button>
                  </div>
                  {form.openTiers.map((t, i) => (
                    <div key={i} className="mb-1 flex gap-2">
                      <input
                        type="number" step="1" value={t.spread_bps}
                        onChange={(e) => {
                          const next = [...form.openTiers]; next[i] = { ...t, spread_bps: Number(e.target.value) }; setForm({ ...form, openTiers: next })
                        }}
                        className="w-24 rounded border border-gray-700 bg-gray-800 px-2 py-1 text-xs text-white" placeholder="bps"
                      />
                      <input
                        type="number" step="0.1" min="0" max="1" value={t.ratio}
                        onChange={(e) => {
                          const next = [...form.openTiers]; next[i] = { ...t, ratio: Number(e.target.value) }; setForm({ ...form, openTiers: next })
                        }}
                        className="w-20 rounded border border-gray-700 bg-gray-800 px-2 py-1 text-xs text-white" placeholder="ratio"
                      />
                      {form.openTiers.length > 1 && (
                        <button type="button" onClick={() => setForm({ ...form, openTiers: form.openTiers.filter((_, j) => j !== i) })} className="text-xs text-red-400 hover:underline">删除</button>
                      )}
                    </div>
                  ))}
                  <p className="mt-1 text-xs text-gray-600">bps 阈值  ratio 仓位比例(0-1)</p>
                </div>
                <div>
                  <div className="mb-1 flex items-center justify-between">
                    <span className="text-xs text-gray-500">清仓梯度</span>
                    <button type="button" onClick={() => setForm({ ...form, closeTiers: [...form.closeTiers, { spread_bps: 30, ratio: 1.0 }] })} className="text-xs text-emerald-400 hover:underline">+ 添加</button>
                  </div>
                  {form.closeTiers.map((t, i) => (
                    <div key={i} className="mb-1 flex gap-2">
                      <input
                        type="number" step="1" value={t.spread_bps}
                        onChange={(e) => {
                          const next = [...form.closeTiers]; next[i] = { ...t, spread_bps: Number(e.target.value) }; setForm({ ...form, closeTiers: next })
                        }}
                        className="w-24 rounded border border-gray-700 bg-gray-800 px-2 py-1 text-xs text-white" placeholder="bps"
                      />
                      <input
                        type="number" step="0.1" min="0" max="1" value={t.ratio}
                        onChange={(e) => {
                          const next = [...form.closeTiers]; next[i] = { ...t, ratio: Number(e.target.value) }; setForm({ ...form, closeTiers: next })
                        }}
                        className="w-20 rounded border border-gray-700 bg-gray-800 px-2 py-1 text-xs text-white" placeholder="ratio"
                      />
                      {form.closeTiers.length > 1 && (
                        <button type="button" onClick={() => setForm({ ...form, closeTiers: form.closeTiers.filter((_, j) => j !== i) })} className="text-xs text-red-400 hover:underline">删除</button>
                      )}
                    </div>
                  ))}
                  <p className="mt-1 text-xs text-gray-600">bps 阈值  ratio 仓位比例(0-1)</p>
                </div>
              </div>
              </div>
            )}
          </div>
        </form>
      )}

      {loading ? (
        <p className="py-8 text-center text-gray-500">加载中...</p>
      ) : strategies.length === 0 ? (
        <div className="rounded-lg border border-gray-800 py-12 text-center text-gray-500">
          <p>还没有策略</p>
          <p className="mt-1 text-sm">去排行榜选择一个币对开始套利</p>
        </div>
      ) : (
        <div className="space-y-3">
          {strategies.map((s) => (
            <div
              key={s.id}
              className="flex items-center justify-between rounded-lg border border-gray-800 bg-gray-900 px-4 py-3"
            >
              <div className="flex-1">
                <div className="flex items-center gap-3">
                  <span className="font-medium">{s.name}</span>
                  <span
                    className={`rounded-full px-2 py-0.5 text-xs ${
                      s.is_enabled
                        ? 'bg-emerald-900 text-emerald-400'
                        : 'bg-gray-700 text-gray-400'
                    }`}
                  >
                    {s.is_enabled ? '运行中' : '已暂停'}
                  </span>
                </div>
                <div className="mt-1 flex gap-4 text-xs text-gray-500">
                  <span>{s.symbol_scope_json?.[0] || '-'}</span>
                  <span>{s.exchange_scope_json?.join(' / ') || '-'}</span>
                  <span>资金: {s.target_quote_amount} USDT</span>
                  <span>开: {formatBps(s.open_spread_bps_threshold)}</span>
                  <span>清: {formatBps(s.close_spread_bps_threshold)}</span>
                </div>
                {(s.open_tiers_json?.length || 0) > 1 || (s.close_tiers_json?.length || 0) > 1 ? (
                  <div className="mt-2 space-y-1">
                    <TierBar tiers={s.open_tiers_json?.length > 1 ? s.open_tiers_json : [{ spread_bps: s.open_spread_bps_threshold, ratio: 1 }]} color="green" label="开" />
                    <TierBar tiers={s.close_tiers_json?.length > 1 ? s.close_tiers_json : [{ spread_bps: s.close_spread_bps_threshold, ratio: 1 }]} color="red" label="清" />
                  </div>
                ) : null}
              </div>
              <div className="flex gap-2">
                <button
                  onClick={() => openEdit(s)}
                  className="rounded px-2 py-1 text-xs text-gray-500 hover:bg-gray-800 hover:text-white"
                >
                  编辑
                </button>
                <label className="relative inline-flex cursor-pointer items-center">
                  <input
                    type="checkbox"
                    checked={s.is_enabled}
                    onChange={() => handleToggle(s.id)}
                    className="peer sr-only"
                  />
                  <div className="h-6 w-11 rounded-full bg-gray-700 peer-checked:bg-emerald-600 peer-focus:outline-none after:absolute after:start-[2px] after:top-[2px] after:h-5 after:w-5 after:rounded-full after:bg-white after:transition-all peer-checked:after:translate-x-full" />
                </label>
                <button
                  onClick={() => handleDelete(s.id)}
                  className="rounded px-2 py-1 text-xs text-gray-600 hover:bg-gray-800 hover:text-red-400"
                >
                  删除
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
