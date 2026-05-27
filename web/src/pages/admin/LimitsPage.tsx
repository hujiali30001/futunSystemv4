import { useEffect, useState } from 'react'
import { getLimits, createLimit, updateLimit, deleteLimit, type RiskLimitRule } from '../../api'

export function LimitsPage() {
  const [limits, setLimits] = useState<RiskLimitRule[]>([])
  const [loading, setLoading] = useState(true)
  const [showForm, setShowForm] = useState(false)

  const reload = () => {
    getLimits().then((res) => setLimits(res.limits)).finally(() => setLoading(false))
  }
  useEffect(reload, [])

  const handleCreate = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const fd = new FormData(e.currentTarget)
    await createLimit({
      scope_type: fd.get('scope_type') as string,
      scope_id: fd.get('scope_id') as string,
      limit_type: fd.get('limit_type') as string,
      limit_value: Number(fd.get('limit_value')),
    })
    setShowForm(false)
    reload()
  }

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-lg font-semibold">额度规则</h2>
        <button onClick={() => setShowForm(!showForm)} className="rounded bg-emerald-600 px-3 py-1.5 text-sm hover:bg-emerald-500">
          {showForm ? '取消' : '新增规则'}
        </button>
      </div>

      {showForm && (
        <form onSubmit={handleCreate} className="mb-4 rounded-lg border border-gray-800 p-4 grid grid-cols-2 gap-3 text-sm">
          <input name="scope_type" placeholder="scope_type (platform/user/symbol)" required className="rounded bg-gray-800 border border-gray-700 px-3 py-2 text-gray-300" />
          <input name="scope_id" placeholder="scope_id" required className="rounded bg-gray-800 border border-gray-700 px-3 py-2 text-gray-300" />
          <input name="limit_type" placeholder="limit_type" required className="rounded bg-gray-800 border border-gray-700 px-3 py-2 text-gray-300" />
          <input name="limit_value" type="number" placeholder="limit_value" required className="rounded bg-gray-800 border border-gray-700 px-3 py-2 text-gray-300" />
          <button type="submit" className="col-span-2 rounded bg-emerald-600 py-2 hover:bg-emerald-500">保存</button>
        </form>
      )}

      {loading ? (
        <p className="text-gray-500">加载中...</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-gray-800">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800 bg-gray-900 text-left text-gray-400">
                <th className="px-3 py-2">ID</th><th className="px-3 py-2">scope</th><th className="px-3 py-2">limit_type</th>
                <th className="px-3 py-2 text-right">limit_value</th><th className="px-3 py-2">enabled</th>
                <th className="px-3 py-2 text-right">priority</th><th className="px-3 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {limits.map((r) => (
                <tr key={r.id} className="border-b border-gray-800 hover:bg-gray-900/50">
                  <td className="px-3 py-2 text-gray-400">{r.id}</td>
                  <td className="px-3 py-2">{r.scope_type}:{r.scope_id}</td>
                  <td className="px-3 py-2 font-mono text-xs">{r.limit_type}</td>
                  <td className="px-3 py-2 text-right">{r.limit_value.toLocaleString()}</td>
                  <td className="px-3 py-2">{r.enabled ? '✓' : '✗'}</td>
                  <td className="px-3 py-2 text-right">{r.priority}</td>
                  <td className="px-3 py-2">
                    <button onClick={async () => { await updateLimit(r.id, { enabled: !r.enabled }); reload() }} className="mr-2 text-xs text-gray-400 hover:text-white">切换</button>
                    <button onClick={async () => { await deleteLimit(r.id); reload() }} className="text-xs text-red-400 hover:text-red-300">删除</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
