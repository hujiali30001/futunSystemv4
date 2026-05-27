import { useEffect, useState } from 'react'
import { getPositions, type PositionItem } from '../api'

export function PositionsPage() {
  const [items, setItems] = useState<PositionItem[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    getPositions({ page_size: 100 })
      .then((res) => setItems(res.items))
      .finally(() => setLoading(false))
  }, [])

  if (loading)
    return <div className="p-6 text-gray-500">加载中...</div>
  if (items.length === 0)
    return <div className="p-6 text-gray-500">暂无持仓</div>

  return (
    <div className="mx-auto max-w-7xl px-4 py-6">
      <h2 className="mb-4 text-lg font-semibold">我的持仓</h2>
      <div className="overflow-x-auto rounded-lg border border-gray-800">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-800 bg-gray-900 text-left text-gray-400">
              <th className="px-3 py-3">币种</th>
              <th className="px-3 py-3">交易所</th>
              <th className="px-3 py-3">方向</th>
              <th className="px-3 py-3 text-right">名义金额</th>
              <th className="px-3 py-3 text-right">开仓价差</th>
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
                  {item.target_notional.toFixed(0)} USDT
                </td>
                <td className="px-3 py-3 text-right">
                  {(item.expected_spread_bps / 100).toFixed(2)}%
                </td>
                <td className="px-3 py-3">{item.status}</td>
                <td className="px-3 py-3">{item.auto_recovery_status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
