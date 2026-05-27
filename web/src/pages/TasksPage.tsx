import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getTasks, type TaskItem } from '../api'

const STATUS_COLORS: Record<string, string> = {
  CREATED: 'bg-gray-700 text-gray-300',
  DISPATCHED: 'bg-blue-900 text-blue-400',
  RUNNING: 'bg-yellow-900 text-yellow-400',
  SUCCEEDED: 'bg-emerald-900 text-emerald-400',
  FAILED: 'bg-red-900 text-red-400',
}

export function TasksPage() {
  const [tasks, setTasks] = useState<TaskItem[]>([])
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const pageSize = 20
  const navigate = useNavigate()

  const load = useCallback(() => {
    setLoading(true)
    getTasks({ page, page_size: pageSize })
      .then((res) => {
        setTasks(res.items)
        setTotal(res.total)
      })
      .catch(() => navigate('/login'))
      .finally(() => setLoading(false))
  }, [page, navigate])

  useEffect(() => { load() }, [load])

  const totalPages = Math.ceil(total / pageSize)

  const badgeClass = (status: string) =>
    STATUS_COLORS[status] || 'bg-gray-700 text-gray-300'

  const formatDate = (iso: string | null) => {
    if (!iso) return '-'
    const d = new Date(iso)
    return d.toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  }

  return (
    <div className="mx-auto max-w-7xl px-4 py-6">
      <h1 className="mb-4 text-xl font-bold">我的任务</h1>

      <div className="overflow-x-auto rounded-lg border border-gray-800">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-800 bg-gray-900 text-left text-gray-400">
              <th className="px-4 py-3">任务ID</th>
              <th className="px-4 py-3">类型</th>
              <th className="px-4 py-3">币种</th>
              <th className="px-4 py-3">交易所</th>
              <th className="px-4 py-3 text-right">金额</th>
              <th className="px-4 py-3 text-right">价差</th>
              <th className="px-4 py-3">状态</th>
              <th className="px-4 py-3">时间</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={8} className="px-4 py-8 text-center text-gray-500">
                  加载中...
                </td>
              </tr>
            ) : tasks.length === 0 ? (
              <tr>
                <td colSpan={8} className="px-4 py-8 text-center text-gray-500">
                  暂无任务
                </td>
              </tr>
            ) : (
              tasks.map((t) => (
                <tr key={t.id} className="border-b border-gray-800 hover:bg-gray-900">
                  <td className="max-w-[120px] truncate px-4 py-3 font-mono text-xs text-gray-500">
                    {t.task_uuid}
                  </td>
                  <td className="px-4 py-3">
                    <span className={t.task_type === 'open' ? 'text-emerald-400' : 'text-red-400'}>
                      {t.task_type === 'open' ? '开仓' : '平仓'}
                    </span>
                  </td>
                  <td className="px-4 py-3 font-medium">{t.symbol}</td>
                  <td className="px-4 py-3 text-gray-400">
                    {t.spot_exchange} / {t.derivative_exchange}
                  </td>
                  <td className="px-4 py-3 text-right">{t.target_notional}</td>
                  <td className="px-4 py-3 text-right text-gray-400">
                    {(t.expected_spread_bps / 100).toFixed(2)}%
                  </td>
                  <td className="px-4 py-3">
                    <span className={`rounded px-2 py-0.5 text-xs ${badgeClass(t.status)}`}>
                      {t.status}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-500">
                    {formatDate(t.created_at)}
                  </td>
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
