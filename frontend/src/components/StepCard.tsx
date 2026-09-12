import type { ReactNode } from 'react'

interface StepCardProps {
  number: number
  title: string
  meta?: string
  last?: boolean
  disabled?: boolean
  children: ReactNode
}

export default function StepCard({ number, title, meta, last, disabled, children }: StepCardProps) {
  return (
    <div className="flex gap-4">
      <div className="flex flex-col items-center">
        <div
          className={`w-8 h-8 rounded-full border flex items-center justify-center font-mono text-sm shrink-0 ${
            disabled ? 'border-line text-muted' : 'border-signal text-signal'
          }`}
        >
          {number}
        </div>
        {!last && <div className="w-px flex-1 bg-line mt-1" />}
      </div>
      <div className={`flex-1 pb-8 ${disabled ? 'opacity-40 pointer-events-none' : ''}`}>
        <div className="flex items-baseline justify-between gap-3 mb-2">
          <h3 className="font-medium tracking-tight">{title}</h3>
          {meta && <span className="font-mono text-xs text-muted whitespace-nowrap">{meta}</span>}
        </div>
        {children}
      </div>
    </div>
  )
}
