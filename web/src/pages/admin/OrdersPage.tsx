import { useState } from 'react'

interface FillItem {
  id: number
  fill_price: number
  fill_amount: number
  fill_cost: number
  fee_cost: number
  fee_currency: string
  exchange_trade_id: string | null
  filled_at: string | null
  side: string
}

interface OrderItem {
  id: number
  leg_type: string
  exchange: string
  side: string
  symbol: string
  order_type: string
  price: number
  amount: number
  status: string
  avg_price: number | null
  filled_amount: number
  fee_cost: number | null
  fee_currency: string | null
  client_order_id: string
  exchange_order_id: string | null
  error_reason: string | null
  created_at: string | null
  fills: FillItem[]
}

export function OrdersPage() {
  const [taskId, setTaskId] = useState('')
  const [result, setResult] = useState<{ task_id: number; orders: OrderItem[] } | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const doSearch = async () => {
    const id = parseInt(taskId, 10)
    if (!id) return
    setLoading(true)
    setError('')
    try {
      const { default: axios } = await import('axios')
      const { data } = await axios.get('/api/admin/orders', {
        params: { task_id: id },
        headers: { Authorization: `Bearer ${localStorage.getItem('admin_token')}` },
      })
      setResult(data)
    } catch (e: any) {
      setError(e.response?.data?.detail || e.message || '查询失败')
      setResult(null)
    } finally {
      setLoading(false)
    }
  }

  const statusColor = (s: string) => {
    if (s === 'closed' || s === 'filled') return 'text-emerald-400'
    if (s === 'canceled' || s === 'rejected') return 'text-red-400'
    return 'text-amber-400'
  }

  return (
    <div>
      <h1 className="mb-4 text-xl font-bold">订单查询</h1>

      <div className="mb-4 flex items-center gap-3">
        <input
          value={taskId}
          onChange={(e) => setTaskId(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && doSearch()}
          placeholder="输入 Task ID"
          className="rounded border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-white w-48 focus:border-emerald-500 focus:outline-none"
        />
        <button
          onClick={doSearch}
          disabled={loading}
          className="rounded bg-emerald-600 px-4 py-2 text-sm font-medium hover:bg-emerald-500 disabled:opacity-50"
        >
          {loading ? '查询中...' : '查询'}
        </button>
      </div>

      {error && <p className="mb-4 text-sm text-red-400">{error}</p>}

      {result && (
        <div>
          <p className="mb-3 text-sm text-gray-500">
            Task #{result.task_id} · {result.orders.length} 笔订单
          </p>

          {result.orders.length === 0 ? (
            <p className="text-gray-500 text-sm">暂无订单记录</p>
          ) : (
            <div className="space-y-4">
              {result.orders.map((o) => (
                <div key={o.id} className="rounded-lg border border-gray-800 bg-gray-900">
                  <div className="px-4 py-3 border-b border-gray-800 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
                    <span className="text-gray-400 font-mono text-xs">#{o.id}</span>
                    <span className={`text-xs font-medium uppercase ${o.side === 'buy' ? 'text-emerald-400' : 'text-red-400'}`}>{o.side}</span>
                    <span className="text-gray-300">{o.symbol}</span>
                    <span className="text-gray-500 text-xs">{o.leg_type} · {o.exchange}</span>
                    <span className="text-gray-500 text-xs">{o.order_type}</span>
                    {o.price != null && <span className="text-gray-400 text-xs">价 {o.price}</span>}
                    <span className="text-gray-400 text-xs">量 {o.amount}</span>
                    <span className={`text-xs font-medium ${statusColor(o.status)}`}>{o.status}</span>
                    {o.avg_price != null && <span className="text-gray-400 text-xs">均价 {o.avg_price}</span>}
                    <span className="text-gray-400 text-xs">成交 {o.filled_amount}</span>
                    {o.fee_cost != null && <span className="text-gray-500 text-xs">费 {o.fee_cost} {o.fee_currency || ''}</span>}
                    {o.error_reason && <span className="text-red-400 text-xs">{o.error_reason}</span>}
                  </div>

                  {o.fills.length > 0 && (
                    <div className="px-4 py-2">
                      <p className="mb-1 text-xs text-gray-600">成交明细</p>
                      <div className="overflow-x-auto">
                        <table className="w-full text-xs">
                          <thead>
                            <tr className="text-gray-500 border-b border-gray-800">
                              <th className="py-1 text-left w-12">#</th>
                              <th className="py-1 text-right">价格</th>
                              <th className="py-1 text-right">数量</th>
                              <th className="py-1 text-right">金额</th>
                              <th className="py-1 text-right">手续费</th>
                              <th className="py-1 text-left">成交ID</th>
                              <th className="py-1 text-left">时间</th>
                            </tr>
                          </thead>
                          <tbody>
                            {o.fills.map((f) => (
                              <tr key={f.id} className="border-b border-gray-800/50 text-gray-400">
                                <td className="py-1">{f.id}</td>
                                <td className="py-1 text-right font-mono">{f.fill_price}</td>
                                <td className="py-1 text-right font-mono">{f.fill_amount}</td>
                                <td className="py-1 text-right font-mono">{f.fill_cost.toFixed(4)}</td>
                                <td className="py-1 text-right font-mono">{f.fee_cost} {f.fee_currency}</td>
                                <td className="py-1 font-mono text-gray-600">{f.exchange_trade_id || '-'}</td>
                                <td className="py-1 text-gray-500">{f.filled_at?.replace('T', ' ').slice(0, 19) || '-'}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
