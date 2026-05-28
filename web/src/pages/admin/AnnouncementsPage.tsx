import { useEffect, useState } from 'react'
import { getAnnouncements, createAnnouncement, deleteAnnouncement, sendAnnouncement } from '../../api'

export function AnnouncementsPage() {
  const [items, setItems] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [showForm, setShowForm] = useState(false)
  const [sendingId, setSendingId] = useState<number | null>(null)

  const reload = () => {
    getAnnouncements().then((res) => setItems(res.announcements)).finally(() => setLoading(false))
  }
  useEffect(reload, [])

  const handleCreate = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const fd = new FormData(e.currentTarget)
    await createAnnouncement({
      title: fd.get('title') as string,
      content: fd.get('content') as string,
      status: fd.get('status') as string,
    })
    setShowForm(false)
    reload()
  }

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-lg font-semibold">公告管理</h2>
        <button onClick={() => setShowForm(!showForm)} className="rounded bg-emerald-600 px-3 py-1.5 text-sm hover:bg-emerald-500">
          {showForm ? '取消' : '发布公告'}
        </button>
      </div>

      {showForm && (
        <form onSubmit={handleCreate} className="mb-4 rounded-lg border border-gray-800 p-4 flex flex-col gap-3 text-sm">
          <input name="title" placeholder="标题" required className="rounded bg-gray-800 border border-gray-700 px-3 py-2 text-gray-300" />
          <textarea name="content" placeholder="内容" required className="rounded bg-gray-800 border border-gray-700 px-3 py-2 text-gray-300 min-h-[80px]" />
          <select name="status" className="rounded bg-gray-800 border border-gray-700 px-3 py-2 text-gray-300">
            <option value="draft">草稿</option>
            <option value="published">已发布</option>
            <option value="archived">已归档</option>
          </select>
          <button type="submit" className="rounded bg-emerald-600 py-2 hover:bg-emerald-500">保存</button>
        </form>
      )}

      {loading ? <p className="text-gray-500">加载中...</p> : (
        <div className="overflow-x-auto rounded-lg border border-gray-800">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800 bg-gray-900 text-left text-gray-400">
                <th className="px-3 py-2">标题</th><th className="px-3 py-2">状态</th><th className="px-3 py-2">置顶</th><th className="px-3 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {items.map((a) => (
                <tr key={a.id} className="border-b border-gray-800 hover:bg-gray-900/50">
                  <td className="px-3 py-2">{a.title}</td>
                  <td className="px-3 py-2">{a.status}</td>
                  <td className="px-3 py-2">{a.is_pinned ? '✓' : ''}</td>
                  <td className="px-3 py-2">
                    {a.channels_json?.length > 0 && (
                      <button
                        onClick={async () => { setSendingId(a.id); try { await sendAnnouncement(a.id) } finally { setSendingId(null) } }}
                        disabled={sendingId === a.id}
                        className="mr-2 text-xs text-emerald-400 hover:text-emerald-300 disabled:opacity-50"
                      >
                        {sendingId === a.id ? '发送中...' : '推送'}
                      </button>
                    )}
                    <button onClick={async () => { await deleteAnnouncement(a.id); reload() }} className="text-xs text-red-400 hover:text-red-300">删除</button>
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
