// The pieces every Security tab draws its lists with, so a commit, a site, a
// package and an alert read the same way: a section (title, count, the bulk
// verdicts), a group (one surface card holding rows), and an item (who asked
// and where, what is asked, what it changes, the decision on the right).
// Styles: pages/security.css (the `sq-` classes).
//
// Every string handed in here may be the agent's: these only place text nodes.
import './pages/security.css'

export function Section({ title, count, actions, lede, className = '', children }) {
  return (
    <section className={`sq-sec${className ? ` ${className}` : ''}`}>
      <header className="sq-sec-head">
        <h3>{title}</h3>
        {count != null && <span className="sq-count">{count}</span>}
        {actions && <div className="sq-sec-actions">{actions}</div>}
      </header>
      {lede && <p className="sq-lede">{lede}</p>}
      {children}
    </section>
  )
}

// One surface card; its items are separated by hairlines
export function Group({ className = '', children, ...rest }) {
  return <div className={`sq-group${className ? ` ${className}` : ''}`} {...rest}>{children}</div>
}

// A state said in words, with a dot in its colour: ok | warn | bad | quiet.
// `why` follows in ink: "Push failed: remote rejected (non-fast-forward)".
export function State({ tone = 'quiet', title, why, children }) {
  return (
    <span className={`sq-state ${tone}`} title={title}>
      <i className="sq-dot" aria-hidden="true" />
      <span><b>{children}</b>{why ? <span className="sq-why">: {why}</span> : null}</span>
    </span>
  )
}

// One thing to decide.
//   meta     the sentence above: what kind, for which project, from where, when
//   title    what is asked (mono when it is data: a host, a package)
//   change   what approving it changes, in a sentence
//   actions  the decision buttons: right of the text on a wide screen, under it
//            on a narrow one; `below` keeps them under the text everywhere (a
//            card with a form above its buttons)
export function Item({
  meta, title, mono = false, titleAttr, state, change, actions, below = false, tone,
  className = '', children,
}) {
  const cls = ['sq-item', below ? 'below' : '', tone ? `is-${tone}` : '', className]
    .filter(Boolean).join(' ')
  return (
    <div className={cls}>
      <div className="sq-item-main">
        {meta && <div className="sq-meta">{meta}</div>}
        {title != null && (
          <div className={`sq-title${mono ? ' mono' : ''}`} title={titleAttr}>{title}</div>)}
        {state && <div className="sq-states">{state}</div>}
        {change && <p className="sq-change">{change}</p>}
        {children}
      </div>
      {actions && <div className="sq-actions">{actions}</div>}
    </div>
  )
}

// Server sentences mark names with backticks ("installs into `main`"): those
// parts in mono, the rest as words. Text nodes only.
export function Ticks({ text }) {
  const parts = String(text ?? '').split('`')
  if (parts.length < 3) return text ?? null
  return parts.map((t, i) => (i % 2 ? <span key={i} className="mono">{t}</span> : t))
}

// A label: value list for the details of a request (command, files, integrity)
export function Facts({ rows }) {
  const shown = rows.filter((r) => r && r[1] != null && r[1] !== false && r[1] !== '')
  if (!shown.length) return null
  return (
    <dl className="sq-facts">
      {shown.map(([label, value, mono]) => (
        <div key={label} className="sq-fact">
          <dt>{label}</dt>
          <dd className={mono ? 'mono' : undefined}>{value}</dd>
        </div>
      ))}
    </dl>
  )
}
