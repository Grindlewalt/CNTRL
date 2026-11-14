import {
  Children, Fragment, isValidElement, useEffect, useId, useLayoutEffect, useMemo, useRef, useState,
} from 'react'
import { createPortal } from 'react-dom'
import { useDismiss } from '../useDismiss.js'
import { useMediaQuery } from '../breakpoints.js'
import { keepInside, placePopover } from './place.js'
import {
  arrange, buildOptions, displayFor, firstEnabled, pickByNumber, readRecents, SEARCH_AFTER,
  step, textOf, typeahead, writeRecent,
} from './selectLogic.js'

// The app's dropdown: a field-shaped trigger that opens a glass listbox.
//
// Props are the ones the native-<select> version took, so every call site
// works unchanged:
//   label, hint     the field scaffolding (label above, hint below)
//   options         strings, or {value, label, sub?, disabled?, group?}
//   children        <option> / <optgroup> elements, listed after `options`
//   placeholder     shown while the value is '' (the disabled-empty-option
//                   idiom); never listed as a choice
//   value / defaultValue, onChange(e)   e.target.value is the option's value
//                   as a string, exactly as a native select reports it
//   disabled, className (on the control), name (a hidden input), id and any
//                   aria-*/title/data-* (on the trigger)
// New:
//   recentKey       which list's recents to use; defaults to the label (or
//                   aria-label). false turns recents off.
//
// Behaviour (the pure parts are selectLogic.js, tested in node):
//  - more than 6 options: a search field, focused on open; typing filters
//  - the 9 most recent picks of a long list come first, numbered; 1 to 9 pick
//    them while the search is empty. A short list keeps its order and 1 to 6
//    pick by position.
//  - ArrowUp/Down, PageUp/Down, Home/End, Enter, Escape (one layer, via
//    useDismiss), Tab closes; typeahead in a list with no search field
//  - role=combobox trigger, role=listbox popup, aria-activedescendant
//  - on a phone or a touch screen it opens as a bottom sheet with the same
//    search (not focused, so the keyboard does not cover the list)
const SHEET_QUERY = '(max-width: 768px), (pointer: coarse)'

function store() {
  try { return window.localStorage } catch { return null }
}

// <option>/<optgroup> children as option objects, in order
function fromChildren(children, group = null, out = []) {
  Children.forEach(children, (c) => {
    if (!isValidElement(c)) return
    if (c.type === Fragment) fromChildren(c.props.children, group, out)
    else if (c.type === 'optgroup') fromChildren(c.props.children, c.props.label ?? null, out)
    else if (c.type === 'option') {
      const text = textOf(c.props.children)
      out.push({
        value: c.props.value ?? text,
        label: c.props.label ?? (c.props.children ?? text),
        disabled: !!c.props.disabled,
        group,
      })
    }
  })
  return out
}

function Chevron() {
  return (
    <svg className="sel-chev" width="14" height="14" viewBox="0 0 24 24" fill="none"
         stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
         aria-hidden="true"><path d="m6 9.5 6 6 6-6" /></svg>
  )
}

function SearchIcon() {
  return (
    <svg className="sel-search-ico" width="14" height="14" viewBox="0 0 24 24" fill="none"
         stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
      <circle cx="11" cy="11" r="6.5" /><path d="m16 16 4 4" /></svg>
  )
}

function Check() {
  return (
    <svg className="sel-check" width="14" height="14" viewBox="0 0 24 24" fill="none"
         stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"
         aria-hidden="true"><path d="m5 12.5 4.5 4.5L19 7" /></svg>
  )
}

