import { useEffect, useState } from 'react'
import axios from 'axios'

const api = axios.create({ baseURL: '/api' })
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('admin_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

interface ConfigRow {
  id: number
  config_key: string
  config_value: string
  config_type: string
  description: string | null
  updated_at: string | null
}

export function ConfigsPage() {
  const [configs, setConfigs] = useState<ConfigRow[]>([])
  const [loading, setLoading] = useState(true)
  const [editingKey, setEditingKey] = useState<string | null>(null)
  const [editValue, setEditValue] = useState('')

  useEffect(() => {
    api.get('/admin/configs')
      .then((res) => setConfigs(res.data))
      .finally(() => setLoading(false))
  }, [])

  const save = async (key: string) => {
    await api.put(`/admin/configs/${key}`, { config_value: editValue })
    setConfigs((prev) =>
      prev.map((c) => (c.config_key === key ? { ...c, config_value: editValue } : c))
    )
    setEditingKey(null)
  }

  if (loading) return <div className="p-6 text-gray-400">加载中...</div>

  return (
    <div className="mx-auto max-w-5xl px-6 py-6">
      <h2 className="mb-4 text-lg font-semibold">平台配置</h2>
      <div className="overflow-x-auto rounded-lg border border-gray-800">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-800 bg-gray-900 text-left text-gray-400">
              <th className="px-3 py-3 w-48">配置键</th>
              <th className="px-3 py-3">值</th>
              <th className="px-3 py-3 w-16">类型</th>
              <th className="px-3 py-3 w-24">更新时间</th>
              <th className="px-3 py-3 w-24">操作</th>
            </tr>
          </thead>
          <tbody>
            {configs.map((row) => (
              <tr key={row.id} className="border-b border-gray-800 hover:bg-gray-900/50">
                <td className="px-3 py-3 font-mono text-xs text-gray-300">{row.config_key}</td>
                <td className="px-3 py-3">
                  {editingKey === row.config_key ? (
                    <input
                      autoFocus
                      className="w-full rounded border border-gray-700 bg-gray-800 px-2 py-1 text-sm text-white"
                      value={editValue}
                      onChange={(e) => setEditValue(e.target.value)}
                      onBlur={() => save(row.config_key)}
                      onKeyDown={(e) => { if (e.key === 'Enter') save(row.config_key) }}
                    />
                  ) : (
                    <span className="text-gray-200 break-all">
                      {row.config_value.length > 80
                        ? row.config_value.slice(0, 80) + '...'
                        : row.config_value}
                    </span>
                  )}
                </td>
                <td className="px-3 py-3 text-xs text-gray-500">{row.config_type}</td>
                <td className="px-3 py-3 text-xs text-gray-500">
                  {row.updated_at ? new Date(row.updated_at).toLocaleString() : '--'}
                </td>
                <td className="px-3 py-3">
                  <button
                    className="text-xs text-blue-400 hover:underline"
                    onClick={() => {
                      setEditingKey(row.config_key)
                      setEditValue(row.config_value)
                    }}
                  >
                    编辑
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
