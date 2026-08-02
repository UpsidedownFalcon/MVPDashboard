// Accessible pill tabs (UIUX §4): proper tablist roles + arrow-key movement.

import { useRef, type KeyboardEvent, type ReactNode } from 'react'

export interface TabDef {
  id: string
  label: string
  content: ReactNode
}

export default function Tabs({
  tabs,
  active,
  onChange,
  label,
}: {
  tabs: TabDef[]
  active: string
  onChange: (id: string) => void
  label: string
}) {
  const listRef = useRef<HTMLDivElement>(null)

  const onKey = (e: KeyboardEvent) => {
    const idx = tabs.findIndex((t) => t.id === active)
    let next = -1
    if (e.key === 'ArrowRight') next = (idx + 1) % tabs.length
    if (e.key === 'ArrowLeft') next = (idx - 1 + tabs.length) % tabs.length
    if (next >= 0) {
      e.preventDefault()
      onChange(tabs[next].id)
      const buttons = listRef.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]')
      buttons?.[next]?.focus()
    }
  }

  const current = tabs.find((t) => t.id === active) ?? tabs[0]

  return (
    <div className="tabs-wrap">
      <div className="tabs" role="tablist" aria-label={label} ref={listRef} onKeyDown={onKey}>
        {tabs.map((t) => (
          <button
            key={t.id}
            role="tab"
            id={`tab-${t.id}`}
            aria-selected={t.id === current.id}
            aria-controls={`panel-${t.id}`}
            tabIndex={t.id === current.id ? 0 : -1}
            className={`tab ${t.id === current.id ? 'is-active' : ''}`}
            onClick={() => onChange(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div
        role="tabpanel"
        id={`panel-${current.id}`}
        aria-labelledby={`tab-${current.id}`}
        className="tab-panel"
      >
        {current.content}
      </div>
    </div>
  )
}
