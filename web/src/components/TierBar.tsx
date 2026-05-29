type TierItem = { spread_bps: number; ratio: number }

function totalRatio(tiers: TierItem[]) {
  return tiers.reduce((s, t) => s + t.ratio, 0) || 1
}

export function TierBar({ tiers, color, label }: { tiers: TierItem[]; color: string; label: string }) {
  if (!tiers || tiers.length <= 1) return null

  const total = totalRatio(tiers)
  const bgClass = color === 'green'
    ? 'from-emerald-600 to-emerald-800'
    : 'from-red-600 to-red-800'

  return (
    <div className="flex items-center gap-1.5">
      <span className="text-xs text-gray-500 w-5">{label}</span>
      <div className="flex h-4 flex-1 rounded-sm overflow-hidden">
        {tiers.map((t, i) => {
          const w = Math.max((t.ratio / total) * 100, 3)
          return (
            <div
              key={i}
              className={`flex items-center justify-center bg-gradient-to-r ${bgClass} text-xs text-white/90`}
              style={{ width: `${w}%`, minWidth: 0, opacity: 1 - i * 0.12 }}
              title={`${(t.spread_bps / 100).toFixed(2)}% · ${((t.ratio / total) * 100).toFixed(0)}%`}
            >
              {(t.spread_bps / 100).toFixed(2)}%
            </div>
          )
        })}
      </div>
    </div>
  )
}
