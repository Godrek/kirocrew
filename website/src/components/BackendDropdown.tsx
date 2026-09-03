import { Check } from 'lucide-react'

import { acpBackendLabel } from '../providers/acpBackends'
import { i18nT } from '../i18n/t'

/**
 * The composer's harness picker: which ACP backend THIS chat runs on.
 *
 * Deliberately the plainest possible sibling of `ModelEffortDropdown` — three
 * rows, no filter input, no drill-in. The list is short and closed (the
 * dashboard-selectable set), so a filter would be chrome with nothing to do.
 *
 * The rows are labelled through `acpBackendLabel`, the same resolver Settings ▸
 * Chat renders its selector with, so the two surfaces cannot name the same
 * harness differently. The footer states the SCOPE, because that is the one
 * thing this control changes about the settings selector's meaning: the pick is
 * this chat's, and the configured default is what a NEW chat starts on.
 */
export default function BackendDropdown({
  anchorRect, dropdownRef, backends, activeBackend, onSelect,
}: {
  anchorRect: DOMRect
  dropdownRef: React.Ref<HTMLDivElement>
  /** The harnesses to offer, in the order the server offers them. */
  backends: readonly string[]
  /** The harness this slot is on — its own binding, else the configured
   *  default. Compared by equality, so `''` (kiro) marks its row like any
   *  other id rather than reading as "nothing selected". */
  activeBackend: string
  onSelect: (backend: string) => void
}) {
  const width = 260
  // Right-aligned to the button's right edge and clamped to the viewport, the
  // same placement rule the model dropdown beside it uses.
  const left = Math.max(8, Math.min(anchorRect.right - width, window.innerWidth - width - 8))
  return (
    <div
      ref={dropdownRef}
      role="dialog"
      aria-label={i18nT('components.backendDropdown.backend_selector')}
      tabIndex={-1}
      className="fixed z-[9999] bg-bg-elevated border border-border rounded-xl shadow-xl overflow-hidden animate-slide-up flex flex-col p-1"
      style={{ width, bottom: window.innerHeight - anchorRect.top + 4, left }}
    >
      <div role="listbox" aria-label={i18nT('components.backendDropdown.backend_list')} className="flex flex-col gap-0.5">
        {backends.map(backend => {
          const active = backend === activeBackend
          return (
            <button
              // Prefixed rather than `backend || 'kiro'`: kiro's id is the empty
              // string, and a truthiness fallback here is the same shape that
              // elsewhere silently means "unset".
              key={`backend:${backend}`}
              type="button"
              role="option"
              aria-selected={active}
              tabIndex={-1}
              className={`w-full text-left px-2.5 py-2 flex items-center gap-2 rounded-md cursor-pointer transition-all border-none bg-transparent ${active ? 'bg-accent-subtle' : 'hover:bg-bg-hover'}`}
              onClick={() => onSelect(backend)}
            >
              <span className={`text-[13px] font-semibold truncate ${active ? 'text-accent' : 'text-text'}`}>
                {acpBackendLabel(backend)}
              </span>
              {active && <span className="text-accent ml-auto shrink-0"><Check className="lucide-inline" /></span>}
            </button>
          )
        })}
      </div>
      <div className="px-2.5 py-2 mt-0.5 text-[12px] text-muted leading-tight border-t border-border">
        {i18nT('components.backendDropdown.applies_to_this_chat_only')}
      </div>
    </div>
  )
}
