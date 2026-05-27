import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { type LeaderboardRow } from '../api'
import { useWebSocket } from '../hooks/useWebSocket'

type Direction = 'spot_futures' | 'futures_spot'

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

export function LeaderboardPage() {
  const [rows, setRows] = useState<LeaderboardRow[]>([])
  const [direction, setDirection] = useState<Direction>('spot_futures')
  const [loading, setLoading] = useState(true)
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [refreshInterval, setRefreshInterval] = useState(10)
  const [search, setSearch] = useState('')
  const [minVolume, setMinVolume] = useState('')
  const [minFunding, setMinFunding] = useState('')
  const [fundingOp, setFundingOp] = useState<'gte' | 'lte'>('gte')
  const [pinned, setPinned] = useState<Set<string>>(new Set())
  const [page, setPage] = useState(1)
  const [jumpPage, setJumpPage] = useState('')
  const displayPageSize = 15
  const navigate = useNavigate()
  const token = localStorage.getItem('token')

  const pinKey = (r: LeaderboardRow) =>
    `${r.full_symbol}|${r.spot_exchange}|${r.derivative_exchange}`

  const togglePin = (row: LeaderboardRow) => {
    setPinned((prev) => {
      const next = new Set(prev)
      const k = pinKey(row)
      if (next.has(k)) next.delete(k)
      else next.add(k)
      return next
    })
  }

  const filtered = (() => {
    let list = rows
    const q = search.trim().toUpperCase()
    if (q) {
      list = list.filter((r) => r.symbol.toUpperCase().includes(q))
    }
    if (minVolume) {
      const threshold = parseFloat(minVolume)
      if (!isNaN(threshold) && threshold > 0) {
        list = list.filter(
          (r) =>
            parseVolume(r.spot_volume) >= threshold ||
            parseVolume(r.deriv_volume) >= threshold,
        )
      }
    }
    if (minFunding) {
      const threshold = parseFloat(minFunding)
      if (!isNaN(threshold)) {
        if (fundingOp === 'gte') {
          list = list.filter(
            (r) => parseFundingRate(r.funding_rate_display) >= threshold,
          )
        } else {
          list = list.filter(
            (r) => parseFundingRate(r.funding_rate_display) <= threshold,
          )
        }
      }
    }
    const pinnedList = list.filter((r) => pinned.has(pinKey(r)))
    const rest = list.filter((r) => !pinned.has(pinKey(r)))
    return [...pinnedList, ...rest]
  })()

  const filteredTotal = filtered.length
  const totalPages = Math.ceil(filteredTotal / displayPageSize)
  const pageItems = filtered.slice(
    (page - 1) * displayPageSize,
    page * displayPageSize,
  )

  const wsEnabled = autoRefresh

  useWebSocket(direction, (items: LeaderboardRow[]) => {
    setRows(items)
    setLoading(false)
  }, wsEnabled)

  useEffect(() => {
    setPage(1)
  }, [search, minVolume, minFunding, direction])

  const handleStart = (row: LeaderboardRow) => {
    const params = new URLSearchParams({
      symbol: row.full_symbol,
      spot_exchange: row.spot_exchange,
      derivative_exchange: row.derivative_exchange,
    })
    navigate(`/strategies?${params.toString()}`)
  }

  const switchDir = (dir: Direction) => {
    setDirection(dir)
    setPage(1)
  }

  const tabClass = (dir: Direction) =>
    direction === dir
      ? 'border-b-2 border-emerald-400 text-white pb-2 px-1'
      : 'text-gray-500 pb-2 px-1 hover:text-gray-300'

  return (
    <div className="mx-auto max-w-7xl px-4 py-6">
      <div className="mb-4 flex items-center gap-6">
        <button
          onClick={() => switchDir('spot_futures')}
          className={tabClass('spot_futures')}
        >
          现期排行榜
        </button>
        <button
          onClick={() => switchDir('futures_spot')}
          className={tabClass('futures_spot')}
        >
          期现排行榜
        </button>
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-3 text-sm">
        <button
          onClick={() => setAutoRefresh((v) => !v)}
          className={`rounded px-3 py-1.5 font-medium ${
            autoRefresh
              ? 'bg-emerald-600 text-white hover:bg-emerald-500'
              : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
          }`}
        >
          {autoRefresh ? '⏸ 暂停刷新' : '▶ 恢复刷新'}
        </button>
        {autoRefresh && (
          <select
            value={refreshInterval}
            onChange={(e) => setRefreshInterval(Number(e.target.value))}
            className="rounded bg-gray-800 border border-gray-700 px-2 py-1.5 text-gray-300"
          >
            <option value={5}>5s</option>
            <option value={10}>10s</option>
            <option value={30}>30s</option>
            <option value={60}>60s</option>
          </select>
        )}
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="搜索币种..."
          className="rounded bg-gray-800 border border-gray-700 px-3 py-1.5 text-gray-300 placeholder-gray-600 w-36"
        />
        <input
          value={minVolume}
          onChange={(e) => setMinVolume(e.target.value)}
          placeholder="24h交易额 ≥"
          className="rounded bg-gray-800 border border-gray-700 px-3 py-1.5 text-gray-300 placeholder-gray-600 w-32"
        />
        <select
          value={fundingOp}
          onChange={(e) => setFundingOp(e.target.value as 'gte' | 'lte')}
          className="rounded bg-gray-800 border border-gray-700 px-2 py-1.5 text-gray-300"
        >
          <option value="gte">≥</option>
          <option value="lte">≤</option>
        </select>
        <input
          value={minFunding}
          onChange={(e) => setMinFunding(e.target.value)}
          placeholder="费率阈值 %"
          className="rounded bg-gray-800 border border-gray-700 px-3 py-1.5 text-gray-300 placeholder-gray-600 w-32"
        />
        {pinned.size > 0 && (
          <button
            onClick={() => setPinned(new Set())}
            className="rounded bg-gray-700 px-2 py-1 text-xs text-gray-400 hover:bg-gray-600"
          >
            取消置顶 ({pinned.size})
          </button>
        )}
      </div>

      <div className="overflow-x-auto rounded-lg border border-gray-800">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-800 bg-gray-900 text-left text-gray-400">
              <th className="px-3 py-3 w-[150px]">开清差价</th>
              <th className="px-3 py-3">币种名称</th>
              <th className="px-3 py-3">交易所</th>
              <th className="px-3 py-3 text-right">当前价格</th>
              <th className="px-3 py-3">资金费率</th>
              <th className="px-3 py-3 text-right">指数差价(%)</th>
              <th className="px-3 py-3 text-right">24h交易额</th>
              {token && <th className="px-3 py-3 text-center w-[80px]">操作</th>}
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={token ? 8 : 7} className="px-3 py-10 text-center text-gray-500">
                  加载中...
                </td>
              </tr>
            ) : filtered.length === 0 ? (
              <tr>
                <td colSpan={token ? 8 : 7} className="px-3 py-10 text-center text-gray-500">
                  暂无数据
                </td>
              </tr>
            ) : (
              pageItems.map((row) => {
                const isPinned = pinned.has(pinKey(row))
                return (
                  <tr
                    key={`${pinKey(row)}`}
                    onClick={() => togglePin(row)}
                    className={`cursor-pointer border-b border-gray-800 hover:bg-gray-900/50 ${
                      isPinned ? 'bg-emerald-900/20' : ''
                    }`}
                  >
                    <td className="px-3 py-3 font-mono text-sm">
                      <span
                        className={
                          row.open_spread_pct > 0 ? 'text-emerald-400' : 'text-red-400'
                        }
                      >
                        {row.open_spread_pct.toFixed(2)}%
                      </span>
                      <span className="text-gray-500"> / </span>
                      <span
                        className={
                          row.close_spread_pct > 0 ? 'text-emerald-400' : 'text-red-400'
                        }
                      >
                        {row.close_spread_pct.toFixed(2)}%
                      </span>
                    </td>
                    <td className="px-3 py-3 font-medium">
                      {isPinned && (
                        <span className="mr-1 text-emerald-400" title="已置顶">
                          📌
                        </span>
                      )}
                      {row.symbol}
                    </td>
                    <td className="px-3 py-3 text-gray-400">
                      {row.spot_exchange} / {row.derivative_exchange}
                    </td>
                    <td className="px-3 py-3 text-right font-mono text-xs text-gray-400">
                      {row.spot_price || '--'}
                      <br />
                      {row.deriv_price || '--'}
                    </td>
                    <td className="px-3 py-3 font-mono text-sm text-gray-300">
                      {row.funding_rate_display}
                    </td>
                    <td className="px-3 py-3 text-right text-gray-500">
                      {row.index_spread_pct !== 0
                        ? `${row.index_spread_pct > 0 ? '+' : ''}${row.index_spread_pct.toFixed(
                            3,
                          )}%`
                        : '--'}
                    </td>
                    <td className="px-3 py-3 text-right text-gray-500">
                      {row.spot_volume || '--'} / {row.deriv_volume || '--'}
                    </td>
                    {token && (
                      <td className="px-3 py-3 text-center">
                        <button
                          onClick={(e) => {
                            e.stopPropagation()
                            handleStart(row)
                          }}
                          className="rounded bg-emerald-600 px-3 py-1 text-xs font-medium hover:bg-emerald-500"
                        >
                          开始套利
                        </button>
                      </td>
                    )}
                  </tr>
                )
              })
            )}
          </tbody>
        </table>
      </div>

      <div className="mt-4 flex items-center justify-center gap-3 text-sm">
        <button
          onClick={() => setPage(1)}
          disabled={page <= 1}
          className="rounded bg-gray-800 px-2.5 py-1.5 disabled:opacity-30 hover:bg-gray-700"
        >
          首页
        </button>
        <button
          onClick={() => setPage((p) => Math.max(1, p - 1))}
          disabled={page <= 1}
          className="rounded bg-gray-800 px-3 py-1.5 disabled:opacity-30 hover:bg-gray-700"
        >
          上一页
        </button>
        {Array.from({ length: Math.min(totalPages, 7) }, (_, i) => {
          let p: number
          if (totalPages <= 7) {
            p = i + 1
          } else if (page <= 4) {
            p = i + 1
          } else if (page >= totalPages - 3) {
            p = totalPages - 6 + i
          } else {
            p = page - 3 + i
          }
          return (
            <button
              key={p}
              onClick={() => setPage(p)}
              className={`rounded px-2.5 py-1 ${
                p === page
                  ? 'bg-emerald-600 text-white'
                  : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
              }`}
            >
              {p}
            </button>
          )
        })}
        <button
          onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
          disabled={page >= totalPages}
          className="rounded bg-gray-800 px-3 py-1.5 disabled:opacity-30 hover:bg-gray-700"
        >
          下一页
        </button>
        <button
          onClick={() => setPage(totalPages)}
          disabled={page >= totalPages}
          className="rounded bg-gray-800 px-2.5 py-1.5 disabled:opacity-30 hover:bg-gray-700"
        >
          尾页
        </button>
        <span className="text-gray-500">/ {totalPages}</span>
        <input
          value={jumpPage}
          onChange={(e) => setJumpPage(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              const n = parseInt(jumpPage, 10)
              if (!isNaN(n) && n >= 1 && n <= totalPages) {
                setPage(n)
                setJumpPage('')
              }
            }
          }}
          placeholder="跳到"
          className="w-14 rounded bg-gray-800 border border-gray-700 px-2 py-1.5 text-center text-gray-300 placeholder-gray-600"
        />
      </div>

      <p className="mt-2 text-center text-xs text-gray-600">
        显示 {filteredTotal} 条{autoRefresh && ` · 每 ${refreshInterval}s 刷新`}
      </p>
    </div>
  )
}
