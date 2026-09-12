const LABELS: Record<string, string> = {
  draft: 'Borrador',
  analyzing: 'Analizando',
  scripting: 'Escribiendo guion',
  storyboarding: 'Generando plan visual',
  provisioning: 'Abasteciendo',
  rendering: 'Montando',
  completed: 'Completado',
  failed: 'Error',
}

const DOT: Record<string, string> = {
  draft: 'bg-muted',
  analyzing: 'bg-cine',
  scripting: 'bg-cine',
  storyboarding: 'bg-cine',
  provisioning: 'bg-amber',
  rendering: 'bg-amber',
  completed: 'bg-signal',
  failed: 'bg-error',
}

export default function StatusBadge({ status }: { status: string }) {
  const busy = ['analyzing', 'scripting', 'storyboarding', 'provisioning', 'rendering'].includes(status)
  return (
    <span className="inline-flex items-center gap-1.5 font-mono text-[11px] text-muted shrink-0 whitespace-nowrap">
      <span className={`w-1.5 h-1.5 rounded-full ${DOT[status] ?? 'bg-muted'} ${busy ? 'animate-pulse' : ''}`} />
      {LABELS[status] ?? status}
    </span>
  )
}
