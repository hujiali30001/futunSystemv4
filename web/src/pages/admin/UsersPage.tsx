import { useEffect, useState } from 'react'
import { getAdminUsers, type UserInfo } from '../../api'

export function AdminUsersPage() {
  const [items, setItems] = useState<UserInfo[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    getAdminUsers({ page_size: 50 }).then((res) => setItems(res.items)).finally(() => setLoading(false))
  }, [])

  return (
    <div>
      <h2 className="mb-4 text-lg font-semibold">用户管理</h2>
      {loading ? <p className="text-gray-500">加载中...</p> : (
        <div className="overflow-x-auto rounded-lg border border-gray-800">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800 bg-gray-900 text-left text-gray-400">
                <th className="px-3 py-2">ID</th><th className="px-3 py-2">用户名</th><th className="px-3 py-2">状态</th><th className="px-3 py-2">节点</th><th className="px-3 py-2">交易</th>
              </tr>
            </thead>
            <tbody>
              {items.map((u) => (
                <tr key={u.id} className="border-b border-gray-800 hover:bg-gray-900/50">
                  <td className="px-3 py-2 text-gray-400">{u.id}</td>
                  <td className="px-3 py-2">{u.username}</td>
                  <td className="px-3 py-2">{u.status}</td>
                  <td className="px-3 py-2 text-xs text-gray-400">{u.node_id || 'main'}</td>
                  <td className="px-3 py-2">{u.is_trading_enabled ? '✓' : '✗'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
