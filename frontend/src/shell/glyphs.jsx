// Line glyphs for the chat's chrome, drawn like the nav's (24-unit box, round
// caps, currentColor). They replace the typographic stand-ins (＋ ✕ « » ◼ ↑ ▾
// ⋯) that rendered in a different face on every platform.

const PATHS = {
  plus: <path d="M12 5v14M5 12h14" />,
  close: <path d="M6 6l12 12M18 6 6 18" />,
  left: <path d="m14.5 6-6 6 6 6" />,
  right: <path d="m9.5 6 6 6-6 6" />,
  down: <path d="m6 9.5 6 6 6-6" />,
  up: <path d="m6 14.5 6-6 6 6" />,
  menu: <path d="M4 7h16M4 12h16M4 17h16" />,
  more: <><circle cx="6" cy="12" r="1.4" fill="currentColor" stroke="none" />
          <circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none" />
          <circle cx="18" cy="12" r="1.4" fill="currentColor" stroke="none" /></>,
  check: <path d="m5 12.5 4.5 4.5L19 7" />,
  cross: <path d="M7 7l10 10M17 7 7 17" />,
  dash: <path d="M6 12h12" />,
  stop: <rect x="6.5" y="6.5" width="11" height="11" rx="2" fill="currentColor" stroke="none" />,
  send: <path d="M12 19V5M6 11l6-6 6 6" />,
  splitRight: <><rect x="3.5" y="4.5" width="17" height="15" rx="2.5" /><path d="M12 4.5v15" /></>,
  splitDown: <><rect x="3.5" y="4.5" width="17" height="15" rx="2.5" /><path d="M3.5 12h17" /></>,
  minimize: <path d="M6 16h12" />,
  maximize: <rect x="4.5" y="4.5" width="15" height="15" rx="2.5" />,
  restore: <><rect x="4.5" y="8" width="11.5" height="11.5" rx="2" /><path d="M8.5 8V6.5a2 2 0 0 1 2-2h7a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H16" /></>,
}

export function UiGlyph({ name, size = 16, className = '' }) {
  return (
    <svg className={`ui-glyph${className ? ` ${className}` : ''}`} width={size} height={size}
         viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {PATHS[name] || PATHS.more}
    </svg>
  )
}

export function SendGlyph({ stop = false }) {
  return <UiGlyph name={stop ? 'stop' : 'send'} size={18} />
}
