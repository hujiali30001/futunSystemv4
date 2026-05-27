import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getLeaderboard, type LeaderboardRow } from '../api'

type Direction = 'spot_futures' | 'futures_spot'

export function LeaderboardPage() {
  const [rows, setRows] = useState<LeaderboardRow[]>([])
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [direction, setDirection] = useState<Direction>('spot_futures')
  const [loading, setLoading] = useState(true)
  const pageSize = 20
  const navigate = useNavigate()
  const token = localStorage.getItem('token')

  const load = useCallback(() => {
    setLoading(true)
    getLeaderboard({ direction, page, page_size: pageSize })
      .then((res) => {
        setRows(res.items)
        setTotal(res.total)
      })
      .finally(() => setLoading(false))
  }, [direction, page])

  useEffect(() => { load() }, [load])

  const totalPages = Math.ceil(total / pageSize)

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
        <button onClick={() => switchDir('spot_futures')} className={tabClass('spot_futures')}>
          现期排行榜
        </button>
        <button onClick={() => switchDir('futures_spot')} className={tabClass('futures_spot')}>
          期现排行榜
        </button>
      </div>

      <div className="overflow-x-auto rounded-lg border border-gray-800">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-800 bg-gray-900 text-left text-gray-400">
              <th className="px-3 py-3 w-[150px]">开清差价</th>
              <th className="px-3 py-3">币种名称</th>
              <th className="px-3 py-3">交易所</th>
              <th className="px-3 py-3">资金费率</th>
              <th className="px-3 py-3 text-right">指数差价(%)</th>
              <th className="px-3 py-3 text-right">24h交易额</th>
              {token && <th className="px-3 py-3 text-center w-[80px]">操作</th>}
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={token ? 7 : 6} className="px-3 py-10 text-center text-gray-500">
                  加载中...
                </td>
              </tr>
            ) : rows.length === 0 ? (
              <tr>
                <td colSpan={token ? 7 : 6} className="px-3 py-10 text-center text-gray-500">
                  暂无数据
                </td>
              </tr>
            ) : (
              rows.map((row, i) => (
                <tr
                  key={`${row.full_symbol}-${row.spot_exchange}-${row.derivative_exchange}-${i}`}
                  className="border-b border-gray-800 hover:bg-gray-900/50"
                >
                  <td className="px-3 py-3 font-mono text-sm">
                    <span className={row.open_yield_pct > 0 ? 'text-emerald-400' : 'text-red-400'}>
                      {row.open_yield_pct.toFixed(2)}%
                    </span>
                    <span className="text-gray-500"> / </span>
                    <span className={row.close_yield_pct > 0 ? 'text-emerald-400' : 'text-red-400'}>
                      {row.close_yield_pct.toFixed(2)}%
                    </span>
                  </td>
                  <td className="px-3 py-3 font-medium">{row.symbol}</td>
                  <td className="px-3 py-3 text-gray-400">
                    {row.spot_exchange} / {row.derivative_exchange}
                  </td>
                  <td className="px-3 py-3 font-mono text-sm text-gray-300">
                    {row.funding_rate_display}
                  </td>
                  <td className="px-3 py-3 text-right text-gray-500">
                    {row.index_spread_pct !== 0
                      ? `${row.index_spread_pct > 0 ? '+' : ''}${row.index_spread_pct.toFixed(3)}%`
                      : '--'}
                  </td>
                  <td className="px-3 py-3 text-right text-gray-500">
                    {row.spot_volume || '--'} / {row.deriv_volume || '--'}
                  </td>
                  {token && (
                    <td className="px-3 py-3 text-center">
                      <button
                        onClick={() => handleStart(row)}
                        className="rounded bg-emerald-600 px-3 py-1 text-xs font-medium hover:bg-emerald-500"
                      >
                        开始套利
                      </button>
                    </td>
                  )}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <div className="mt-4 flex items-center justify-center gap-3 text-sm">
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
      </div>

      <p className="mt-2 text-center text-xs text-gray-600">共 {total} 条</p>
    </div>
  )
}
