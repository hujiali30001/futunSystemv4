import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  createStrategy,
  deleteStrategy,
  getStrategies,
  toggleStrategy,
  type Strategy,
} from '../api'

type TierRow = { spread_bps: number; ratio: number }

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
  const presetOpen = searchParams.get('open_spread_bps')
  const presetClose = searchParams.get('close_spread_bps')

  const [showForm, setShowForm] = useState(!!presetSymbol)
  const [formName, setFormName] = useState('')
  const [formSymbol, setFormSymbol] = useState(presetSymbol || '')
  const [formSpot, setFormSpot] = useState(presetSpot || '')
  const [formDeriv, setFormDeriv] = useState(presetDeriv || '')
  const [formAmount, setFormAmount] = useState(10)
  const [formOpenBps, setFormOpenBps] = useState(Number(presetOpen) || 100)
  const [formCloseBps, setFormCloseBps] = useState(Number(presetClose) || 10)
  const [submitting, setSubmitting] = useState(false)
  const [useTiers, setUseTiers] = useState(false)
  const [openTiers, setOpenTiers] = useState<TierRow[]>([
    { spread_bps: formOpenBps, ratio: 1.0 },
  ])
  const [closeTiers, setCloseTiers] = useState<TierRow[]>([
    { spread_bps: formCloseBps, ratio: 1.0 },
  ])

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!formName || !formSymbol || !formSpot || !formDeriv) return
    setSubmitting(true)
    try {
      const body: any = {
        name: formName,
        symbol: formSymbol,
        spot_exchange: formSpot,
        derivative_exchange: formDeriv,
        target_quote_amount: formAmount,
        open_spread_bps_threshold: formOpenBps,
        close_spread_bps_threshold: formCloseBps,
      }
      if (useTiers) {
        body.open_tiers_json = openTiers.filter(t => t.ratio > 0)
        body.close_tiers_json = closeTiers.filter(t => t.ratio > 0)
      }
      await createStrategy(body)
      setShowForm(false)
      setFormName('')
      setFormSymbol('')
      load()
      navigate('/strategies', { replace: true })
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
          onClick={() => { setShowForm(!showForm); navigate('/strategies', { replace: true }) }}
          className="rounded bg-emerald-600 px-4 py-1.5 text-sm font-medium hover:bg-emerald-500"
        >
          {showForm ? '取消' : '新建策略'}
        </button>
      </div>

      {showForm && (
        <form
          onSubmit={handleCreate}
          className="mb-6 rounded-lg border border-gray-800 bg-gray-900 p-4"
        >
          <div className="grid grid-cols-2 gap-4 md:grid-cols-3">
            <div>
              <label className="mb-1 block text-xs text-gray-400">策略名称</label>
              <input
                value={formName}
                onChange={(e) => setFormName(e.target.value)}
                className="w-full rounded border border-gray-700 bg-gray-800 px-2 py-1.5 text-sm text-white focus:border-emerald-500 focus:outline-none"
                placeholder="如 BTC 期现"
                required
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-gray-400">交易对</label>
              <input
                value={formSymbol}
                onChange={(e) => setFormSymbol(e.target.value)}
                className="w-full rounded border border-gray-700 bg-gray-800 px-2 py-1.5 text-sm text-white focus:border-emerald-500 focus:outline-none"
                placeholder="BTC/USDT"
                required
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-gray-400">现货交易所</label>
              <input
                value={formSpot}
                onChange={(e) => setFormSpot(e.target.value)}
                className="w-full rounded border border-gray-700 bg-gray-800 px-2 py-1.5 text-sm text-white focus:border-emerald-500 focus:outline-none"
                placeholder="binance"
                required
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-gray-400">合约交易所</label>
              <input
                value={formDeriv}
                onChange={(e) => setFormDeriv(e.target.value)}
                className="w-full rounded border border-gray-700 bg-gray-800 px-2 py-1.5 text-sm text-white focus:border-emerald-500 focus:outline-none"
                placeholder="bybit"
                required
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-gray-400">投入 USDT</label>
              <input
                type="number"
                value={formAmount}
                onChange={(e) => setFormAmount(Number(e.target.value))}
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
                value={formOpenBps / 100}
                onChange={(e) => setFormOpenBps(Number(e.target.value) * 100)}
                className="w-full rounded border border-gray-700 bg-gray-800 px-2 py-1.5 text-sm text-white focus:border-emerald-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs text-gray-400">清仓价差 %</label>
              <input
                type="number"
                step="0.01"
                value={formCloseBps / 100}
                onChange={(e) => setFormCloseBps(Number(e.target.value) * 100)}
                className="w-full rounded border border-gray-700 bg-gray-800 px-2 py-1.5 text-sm text-white focus:border-emerald-500 focus:outline-none"
              />
            </div>
            <div className="flex items-end">
              <button
                type="submit"
                disabled={submitting}
                className="w-full rounded bg-emerald-600 py-1.5 text-sm font-medium hover:bg-emerald-500 disabled:opacity-50"
              >
                {submitting ? '保存中...' : '保存并启动'}
              </button>
            </div>
          </div>

          <div className="mt-3 border-t border-gray-800 pt-3">
            <label className="flex items-center gap-2 text-sm text-gray-400 cursor-pointer">
              <input
                type="checkbox"
                checked={useTiers}
                onChange={(e) => setUseTiers(e.target.checked)}
              />
              多级开清仓
            </label>
            {useTiers && (
              <div className="mt-3 grid grid-cols-1 gap-4 md:grid-cols-2">
                <div>
                  <div className="mb-1 flex items-center justify-between">
                    <span className="text-xs text-gray-500">开仓梯度</span>
                    <button
                      type="button"
                      onClick={() =>
                        setOpenTiers([...openTiers, { spread_bps: 200, ratio: 1.0 }])
                      }
                      className="text-xs text-emerald-400 hover:underline"
                    >
                      + 添加
                    </button>
                  </div>
                  {openTiers.map((t, i) => (
                    <div key={i} className="mb-1 flex gap-2">
                      <input
                        type="number"
                        step="1"
                        value={t.spread_bps}
                        onChange={(e) => {
                          const next = [...openTiers]
                          next[i] = { ...t, spread_bps: Number(e.target.value) }
                          setOpenTiers(next)
                        }}
                        className="w-24 rounded border border-gray-700 bg-gray-800 px-2 py-1 text-xs text-white"
                        placeholder="bps"
                      />
                      <input
                        type="number"
                        step="0.1"
                        min="0"
                        max="1"
                        value={t.ratio}
                        onChange={(e) => {
                          const next = [...openTiers]
                          next[i] = { ...t, ratio: Number(e.target.value) }
                          setOpenTiers(next)
                        }}
                        className="w-20 rounded border border-gray-700 bg-gray-800 px-2 py-1 text-xs text-white"
                        placeholder="ratio"
                      />
                      {openTiers.length > 1 && (
                        <button
                          type="button"
                          onClick={() => setOpenTiers(openTiers.filter((_, j) => j !== i))}
                          className="text-xs text-red-400 hover:underline"
                        >
                          删除
                        </button>
                      )}
                    </div>
                  ))}
                  <p className="mt-1 text-xs text-gray-600">bps 阈值  ratio 仓位比例(0-1)</p>
                </div>
                <div>
                  <div className="mb-1 flex items-center justify-between">
                    <span className="text-xs text-gray-500">清仓梯度</span>
                    <button
                      type="button"
                      onClick={() =>
                        setCloseTiers([...closeTiers, { spread_bps: 50, ratio: 1.0 }])
                      }
                      className="text-xs text-emerald-400 hover:underline"
                    >
                      + 添加
                    </button>
                  </div>
                  {closeTiers.map((t, i) => (
                    <div key={i} className="mb-1 flex gap-2">
                      <input
                        type="number"
                        step="1"
                        value={t.spread_bps}
                        onChange={(e) => {
                          const next = [...closeTiers]
                          next[i] = { ...t, spread_bps: Number(e.target.value) }
                          setCloseTiers(next)
                        }}
                        className="w-24 rounded border border-gray-700 bg-gray-800 px-2 py-1 text-xs text-white"
                        placeholder="bps"
                      />
                      <input
                        type="number"
                        step="0.1"
                        min="0"
                        max="1"
                        value={t.ratio}
                        onChange={(e) => {
                          const next = [...closeTiers]
                          next[i] = { ...t, ratio: Number(e.target.value) }
                          setCloseTiers(next)
                        }}
                        className="w-20 rounded border border-gray-700 bg-gray-800 px-2 py-1 text-xs text-white"
                        placeholder="ratio"
                      />
                      {closeTiers.length > 1 && (
                        <button
                          type="button"
                          onClick={() => setCloseTiers(closeTiers.filter((_, j) => j !== i))}
                          className="text-xs text-red-400 hover:underline"
                        >
                          删除
                        </button>
                      )}
                    </div>
                  ))}
                  <p className="mt-1 text-xs text-gray-600">bps 阈值  ratio 仓位比例(0-1)</p>
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
                  {(s.open_tiers_json?.length || 0) > 1 && (
                    <span className="text-emerald-500">
                      {s.open_tiers_json.length}级开{(s.close_tiers_json?.length || 0) > 1 ? ` / ${s.close_tiers_json.length}级清` : ''}
                    </span>
                  )}
                </div>
              </div>
              <div className="flex gap-2">
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