export default function Select({
  label, hint, options = [], placeholder, children, value, defaultValue, onChange,
  disabled = false, className = '', recentKey, name, id, searchPlaceholder = 'Search',
  'aria-label': ariaLabel, ...rest
}) {
  const uid = useId()
  const labelId = `${uid}l`
  const hintId = `${uid}h`
  const listId = `${uid}x`
  const triggerId = id || `${uid}t`
  const optId = (i) => `${uid}o${i}`

  const controlled = value !== undefined
  const [inner, setInner] = useState(defaultValue ?? '')
  const current = controlled ? value : inner
  const { list, placeholder: ph } = useMemo(
    () => buildOptions(options, fromChildren(children), placeholder),
    [options, children, placeholder])
  const searchable = list.length > SEARCH_AFTER
  const title = (typeof label === 'string' && label) || ariaLabel || ph || 'Choose'
  const key = recentKey === false ? null
    : (recentKey || (typeof label === 'string' && label) || ariaLabel || null)

  const sheet = useMediaQuery(SHEET_QUERY)
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [recents, setRecents] = useState([])
  const [active, setActive] = useState(-1)
  const { sections, items } = useMemo(
    () => arrange(list, recents, searchable ? query : ''), [list, recents, query, searchable])

  const triggerRef = useRef(null)
  const popRef = useRef(null)
  const listRef = useRef(null)
  const searchRef = useRef(null)
  const sheetRef = useRef(null)
  const typed = useRef({ text: '', at: 0 })
  const wasOpen = useRef(false)
  const close = () => { setOpen(false); setQuery('') }
  const wrapRef = useDismiss(open, close, popRef)

  const shown = displayFor(list, current, ph)
  const longest = useMemo(() => {
    let best = ph || ''
    for (const o of list) { const t = textOf(o.label); if (t.length > best.length) best = t }
    return best
  }, [list, ph])

  function openList(seed = '', ahead = '') {
    if (disabled) return
    const rec = readRecents(store(), key)
    const next = arrange(list, rec, searchable ? seed : '').items
    let idx = next.findIndex((it) => it.value === String(current ?? '') && !it.disabled)
    if (seed) idx = firstEnabled(next)
    if (ahead) { const t = typeahead(next, ahead, idx); if (t >= 0) idx = t }
    if (idx < 0) idx = firstEnabled(next)
    setRecents(rec)
    setQuery(searchable ? seed : '')
    setActive(idx)
    setOpen(true)
  }

  function choose(it) {
    if (!it || it.disabled) return
    if (searchable) writeRecent(store(), key, it.value)
    close()
    triggerRef.current?.focus()
    if (it.value === String(current ?? '')) return
    if (!controlled) setInner(it.value)
    const target = { value: it.value, name, id: triggerId, type: 'select-one' }
    onChange?.({
      target, currentTarget: target, type: 'change', nativeEvent: null,
      preventDefault() {}, stopPropagation() {}, persist() {},
      isDefaultPrevented: () => false, isPropagationStopped: () => false,
    })
  }

  // Focus goes back to the trigger when the list closes with focus inside it
  // (Escape, a pick); a click elsewhere leaves focus where the click put it.
  useEffect(() => {
    if (open) { wasOpen.current = true; return }
    if (!wasOpen.current) return
    wasOpen.current = false
    const ae = document.activeElement
    if (!ae || ae === document.body) triggerRef.current?.focus()
  }, [open])

  // place the popover before paint; follow scrolls and resizes, but not the
  // list's own scrolling (re-measuring would snap it back to the top)
  useLayoutEffect(() => {
    if (!open || sheet) return undefined
    const run = (e) => {
      if (e?.target && popRef.current?.contains(e.target)) return
      const t = triggerRef.current
      const p = popRef.current
      if (!t || !p) return
      p.style.setProperty('--sel-w', `${t.offsetWidth}px`)
      placePopover(p, t.getBoundingClientRect(), { align: 'left', offset: 6 })
    }
    run()
    window.addEventListener('resize', run)
    window.addEventListener('scroll', run, true)
    return () => {
      window.removeEventListener('resize', run)
      window.removeEventListener('scroll', run, true)
    }
  }, [open, sheet, items.length])

  // the sheet keeps the height it opened at, so filtering doesn't make it jump
  useLayoutEffect(() => {
    const el = sheetRef.current
    if (open && sheet && el) el.style.minHeight = `${el.offsetHeight}px`
  }, [open, sheet])

  // on open: the search field on a desktop, the list itself on a phone
  useEffect(() => {
    if (!open) return
    const el = searchable && !sheet ? searchRef.current : listRef.current
    el?.focus({ preventScroll: true })
  }, [open, searchable, sheet])

  useEffect(() => {
    if (!open || active < 0) return
    document.getElementById(`${uid}o${active}`)?.scrollIntoView?.({ block: 'nearest' })
  }, [open, active, uid])

  function onListKey(e) {
    if (e.nativeEvent?.isComposing) return
    const k = e.key
    const plain = !e.ctrlKey && !e.metaKey && !e.altKey
    const move = (i) => { if (i >= 0) setActive(i) }
    if (k === 'ArrowDown' || k === 'ArrowUp') {
      e.preventDefault(); move(step(items, active, k === 'ArrowDown' ? 1 : -1)); return
    }
    if (k === 'PageDown' || k === 'PageUp') {
      e.preventDefault(); move(step(items, active, k === 'PageDown' ? 8 : -8)); return
    }
    if ((k === 'Home' || k === 'End') && !query) {
      e.preventDefault(); move(firstEnabled(items, k === 'End')); return
    }
    if (k === 'Enter') { e.preventDefault(); e.stopPropagation(); choose(items[active]); return }
    if (k === 'Tab') { e.preventDefault(); close(); triggerRef.current?.focus(); return }
    if (/^[1-9]$/.test(k) && plain && !query) {
      const it = pickByNumber(items, k)
      if (it) { e.preventDefault(); choose(it); return }
    }
    if (searchable && e.currentTarget === searchRef.current) return   // the field types
    if (searchable && k.length === 1 && k !== ' ' && plain) {
      // the sheet focuses the list, not the field: typing still searches
      e.preventDefault()
      const q = query + k
      setQuery(q)
      setActive(firstEnabled(arrange(list, recents, q).items))
      searchRef.current?.focus({ preventScroll: true })
      return
    }
    if (k === ' ' && !typed.current.text) { e.preventDefault(); choose(items[active]); return }
    if (k.length === 1 && plain) {
      e.preventDefault()
      const now = Date.now()
      const text = now - typed.current.at < 700 ? typed.current.text + k : k
      typed.current = { text, at: now }
      // repeating one letter cycles through the options that start with it
      const same = text.length > 1 && [...text].every((c) => c === text[0])
      const i = typeahead(items, same ? text[0] : text, same || text.length === 1 ? active : active - 1)
      move(i)
    }
  }

  function onTriggerKey(e) {
    if (disabled || open) return
    const k = e.key
    if (k === 'ArrowDown' || k === 'ArrowUp') {
      e.preventDefault(); openList(); return
    }
    if (k.length === 1 && k !== ' ' && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault()
      if (searchable) openList(k)
      else openList('', k)
    }
  }

  const onSearch = (e) => {
    const q = e.target.value
    setQuery(q)
    setActive(firstEnabled(arrange(list, recents, q).items))
  }

  const activeId = open && active >= 0 && items[active] ? optId(active) : undefined
  const selectedValue = String(current ?? '')
  let flat = -1
  const listbox = (
    <div ref={listRef} className="sel-list" role="listbox" id={listId} tabIndex={-1}
         aria-labelledby={label ? labelId : undefined}
         aria-label={label ? undefined : title}
         aria-activedescendant={activeId} onKeyDown={onListKey}>
      {sections.map((s, si) => {
        const opts = s.items.map((it) => {
          flat += 1
          const i = flat
          const selected = !!shown.option && it.value === shown.option.value
          return (
            <div key={`${s.label || ''}:${it.value}`} id={optId(i)} role="option"
                 aria-selected={selected} aria-disabled={it.disabled || undefined}
                 className={['sel-opt', i === active ? 'active' : '', selected ? 'selected' : '']
                   .filter(Boolean).join(' ')}
                 onMouseMove={() => { if (!it.disabled && i !== active) setActive(i) }}
                 onMouseDown={(e) => e.preventDefault()}
                 onClick={() => choose(it)}>
              {it.num != null && <kbd className="sel-num" aria-hidden="true">{it.num}</kbd>}
              <span className="sel-opt-text">
                <span className="sel-opt-label">{it.label}</span>
                {it.sub != null && <span className="sel-opt-sub">{it.sub}</span>}
                {query && it.group && <span className="sel-opt-sub">{it.group}</span>}
              </span>
              {selected && <Check />}
            </div>
          )
        })
        if (!s.label) return <Fragment key={`s${si}`}>{opts}</Fragment>
        return (
          <div key={`s${si}`} role="group" aria-labelledby={`${uid}g${si}`} className="sel-groupbox">
            <div className="sel-group" id={`${uid}g${si}`}>{s.label}</div>
            {opts}
          </div>
        )
      })}
      {items.length === 0 && (
        <div className="sel-empty">{query ? `Nothing matches "${query}".` : 'Nothing to choose.'}</div>
      )}
    </div>
  )
  const search = searchable && (
    <div className="sel-search">
      <SearchIcon />
      <input ref={searchRef} type="text" value={query} onChange={onSearch} onKeyDown={onListKey}
             role="combobox" aria-expanded="true" aria-controls={listId} aria-autocomplete="list"
             aria-activedescendant={activeId} aria-label={`Search ${title}`}
             placeholder={searchPlaceholder} autoComplete="off" spellCheck={false}
             autoCapitalize="off" enterKeyHint="done" />
    </div>
  )
  let layer = null
  if (open && sheet) {
    layer = (
      <div ref={popRef} className="sel-layer" onPointerDown={keepInside}>
        <div className="sel-scrim" onClick={close} />
        <div ref={sheetRef} className="sel-sheet" role="dialog" aria-modal="true" aria-label={title}>
          <div className="sel-sheet-head">
            <span className="sel-sheet-title">{title}</span>
            <button type="button" className="icon-btn sel-sheet-close" aria-label="Close"
                    onClick={close}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                   strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                <path d="M6 6l12 12M18 6 6 18" /></svg>
            </button>
          </div>
          {search}
          {listbox}
        </div>
      </div>
    )
  } else if (open) {
    layer = (
      <div ref={popRef} className="sel-pop" onPointerDown={keepInside}>
        {search}
        {listbox}
      </div>
    )
  }

  const text = shown.option ? shown.option.label : (shown.placeholder ? ph : '')
  const control = (
    <div ref={wrapRef}
         className={['sel', open ? 'open' : '', className].filter(Boolean).join(' ')}>
      <button {...rest} ref={triggerRef} type="button" id={triggerId} className="sel-trigger"
              role="combobox" aria-haspopup="listbox" aria-expanded={open}
              aria-controls={open ? listId : undefined}
              aria-labelledby={label ? labelId : undefined}
              aria-label={label ? undefined : ariaLabel}
              aria-describedby={hint ? hintId : undefined}
              disabled={disabled}
              onClick={() => (open ? close() : openList())}
              onKeyDown={onTriggerKey}>
        <span className="sel-value">
          <span className={shown.option ? 'sel-text' : 'sel-text sel-ph'}>{text || ' '}</span>
          <span className="sel-sizer" aria-hidden="true">{longest || ' '}</span>
        </span>
        <Chevron />
      </button>
      {name && <input type="hidden" name={name} value={selectedValue} />}
      {layer && createPortal(layer, document.body)}
    </div>
  )
  if (!label && !hint) return control
  return (
    <div className="field">
      {label && (
        <span id={labelId} onClick={() => !disabled && triggerRef.current?.focus()}>{label}</span>
      )}
      {control}
      {hint && <span className="field-hint" id={hintId}>{hint}</span>}
    </div>
  )
}
