import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getOpportunities, type Opportunity } from '../api'

export function LeaderboardPage() {
  const [items, setItems] = useState<Opportunity[]>([])
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [sortBy, setSortBy] = useState<'open_spread_bps' | 'close_spread_bps'>('open_spread_bps')
  const [loading, setLoading] = useState(true)
  const pageSize = 20
  const navigate = useNavigate()
  const token = localStorage.getItem('token')

  useEffect(() => {
    setLoading(true)
    getOpportunities({ page, page_size: pageSize, sort_by: sortBy })
      .then((res) => {
        setItems(res.items)
        setTotal(res.total)
      })
      .finally(() => setLoading(false))
  }, [page, sortBy])

  const totalPages = Math.ceil(total / pageSize)

  const handleStart = (opp: Opportunity) => {
    const params = new URLSearchParams({
      symbol: opp.symbol,
      spot_exchange: opp.spot_exchange,
      derivative_exchange: opp.derivative_exchange,
      open_spread_bps: opp.open_spread_bps.toString(),
      close_spread_bps: opp.close_spread_bps.toString(),
    })
    navigate(`/strategies?${params.toString()}`)
  }

  const formatBps = (bps: number) => {
    const pct = bps / 100
    const color = pct > 0 ? 'text-emerald-400' : pct < 0 ? 'text-red-400' : 'text-gray-400'
    return <span className={color}>{pct.toFixed(2)}%</span>
  }

  return (
    <div className="mx-auto max-w-7xl px-4 py-6">
      <h1 className="mb-4 text-xl font-bold">资金费率套利排行</h1>

      <div className="mb-3 flex gap-2">
        <button
          onClick={() => { setSortBy('open_spread_bps'); setPage(1) }}
          className={`rounded px-3 py-1.5 text-sm ${
            sortBy === 'open_spread_bps'
              ? 'bg-emerald-600 text-white'
              : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
          }`}
        >
          按开仓价差
        </button>
        <button
          onClick={() => { setSortBy('close_spread_bps'); setPage(1) }}
          className={`rounded px-3 py-1.5 text-sm ${
            sortBy === 'close_spread_bps'
              ? 'bg-emerald-600 text-white'
              : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
          }`}
        >
          按清仓价差
        </button>
      </div>

      <div className="overflow-x-auto rounded-lg border border-gray-800">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-800 bg-gray-900 text-left text-gray-400">
              <th className="px-4 py-3">币种</th>
              <th className="px-4 py-3">交易所对</th>
              <th className="px-4 py-3 text-right">资金费率</th>
              <th className="px-4 py-3 text-right">开仓价差</th>
              <th className="px-4 py-3 text-right">清仓价差</th>
              {token && <th className="px-4 py-3 text-center">操作</th>}
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={token ? 6 : 5} className="px-4 py-8 text-center text-gray-500">
                  加载中...
                </td>
              </tr>
            ) : items.length === 0 ? (
              <tr>
                <td colSpan={token ? 6 : 5} className="px-4 py-8 text-center text-gray-500">
                  暂无数��
                </td>
              </tr>
            ) : (
              items.map((opp, i) => (
                <tr
                  key={`${opp.symbol}-${opp.spot_exchange}-${opp.derivative_exchange}-${i}`}
                  className="border-b border-gray-800 hover:bg-gray-900"
                >
                  <td className="px-4 py-3 font-medium">{opp.symbol}</td>
                  <td className="px-4 py-3 text-gray-400">
                    {opp.spot_exchange} / {opp.derivative_exchange}
                  </td>
                  <td className="px-4 py-3 text-right text-gray-400">
                    {(opp.funding_rate * 100).toFixed(4)}%
                  </td>
                  <td className="px-4 py-3 text-right">{formatBps(opp.open_spread_bps)}</td>
                  <td className="px-4 py-3 text-right">{formatBps(opp.close_spread_bps)}</td>
                  {token && (
                    <td className="px-4 py-3 text-center">
                      <button
                        onClick={() => handleStart(opp)}
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

      {totalPages > 1 && (
        <div className="mt-4 flex items-center justify-center gap-2">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page <= 1}
            className="rounded bg-gray-800 px-3 py-1.5 text-sm disabled:opacity-30"
          >
            上一页
          </button>
          <span className="text-sm text-gray-400">
            {page} / {totalPages}
          </span>
          <button
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            disabled={page >= totalPages}
            className="rounded bg-gray-800 px-3 py-1.5 text-sm disabled:opacity-30"
          >
            下一页
          </button>
        </div>
      )}
    </div>
  )
}
