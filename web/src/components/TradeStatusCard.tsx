import { useState } from 'react'
import { tradeToggle } from '../api'

interface Props {
  isTradingEnabled: boolean
  nodeId?: string
}

export function TradeStatusCard({ isTradingEnabled, nodeId }: Props) {
  const [enabled, setEnabled] = useState(isTradingEnabled)

  const handleToggle = async () => {
    try {
      const res = await tradeToggle()
      setEnabled(res.is_trading_enabled)
    } catch {
      // ignore
    }
  }

  return (
    <div className="mb-4 rounded-lg border border-gray-800 bg-gray-900/50 px-4 py-3">
      <div className="flex items-center gap-3">
        <span className={`h-2.5 w-2.5 rounded-full ${enabled ? 'bg-emerald-400' : 'bg-red-400'}`} />
        <span className="text-sm font-medium text-gray-200">
          {enabled ? '交易运行中' : '交易已暂停'}
        </span>
        <button
          onClick={handleToggle}
          className={`ml-auto rounded px-3 py-1.5 text-xs font-medium transition ${
            enabled
              ? 'bg-red-500/10 text-red-400 hover:bg-red-500/20'
              : 'bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20'
          }`}
        >
          {enabled ? '暂停自动交易' : '启用自动交易'}
        </button>
        {nodeId && (
          <span className="text-xs text-gray-500">节点: {nodeId}</span>
        )}
      </div>
    </div>
  )
}
