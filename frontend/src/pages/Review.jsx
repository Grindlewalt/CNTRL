import { useContext } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import TriagePanel from '../TriagePanel.jsx'
import Posture from '../Posture.jsx'
import ScrollHint from '../ScrollHint.jsx'
import { PendingCountContext } from '../Notices.jsx'
import { ledeFor, SECURITY_LEDE_LINES, SECURITY_LEDES } from '../securityCopy.js'
import HelpTip from '../components/HelpTip.jsx'
import { BoxesGlance, LivePages, QueueList } from '../SecurityDash.jsx'
import Page from '../components/Page.jsx'
import Tabs from '../components/Tabs.jsx'

// The queue of everything awaiting the operator: git requests, sites a box
// asked for, packages, services, environments, skills, held journal lines,
// pages to publish, questions in chats, and the alerts that need them. The
// rows, the detail panel and the decisions live in SecurityDash.jsx. Rendered
// whole on /security and, with a `slug`, filtered to one project inside a Work
// window.
//
// EVERY string in a request is UNTRUSTED (the agent's, the guest's, scanned
// data): all of it is rendered as plain text nodes; nothing goes through <Md>.
export function ReviewQueue({ slug }) {
  return <QueueList slug={slug} />
}

// ---- the shell ----
// Security (the page once called Review) is a layout route (routes.jsx): this
// is the <h1> and the tab strip, and the tabs are real URLs — /security,
// /security/network, /security/logs, /security/secrets — so each is linkable
// and NavLink lights the current one. The old /review* addresses redirect.
//
// THE CONTRACT for a tab (an <Outlet> child): it renders inside .review-body,
// a full-height flex column that owns the insets and scrolls if the child
// does not. A tab that wants its own inner scrolling (Network's feed, Logs'
// transcript) is `flex: 1; min-height: 0` and never overflows the body; a
// tab that is a plain document (the queue, Secrets) just flows and the body
// scrolls. No tab paints a heading of its own.
export default function Review() {
  const count = useContext(PendingCountContext)
  const { pathname } = useLocation()
  return (
    <Page variant="fill" title="Security" className="review-shell"
          actions={(
            <ScrollHint><Tabs label="Security sections" items={[
              { to: '/security', end: true, label: 'Queue', count },
              { to: '/security/persistent', label: 'Persistent' },
              { to: '/security/network', label: 'Network' },
              { to: '/security/profiles', label: 'Profiles' },
              { to: '/security/logs', label: 'Logs' },
              { to: '/security/secrets', label: 'Secrets' },
            ]} /></ScrollHint>
          )}>
      <div className="review-body">
        {/* one short line on what this tab is, the rest behind a ? */}
        {ledeFor(SECURITY_LEDE_LINES, pathname) && (
          <p className="tab-lede dim">{ledeFor(SECURITY_LEDE_LINES, pathname)}{' '}
            <HelpTip label="About this tab">{ledeFor(SECURITY_LEDES, pathname)}</HelpTip></p>
        )}
        <Outlet />
      </div>
    </Page>
  )
}

// The index tab, a dashboard: what waits for you in the main column; beside it
// (under it on a narrow screen) the boxes running now and what works in them,
// live pages, how things are set, and Auto review. The history is in Logs.
export function ReviewHome() {
  return (
    <div className="review-page sq-home">
      <div className="sq-main">
        <QueueList />
      </div>
      <aside className="sq-rail" aria-label="At a glance">
        <BoxesGlance />
        <LivePages />
        <Posture />
        <TriagePanel />
      </aside>
    </div>
  )
}
