import { useEffect, useState } from 'react'
import { getAudit, type AuditLogItem } from '../../api'

export function AuditPage() {
  const [items, setItems] = useState<AuditLogItem[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    getAudit({ page_size: 50 }).then((res) => setItems(res.items)).finally(() => setLoading(false))
  }, [])

  return (
    <div>
      <h2 className="mb-4 text-lg font-semibold">审计日志</h2>
      {loading ? <p className="text-gray-500">加载中...</p> : (
        <div className="overflow-x-auto rounded-lg border border-gray-800">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800 bg-gray-900 text-left text-gray-400">
                <th className="px-3 py-2">时间</th><th className="px-3 py-2">管理员</th><th className="px-3 py-2">操作</th>
                <th className="px-3 py-2">目标</th><th className="px-3 py-2">详情</th>
              </tr>
            </thead>
            <tbody>
              {items.map((a) => (
                <tr key={a.id} className="border-b border-gray-800 hover:bg-gray-900/50">
                  <td className="px-3 py-2 text-xs text-gray-400">{a.created_at?.slice(0, 19)}</td>
                  <td className="px-3 py-2 text-gray-400">{a.admin_user_id}</td>
                  <td className="px-3 py-2 font-mono text-xs">{a.action_type}</td>
                  <td className="px-3 py-2 font-mono text-xs">{a.target_type}:{a.target_id}</td>
                  <td className="px-3 py-2 text-xs text-gray-500">{JSON.stringify(a.after_json).slice(0, 60)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
