import { useEffect, useState } from 'react'
import { getSwitches, putSwitch, deleteSwitch, type PlatformSwitch } from '../../api'

export function SwitchesPage() {
  const [switches, setSwitches] = useState<PlatformSwitch[]>([])
  const [loading, setLoading] = useState(true)
  const [showForm, setShowForm] = useState(false)

  const reload = () => {
    getSwitches().then((res) => setSwitches(res.switches)).finally(() => setLoading(false))
  }
  useEffect(reload, [])

  const handleCreate = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const fd = new FormData(e.currentTarget)
    const switchId = `${fd.get('switch_key')}:${fd.get('scope_type')}:${fd.get('scope_id')}`
    await putSwitch(switchId, true)
    setShowForm(false)
    reload()
  }

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-lg font-semibold">平台开关</h2>
        <button onClick={() => setShowForm(!showForm)} className="rounded bg-emerald-600 px-3 py-1.5 text-sm hover:bg-emerald-500">
          {showForm ? '取消' : '新增开关'}
        </button>
      </div>

      {showForm && (
        <form onSubmit={handleCreate} className="mb-4 rounded-lg border border-gray-800 p-4 grid grid-cols-3 gap-3 text-sm">
          <input name="switch_key" placeholder="switch_key (reduce_only/suspend_new)" required className="rounded bg-gray-800 border border-gray-700 px-3 py-2 text-gray-300" />
          <input name="scope_type" placeholder="scope_type (platform/user/symbol)" required className="rounded bg-gray-800 border border-gray-700 px-3 py-2 text-gray-300" />
          <input name="scope_id" placeholder="scope_id" required className="rounded bg-gray-800 border border-gray-700 px-3 py-2 text-gray-300" />
          <button type="submit" className="col-span-3 rounded bg-emerald-600 py-2 hover:bg-emerald-500">保存</button>
        </form>
      )}

      {loading ? <p className="text-gray-500">加载中...</p> : (
        <div className="overflow-x-auto rounded-lg border border-gray-800">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800 bg-gray-900 text-left text-gray-400">
                <th className="px-3 py-2">switch_key</th><th className="px-3 py-2">scope_type</th><th className="px-3 py-2">scope_id</th>
                <th className="px-3 py-2">enabled</th><th className="px-3 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {switches.map((s) => (
                <tr key={`${s.switch_key}:${s.scope_type}:${s.scope_id}`} className="border-b border-gray-800 hover:bg-gray-900/50">
                  <td className="px-3 py-2 font-mono text-xs">{s.switch_key}</td>
                  <td className="px-3 py-2">{s.scope_type}</td>
                  <td className="px-3 py-2">{s.scope_id}</td>
                  <td className="px-3 py-2">{s.enabled ? '✓' : '✗'}</td>
                  <td className="px-3 py-2">
                    <button onClick={async () => { await putSwitch(`${s.switch_key}:${s.scope_type}:${s.scope_id}`, !s.enabled); reload() }} className="mr-2 text-xs text-gray-400 hover:text-white">切换</button>
                    <button onClick={async () => { await deleteSwitch(`${s.switch_key}:${s.scope_type}:${s.scope_id}`); reload() }} className="text-xs text-red-400 hover:text-red-300">删除</button>
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
