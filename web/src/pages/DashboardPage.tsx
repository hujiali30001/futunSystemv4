import { useCallback, useEffect, useState } from 'react'
import { getDashboardSummary, getPnlHistory, getRiskStatus, getLeaderboard, getMe, type DashboardSummary, type RiskStatus, type LeaderboardRow } from '../api'
import { TradeStatusCard } from '../components/TradeStatusCard'
import { useNavigate } from 'react-router-dom'
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

function parseVolume(v: string): number {
  if (!v || v === '--') return 0
  const n = parseFloat(v)
  if (v.endsWith('M')) return n * 1_000_000
  if (v.endsWith('K')) return n * 1_000
  return n
}

function parseFundingRate(v: string): number {
  const m = v.match(/^([+-]?\d+\.?\d*)%/)
  return m ? parseFloat(m[1]) : 0
}

export function DashboardPage() {
  const navigate = useNavigate()
  const [summary, setSummary] = useState<DashboardSummary | null>(null)
  const [risk, setRisk] = useState<RiskStatus | null>(null)
  const [rows, setRows] = useState<LeaderboardRow[]>([])
  const [loading, setLoading] = useState(true)
  const [tradeEnabled, setTradeEnabled] = useState(false)
  const [nodeId, setNodeId] = useState('')
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [refreshInterval, setRefreshInterval] = useState(10)
  const [search, setSearch] = useState('')
  const [minVolume, setMinVolume] = useState('')
  const [minFunding, setMinFunding] = useState('')
  const [fundingOp, setFundingOp] = useState<'gte' | 'lte'>('gte')
  const [page, setPage] = useState(1)
  const pageSize = 15
  const [pnlDays, setPnlDays] = useState(30)
  const [pnlPoints, setPnlPoints] = useState<{ date: string; cumulative_pnl: number }[]>([])
  const [totalPnl, setTotalPnl] = useState(0)
  const [showPnlChart, setShowPnlChart] = useState(false)

  const loadPnl = useCallback(() => {
    getPnlHistory(pnlDays).then((res) => {
      setPnlPoints(res.points)
      setTotalPnl(res.total_realized_pnl)
      setShowPnlChart(true)
    }).catch(() => {})
  }, [pnlDays])

  useEffect(() => { loadPnl() }, [loadPnl])

  const loadData = async () => {
    const [s, r, lb, me] = await Promise.all([
      getDashboardSummary().catch(() => null),
      getRiskStatus().catch(() => null),
      getLeaderboard({ direction: 'spot_futures', page: 1, page_size: 10000 }),
      getMe().catch(() => null),
    ])
    setSummary(s)
    setRisk(r)
    setRows(lb.items)
    if (me) {
      setTradeEnabled(me.is_trading_enabled)
      setNodeId(me.node_id || '')
    }
    setLoading(false)
  }

  useEffect(() => { loadData() }, [])

  useEffect(() => {
    if (!autoRefresh) return
    const timer = setInterval(loadData, refreshInterval * 1000)
    return () => clearInterval(timer)
  }, [autoRefresh, refreshInterval])

  const filtered = rows.filter(r => {
    if (search && !r.symbol.toUpperCase().includes(search.toUpperCase())) return false
    if (minVolume) {
      const vol = Math.max(parseVolume(r.spot_volume), parseVolume(r.deriv_volume))
      if (vol < parseFloat(minVolume)) return false
    }
    if (minFunding) {
      const fr = parseFundingRate(r.funding_rate_display)
      if (fundingOp === 'gte' ? fr < parseFloat(minFunding) : fr > parseFloat(minFunding)) return false
    }
    return true
  })

  const filteredOut = rows.filter(r => {
    return Math.abs(r.open_spread_pct) < 500
  })

  const finalRows = filtered.length === rows.length ? filteredOut : filtered.filter(r => {
    return Math.abs(r.open_spread_pct) < 500
  })

  const totalPages = Math.ceil(finalRows.length / pageSize)
  const paginated = finalRows.slice((page - 1) * pageSize, page * pageSize)

  const statCards = summary ? [
    { label: '活跃策略', value: summary.stats.active_strategies, color: 'text-emerald-400' },
    { label: '持仓', value: summary.stats.open_positions, color: 'text-blue-400' },
    { label: '今日成交', value: summary.stats.today_trades, color: 'text-amber-400' },
    { label: '本周盈亏', value: `${summary.pnl.week >= 0 ? '+' : ''}${summary.pnl.week.toFixed(0)}`, color: summary.pnl.week >= 0 ? 'text-emerald-400' : 'text-red-400', unit: 'USDT' },
  ] : []

  return (
    <div className="mx-auto max-w-7xl px-4 py-6">
      <TradeStatusCard isTradingEnabled={tradeEnabled} nodeId={nodeId} />

      {summary && (
        <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-4">
          {statCards.map((card, i) => (
            <div key={i} className="rounded-lg border border-gray-800 bg-gray-900 p-4">
              <p className="text-xs text-gray-500 mb-1">{card.label}</p>
              <div className="flex items-baseline gap-1">
                <span className={`text-2xl font-bold ${card.color}`}>{card.value}</span>
                {'unit' in card && <span className="text-xs text-gray-600">{card.unit}</span>}
              </div>
            </div>
          ))}
        </div>
      )}

      {summary && (
        <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-4">
          <div className="rounded-lg border border-gray-800 bg-gray-900 p-3">
            <p className="text-xs text-gray-500">今日盈亏</p>
            <p className={`text-lg font-bold mt-0.5 ${summary.pnl.today >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
              {summary.pnl.today >= 0 ? '+' : ''}{summary.pnl.today.toFixed(2)}
            </p>
          </div>
          <div className="rounded-lg border border-gray-800 bg-gray-900 p-3">
            <p className="text-xs text-gray-500">手续费</p>
            <p className="text-lg font-bold mt-0.5 text-gray-400">-{summary.pnl.today_fees.toFixed(2)}</p>
          </div>
          <div className="rounded-lg border border-gray-800 bg-gray-900 p-3">
            <p className="text-xs text-gray-500">今日净盈亏</p>
            <p className={`text-lg font-bold mt-0.5 ${summary.pnl.today_net >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
              {summary.pnl.today_net >= 0 ? '+' : ''}{summary.pnl.today_net.toFixed(2)}
            </p>
          </div>
          <div className="rounded-lg border border-gray-800 bg-gray-900 p-3">
            <p className="text-xs text-gray-500">累计交易</p>
            <p className="text-lg font-bold mt-0.5 text-gray-300">{summary.stats.total_trades}</p>
          </div>
        </div>
      )}

      {risk?.has_alerts && (
        <div className="mb-4 rounded-lg border border-red-800 bg-red-900/20 p-3 flex items-center gap-3">
          <span className="text-lg">⚠️</span>
          <div className="flex-1">
            <p className="text-sm font-medium text-red-400">风控告警</p>
            <p className="text-xs text-red-400/70">
              {risk.daily_loss.exceeded && '日亏损超限 '}
              {risk.stop_loss_alerts.filter(a => a.triggered).length > 0 && `${risk.stop_loss_alerts.filter(a => a.triggered).length} 个策略止损触发`}
            </p>
          </div>
          <button onClick={() => navigate('/settings')} className="rounded bg-red-700 px-3 py-1.5 text-xs font-medium hover:bg-red-600 whitespace-nowrap">
            查看详情
          </button>
        </div>
      )}

      {showPnlChart && pnlPoints.length > 0 && (
        <div className="mb-4 rounded-lg border border-gray-800 bg-gray-900 p-4">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-gray-300">收益走势</span>
              <span className={`text-sm font-semibold ${totalPnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                累计 {totalPnl >= 0 ? '+' : ''}{totalPnl.toFixed(2)} USDT
              </span>
            </div>
            <div className="flex rounded border border-gray-700 bg-gray-800 p-0.5">
              {[7, 30, 90].map((d) => (
                <button
                  key={d}
                  onClick={() => setPnlDays(d)}
                  className={`rounded px-2.5 py-1 text-xs ${pnlDays === d ? 'bg-emerald-600 text-white' : 'text-gray-400 hover:text-white'}`}
                >
                  {d}天
                </button>
              ))}
            </div>
          </div>
          <ResponsiveContainer width="100%" height={180}>
            <AreaChart data={pnlPoints} margin={{ top: 5, right: 5, left: 5, bottom: 5 }}>
              <defs>
                <linearGradient id="dpnlColor" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#10b981" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
              <XAxis dataKey="date" tick={{ fontSize: 10, fill: '#6b7280' }} tickLine={false} axisLine={false} interval="preserveStartEnd" />
              <YAxis tick={{ fontSize: 10, fill: '#6b7280' }} tickLine={false} axisLine={false}
                tickFormatter={(v: number) => `${v >= 0 ? '+' : ''}${v.toFixed(0)}`} width={55} />
              <Tooltip
                contentStyle={{ backgroundColor: '#111827', border: '1px solid #374151', borderRadius: 6, fontSize: 12 }}
                formatter={(value: any) => [`${Number(value) >= 0 ? '+' : ''}${Number(value).toFixed(2)} USDT`, '累计盈亏']}
              />
              <Area type="monotone" dataKey="cumulative_pnl" stroke="#10b981" strokeWidth={2} fill="url(#dpnlColor)" />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}

      <div className="mb-4 flex flex-wrap items-end gap-3">
        <label className="flex items-center gap-1.5 text-xs text-gray-500">
          <input type="checkbox" checked={autoRefresh} onChange={e => setAutoRefresh(e.target.checked)} className="accent-emerald-600" />
          自动刷新
        </label>
        <select value={refreshInterval} onChange={e => setRefreshInterval(Number(e.target.value))} className="rounded border border-gray-700 bg-gray-800 px-2 py-1 text-xs text-gray-400">
          {[5, 10, 30, 60].map(v => <option key={v} value={v}>{v}s</option>)}
        </select>
        <input value={search} onChange={e => { setSearch(e.target.value); setPage(1) }} placeholder="搜索币种..."
          className="rounded border border-gray-700 bg-gray-800 px-3 py-1 text-xs text-white w-28 focus:border-emerald-500 focus:outline-none" />
        <input value={minVolume} onChange={e => { setMinVolume(e.target.value); setPage(1) }} placeholder="最低交易额"
          className="rounded border border-gray-700 bg-gray-800 px-3 py-1 text-xs text-white w-28 focus:border-emerald-500 focus:outline-none" />
        <div className="flex items-center gap-1">
          <select value={fundingOp} onChange={e => setFundingOp(e.target.value as 'gte' | 'lte')} className="rounded border border-gray-700 bg-gray-800 px-1.5 py-1 text-xs text-gray-400">
            <option value="gte">&gt;=</option>
            <option value="lte">&lt;=</option>
          </select>
          <input value={minFunding} onChange={e => { setMinFunding(e.target.value); setPage(1) }} placeholder="费率%"
            className="rounded border border-gray-700 bg-gray-800 px-3 py-1 text-xs text-white w-20 focus:border-emerald-500 focus:outline-none" />
        </div>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-20 text-gray-500">
          <div className="h-6 w-6 animate-spin rounded-full border-2 border-gray-700 border-t-emerald-500" />
        </div>
      ) : (
        <>
          <div className="overflow-x-auto rounded-lg border border-gray-800">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-gray-800 text-gray-500">
                  <th className="px-3 py-2.5 text-left">开清差价</th>
                  <th className="px-3 py-2.5 text-left">币种</th>
                  <th className="px-3 py-2.5 text-left">交易所</th>
                  <th className="px-3 py-2.5 text-right">价格</th>
                  <th className="px-3 py-2.5 text-right">资金费率</th>
                  <th className="px-3 py-2.5 text-right">指数差价</th>
                  <th className="px-3 py-2.5 text-right">24h量</th>
                  <th className="px-3 py-2.5 text-center">操作</th>
                </tr>
              </thead>
              <tbody>
                {paginated.map((r, i) => {
                  return (
                    <tr key={r.symbol + i} className={`border-b border-gray-800/50 ${i % 2 === 0 ? 'bg-gray-900/30' : ''} hover:bg-gray-800/50 transition`}>
                      <td className="px-3 py-2.5">
                        <div className="flex gap-2 text-xs font-mono">
                          <span className={r.open_spread_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}>{r.open_spread_pct.toFixed(2)}%</span>
                          <span className={r.close_spread_pct >= 0 ? 'text-emerald-300' : 'text-red-300'}>{r.close_spread_pct.toFixed(2)}%</span>
                        </div>
                      </td>
                      <td className="px-3 py-2.5 font-medium text-gray-200">{r.symbol}</td>
                      <td className="px-3 py-2.5 text-gray-400">{r.spot_exchange} / {r.derivative_exchange}</td>
                      <td className="px-3 py-2.5 text-right font-mono text-xs text-gray-400">
                        <span className="text-gray-200">{r.spot_price || '--'}</span>
                        <span className="text-gray-600 ml-0.5">现</span>
                        <br />
                        <span className="text-gray-200">{r.deriv_price || '--'}</span>
                        <span className="text-gray-600 ml-0.5">期</span>
                      </td>
                      <td className="px-3 py-2.5 text-right font-mono text-gray-300">
                        {(r.funding_rate_raw * 100).toFixed(4)}%
                      </td>
                      <td className="px-3 py-2.5 text-right font-mono text-gray-500">{r.index_spread_pct?.toFixed(2) ?? '--'}%</td>
                      <td className="px-3 py-2.5 text-right text-gray-500 text-xs">
                        {r.spot_volume || '--'}<span className="text-gray-600 ml-0.5">现</span>
                        <br />
                        {r.deriv_volume || '--'}<span className="text-gray-600 ml-0.5">期</span>
                      </td>
                      <td className="px-3 py-2.5 text-center">
                        <button onClick={() => {
                          const name = `${r.symbol} 期现 ${r.spot_exchange}→${r.derivative_exchange}`
                          navigate(`/strategies?symbol=${r.symbol}&spot_exchange=${r.spot_exchange}&derivative_exchange=${r.derivative_exchange}&open_spread_bps=${Math.round(r.open_spread_pct * 100)}&close_spread_bps=${Math.round(r.close_spread_pct * 100)}&name=${encodeURIComponent(name)}`)
                        }}
                          className="rounded bg-emerald-800/50 px-2.5 py-1 text-xs text-emerald-400 hover:bg-emerald-700/50 transition">
                          套利
                        </button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          {totalPages > 1 && (
            <div className="mt-4 flex items-center justify-center gap-1.5">
              <button disabled={page <= 1} onClick={() => setPage(1)} className="rounded border border-gray-700 px-2.5 py-1 text-xs text-gray-400 hover:text-white disabled:opacity-30">首页</button>
              <button disabled={page <= 1} onClick={() => setPage(p => p - 1)} className="rounded border border-gray-700 px-2.5 py-1 text-xs text-gray-400 hover:text-white disabled:opacity-30">←</button>
              <span className="px-2 text-xs text-gray-500">{page} / {totalPages}</span>
              <button disabled={page >= totalPages} onClick={() => setPage(p => p + 1)} className="rounded border border-gray-700 px-2.5 py-1 text-xs text-gray-400 hover:text-white disabled:opacity-30">→</button>
              <button disabled={page >= totalPages} onClick={() => setPage(totalPages)} className="rounded border border-gray-700 px-2.5 py-1 text-xs text-gray-400 hover:text-white disabled:opacity-30">尾页</button>
            </div>
          )}
        </>
      )}
    </div>
  )
}
