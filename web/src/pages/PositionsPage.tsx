import { useCallback, useEffect, useState } from 'react'
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { getPnlHistory, getPositions, type PositionItem } from '../api'

function PnlChart() {
  const [data, setData] = useState<{ date: string; cumulative_pnl: number }[]>([])
  const [totalPnl, setTotalPnl] = useState(0)
  const [days, setDays] = useState(30)

  const load = useCallback(() => {
    getPnlHistory(days).then((res) => {
      setData(res.points)
      setTotalPnl(res.total_realized_pnl)
    })
  }, [days])

  useEffect(() => { load() }, [load])

  return (
    <div className="mb-6 rounded-lg border border-gray-800 bg-gray-900 p-4">
      <div className="mb-3 flex items-center justify-between">
        <div>
          <h3 className="text-sm font-medium text-gray-300">累计已实现盈亏</h3>
          <span className={`text-lg font-bold ${totalPnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
            {totalPnl >= 0 ? '+' : ''}{totalPnl.toFixed(2)} USDT
          </span>
        </div>
        <div className="flex gap-1">
          {[7, 30, 90].map((d) => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={`rounded px-2 py-0.5 text-xs ${
                days === d
                  ? 'bg-emerald-600 text-white'
                  : 'bg-gray-800 text-gray-400 hover:text-white'
              }`}
            >
              {d}天
            </button>
          ))}
        </div>
      </div>
      {data.length === 0 ? (
        <p className="py-6 text-center text-xs text-gray-600">暂无平仓记录</p>
      ) : (
        <ResponsiveContainer width="100%" height={200}>
          <AreaChart data={data} margin={{ top: 5, right: 5, left: 5, bottom: 5 }}>
            <defs>
              <linearGradient id="pnlColor" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#10b981" stopOpacity={0.3} />
                <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
            <XAxis
              dataKey="date"
              tick={{ fontSize: 10, fill: '#6b7280' }}
              tickLine={false}
              axisLine={false}
              interval="preserveStartEnd"
            />
            <YAxis
              tick={{ fontSize: 10, fill: '#6b7280' }}
              tickLine={false}
              axisLine={false}
              tickFormatter={(v: number) => `${v >= 0 ? '+' : ''}${v.toFixed(0)}`}
              width={60}
            />
            <Tooltip
              contentStyle={{
                backgroundColor: '#111827',
                border: '1px solid #374151',
                borderRadius: 6,
                fontSize: 12,
              }}
              formatter={(value: any) => [`${Number(value) >= 0 ? '+' : ''}${Number(value).toFixed(2)} USDT`, '累计盈亏']}
            />
            <Area
              type="monotone"
              dataKey="cumulative_pnl"
              stroke="#10b981"
              strokeWidth={2}
              fill="url(#pnlColor)"
            />
          </AreaChart>
        </ResponsiveContainer>
      )}
    </div>
  )
}

function PositionsTable() {
  const [items, setItems] = useState<PositionItem[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    getPositions({ page_size: 100 })
      .then((res) => setItems(res.items))
      .finally(() => setLoading(false))
  }, [])

  if (loading)
    return <p className="py-8 text-center text-gray-500">加载中...</p>

  if (items.length === 0)
    return <p className="py-8 text-center text-gray-500">暂无持仓</p>

  return (
    <div className="overflow-x-auto rounded-lg border border-gray-800">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-gray-800 bg-gray-900 text-left text-gray-400">
            <th className="px-3 py-3">币种</th>
            <th className="px-3 py-3">交易所</th>
            <th className="px-3 py-3">方向</th>
            <th className="px-3 py-3 text-right">成交金额</th>
            <th className="px-3 py-3 text-right">开仓价差</th>
            <th className="px-3 py-3 text-right">已实现盈亏</th>
            <th className="px-3 py-3 text-right">未实现盈亏</th>
            <th className="px-3 py-3 text-right">手续费</th>
            <th className="px-3 py-3">状态</th>
            <th className="px-3 py-3">恢复状态</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.task_uuid} className="border-b border-gray-800 hover:bg-gray-900/50">
              <td className="px-3 py-3 font-medium">{item.symbol}</td>
              <td className="px-3 py-3 text-gray-400">
                {item.spot_exchange} / {item.derivative_exchange}
              </td>
              <td className="px-3 py-3">
                {item.task_type === 'open' ? '开仓' : '平仓'}
              </td>
              <td className="px-3 py-3 text-right">
                {(item.filled_notional || item.target_notional).toFixed(0)} USDT
              </td>
              <td className="px-3 py-3 text-right">
                {(item.expected_spread_bps / 100).toFixed(2)}%
              </td>
              <td className={`px-3 py-3 text-right ${(item.realized_pnl ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                {item.realized_pnl != null ? `${item.realized_pnl.toFixed(2)} USDT` : '--'}
              </td>
              <td className={`px-3 py-3 text-right ${(item.unrealized_pnl ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                {item.unrealized_pnl != null ? `${item.unrealized_pnl.toFixed(2)} USDT` : '--'}
              </td>
              <td className="px-3 py-3 text-right text-gray-500">
                {item.total_fee != null ? `${item.total_fee.toFixed(4)}` : '--'}
              </td>
              <td className="px-3 py-3">{item.status}</td>
              <td className="px-3 py-3">{item.auto_recovery_status}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function PositionsPage() {
  return (
    <div className="mx-auto max-w-7xl px-4 py-6">
      <h2 className="mb-4 text-lg font-semibold">我的持仓</h2>
      <PnlChart />
      <PositionsTable />
    </div>
  )
}
