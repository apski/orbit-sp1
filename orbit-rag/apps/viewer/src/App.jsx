import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { pointToSource, sourceOffsets } from './sourceOffsets.js'

const CATEGORIES = ['decision', 'constraint', 'insight', 'note', 'question']

const api = async (url, body) => {
  const r = await fetch(url, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {})
  const j = await r.json()
  if (!r.ok) throw new Error(j.error || r.statusText)
  return j
}

const pathFromUrl = () => new URLSearchParams(location.search).get('path') || ''

export default function App() {
  const [files, setFiles] = useState([])
  const [path, setPath] = useState(pathFromUrl)
  const [doc, setDoc] = useState(null)
  const [error, setError] = useState('')
  const [menu, setMenu] = useState(null)
  const [review, setReview] = useState([])
  const [tab, setTab] = useState('highlights')
  const [locate, setLocate] = useState(null) // { path, start, end } of the clicked search hit
  const lastDoc = useRef('')

  const loadDoc = useCallback(async (p) => {
    if (!p) return
    try {
      const d = await api(`/api/doc?path=${encodeURIComponent(p)}`)
      const key = JSON.stringify([d.revision_id, d.highlights])
      if (key !== lastDoc.current) {
        lastDoc.current = key
        setDoc(d)
        setReview(await api('/api/review'))
      }
      setError('')
    } catch (e) {
      setError(e.message)
    }
  }, [])

  useEffect(() => {
    api('/api/files')
      .then((f) => setFiles([...new Set([...f.files, ...f.sources.map((s) => s.path)])].sort()))
      .catch((e) => setError(e.message))
  }, [])
  useEffect(() => {
    lastDoc.current = ''
    loadDoc(path)
    // source-change watcher: every fetch syncs + re-anchors server-side
    const t = setInterval(() => document.visibilityState === 'visible' && loadDoc(path), 2000)
    return () => clearInterval(t)
  }, [path, loadDoc])
  useEffect(() => {
    const onPop = () => setPath(pathFromUrl())
    addEventListener('popstate', onPop)
    return () => removeEventListener('popstate', onPop)
  }, [])

  const open = (p, ctx, range) => {
    history.pushState(null, '', `?path=${encodeURIComponent(p)}${ctx ? '#' + ctx : ''}`)
    setPath(p)
    setLocate(range ? { path: p, start: range[0], end: range[1] } : null)
    if (ctx) setTimeout(() => flash(ctx), 300)
  }

  // underline the clicked passage and scroll to it; stays until the next click
  useEffect(() => {
    document.querySelectorAll('.locate').forEach((el) => el.classList.remove('locate'))
    if (!doc || !locate || locate.path !== doc.path) return
    const spans = [...document.querySelectorAll('article [data-o]')].filter((el) => {
      const o = +el.dataset.o
      return o < locate.end && o + el.textContent.length > locate.start
    })
    spans.forEach((el) => el.classList.add('locate'))
    spans[0]?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }, [doc, locate])

  useEffect(() => { if (doc && location.hash) flash(location.hash.slice(1)) }, [doc?.revision_id])

  const selectionRange = () => {
    const sel = getSelection()
    if (!sel || sel.isCollapsed || !doc) return null
    const r = sel.getRangeAt(0)
    let s = pointToSource(r.startContainer, r.startOffset, false)
    let e = pointToSource(r.endContainer, r.endOffset, true)
    if (s == null || e == null) return null
    const src = doc.content
    while (s < e && /\s/.test(src[s])) s++
    while (e > s && /\s/.test(src[e - 1])) e--
    return e > s ? { start: s, end: e, text: src.slice(s, e) } : null
  }

  const openMenu = (x, y) => {
    const range = selectionRange()
    if (!range) return false
    setMenu({ ...range, x, y, note: '', scope: '' })
    return true
  }

  const onContextMenu = (ev) => { if (openMenu(ev.clientX, ev.clientY)) ev.preventDefault() }
  useEffect(() => {
    const onKey = (ev) => {
      if (ev.key === 'Escape') setMenu(null)
      if (ev.key === 'h' && !menu && !/input|textarea/i.test(ev.target.tagName)) {
        const r = getSelection()?.rangeCount && getSelection().getRangeAt(0).getBoundingClientRect()
        if (r && openMenu(r.left, r.bottom + 6)) ev.preventDefault()
      }
    }
    addEventListener('keydown', onKey)
    return () => removeEventListener('keydown', onKey)
  })

  const save = async (category) => {
    try {
      await api('/api/highlights', { source_id: doc.source_id, revision_id: doc.revision_id, start: menu.start, end: menu.end, category, note: menu.note, scope: menu.scope })
      setMenu(null)
      getSelection()?.removeAllRanges()
      lastDoc.current = ''
      await loadDoc(path)
    } catch (e) {
      setError(e.message)
    }
  }

  const act = async (kind, id) => {
    try {
      await api(`/api/review/${kind}`, { id })
      lastDoc.current = ''
      await loadDoc(path)
    } catch (e) {
      setError(e.message)
    }
  }

  const plugins = useMemo(() => (doc ? [sourceOffsets({ source: doc.content, highlights: doc.highlights })] : []), [doc])

  return (
    <div className="app">
      <aside className="nav">
        <div className="brand">orbit</div>
        <ul className="files">
          {files.map((f) => (
            <li key={f}><button className={f === path ? 'on' : ''} onClick={() => open(f)}>{f}</button></li>
          ))}
        </ul>
      </aside>

      <main onContextMenu={onContextMenu}>
        {error && <div className="error" role="alert">{error}</div>}
        {doc ? (
          <>
            <header className="doc-head">
              <span>{doc.path}</span>
              <span className="muted" data-testid="revision">rev {doc.revision_id}{doc.git_rev ? ` · git:${doc.git_rev}` : ''}</span>
            </header>
            <article className="md" data-testid="article">
              {doc.lang === 'markdown'
                ? <Markdown remarkPlugins={[remarkGfm]} rehypePlugins={plugins}>{doc.content}</Markdown>
                : <CodeView source={doc.content} highlights={doc.highlights} tags={tab === 'tags' ? doc.tags || [] : []} />}
            </article>
            <p className="hint">Select text, then right-click (or press <kbd>h</kbd>) to capture.</p>
          </>
        ) : (
          <p className="muted empty">Pick a Markdown file.</p>
        )}
      </main>

      <aside className="side">
        <nav className="tabs" role="tablist">
          {['highlights', 'review', 'search', 'bundle', ...(doc?.tags?.length ? ['tags'] : [])].map((t) => (
            <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? 'on' : ''} onClick={() => setTab(t)}>
              {t}{t === 'review' && review.length ? ` (${review.length})` : ''}
            </button>
          ))}
        </nav>
        {tab === 'highlights' && <Highlights doc={doc} act={act} />}
        {tab === 'review' && <ReviewQueue items={review} act={act} open={open} />}
        {tab === 'search' && <Search open={open} />}
        {tab === 'bundle' && <Bundle />}
        {tab === 'tags' && <Tags doc={doc} />}
      </aside>

      {menu && (
        <div className="menu" style={{ left: Math.min(menu.x, innerWidth - 300), top: Math.min(menu.y, innerHeight - 260) }} role="dialog" aria-label="Capture highlight">
          <blockquote>{menu.text.length > 140 ? menu.text.slice(0, 140) + '…' : menu.text}</blockquote>
          <textarea placeholder="Note (optional)" value={menu.note} onChange={(e) => setMenu({ ...menu, note: e.target.value })} />
          <input placeholder="Scope (default: heading path)" value={menu.scope} onChange={(e) => setMenu({ ...menu, scope: e.target.value })} />
          <div className="cats">
            {CATEGORIES.map((c) => (
              <button key={c} className={`cat cat-${c}`} data-testid={`capture-${c}`} onClick={() => save(c)}>{c}</button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// Code sources: the text itself, split at highlight boundaries, same data-o contract.
// Machine tags only show while the Tags tab is open, styled apart from human highlights.
function CodeView({ source, highlights, tags }) {
  const hl = highlights.filter((h) => h.start != null && h.end > h.start)
  const tg = tags.filter((t) => t.end > t.start)
  const pts = [...new Set([0, source.length, ...hl.flatMap((h) => [h.start, h.end]), ...tg.flatMap((t) => [t.start, t.end])])].sort((a, b) => a - b)
  return (
    <pre className="code"><code>
      {pts.slice(0, -1).map((a, i) => {
        const on = hl.filter((h) => h.start <= a && h.end >= pts[i + 1])
        const tagged = tg.filter((t) => t.start <= a && t.end >= pts[i + 1])
        const cls = [on.length && `hl cat-${on[0].category} v-${on[0].validity}`, tagged.length && `tag tag-${tagged[0].kind}`].filter(Boolean).join(' ')
        return (
          <span key={a} data-o={a} data-ctx={on.length ? on.map((h) => h.id).join(' ') : undefined}
            data-tag={tagged.length ? tagged.map((t) => t.id).join(' ') : undefined} className={cls || undefined}>
            {source.slice(a, pts[i + 1])}
          </span>
        )
      })}
    </code></pre>
  )
}

const TAG_KINDS = [['type', 'Types'], ['constant', 'Constants'], ['config', 'Config'], ['api', 'Public API'], ['boundary', 'Boundaries'], ['check', 'Guards'], ['ceiling', 'Known ceilings']]

function Tags({ doc }) {
  if (!doc?.tags) return null
  const go = (id) => {
    const el = document.querySelector(`[data-tag~="${CSS.escape(id)}"]`)
    el?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }
  return (
    <div className="pad" data-testid="tags">
      <p className="muted small">Machine-derived from the code; not curated, never in search ranking.</p>
      {TAG_KINDS.map(([k, title]) => {
        const items = doc.tags.filter((t) => t.kind === k)
        return items.length ? (
          <section key={k}>
            <h4 className="tag-head">{title} <span className="muted">{items.length}</span></h4>
            <ul className="tag-list">
              {items.map((t) => (
                <li key={t.id}><button className={`tag-item tag-${k}`} onClick={() => go(t.id)}>
                  <code>{k === 'constant' ? `${t.name} = ${t.value}` : k === 'type' ? t.name : t.value || t.name}</code>
                  {k === 'type' && t.value && <span className="muted small"> {'{'} {t.value} {'}'}</span>}
                </button></li>
              ))}
            </ul>
          </section>
        ) : null
      })}
    </div>
  )
}

function flash(id) {
  const el = document.querySelector(`[data-ctx~="${CSS.escape(id)}"]`)
  if (!el) return
  el.scrollIntoView({ block: 'center', behavior: 'smooth' })
  el.classList.add('flash')
  setTimeout(() => el.classList.remove('flash'), 1200)
}

function Chip({ v }) {
  return <span className={`chip v-${v}`}>{v.replace('_', ' ')}</span>
}

function Highlights({ doc, act }) {
  if (!doc) return null
  if (!doc.highlights.length) return <p className="muted pad">No highlights yet.</p>
  return (
    <ul className="cards" data-testid="highlights">
      {doc.highlights.map((h) => (
        <li key={h.id} data-testid={`hl-${h.id}`} data-validity={h.validity}>
          <button className="card" onClick={() => flash(h.id)}>
            <div className="row"><b>{h.id}</b><span className={`cat cat-${h.category}`}>{h.category}</span><Chip v={h.validity} /></div>
            <div className="body">{h.body}</div>
            {h.note && <div className="note">{h.note}</div>}
            <div className="muted small">{h.resolution} · {h.scope}</div>
          </button>
          {h.validity === 'needs_review' && <ReviewActions id={h.id} act={act} canAccept={h.start != null} />}
        </li>
      ))}
    </ul>
  )
}

function ReviewActions({ id, act, canAccept, label }) {
  return (
    <div className="actions">
      {canAccept && <button onClick={() => act('accept', id)} data-testid={`accept-${id}`}>{label ?? 'Accept current text'}</button>}
      <button onClick={() => act('retire', id)}>Retire</button>
    </div>
  )
}

function ReviewQueue({ items, act, open }) {
  if (!items.length) return <p className="muted pad">Review queue empty.</p>
  return (
    <ul className="cards">
      {items.map((i) => (
        <li key={i.id}>
          <button className="card" onClick={() => open(i.record.path, i.record.id)}>
            <div className="row"><b>{i.record.id}</b><span className={`cat cat-${i.record.category}`}>{i.record.category}</span><span className="chip">{i.reason.replace('_', ' ')}</span></div>
            {i.reason === 'proposed'
              ? <div className="body">{i.record.exact}</div>
              : <>
                  {i.approved && <div className="diff old">{i.approved[0]}</div>}
                  <div className="diff new">{i.record.start != null ? i.record.exact : '(no position found)'}</div>
                </>}
            <div className="muted small">{i.record.path}</div>
          </button>
          <ReviewActions id={i.record.id} act={act} canAccept={i.record.start != null} label={i.reason === 'proposed' ? 'Approve' : undefined} />
        </li>
      ))}
    </ul>
  )
}

function CodeToggle({ code, setCode }) {
  return <label className="toggle small"><input type="checkbox" checked={code} onChange={(e) => setCode(e.target.checked)} data-testid="include-code" /> Include code</label>
}

function Search({ open }) {
  const [q, setQ] = useState('')
  const [code, setCode] = useState(false)
  const [res, setRes] = useState(null)
  const run = async (e) => {
    e?.preventDefault()
    setRes(await api(`/api/search?q=${encodeURIComponent(q)}&code=${code ? 1 : 0}`))
  }
  return (
    <div className="pad">
      <form onSubmit={run}><input placeholder="Search context…" value={q} onChange={(e) => setQ(e.target.value)} data-testid="search" /></form>
      <CodeToggle code={code} setCode={setCode} />
      {res && (
        <>
          <p className="muted small">{res.retrievers.join(' + ')}{res.warnings.map((w) => ` · ${w}`)}</p>
          <ul className="cards">
            {res.hits.map((h) => (
              <li key={h.id}>
                <button className="card" onClick={() => open(h.path, h.record ? h.id : null, h.offsets)}>
                  <div className="row"><b>{h.id}</b>{h.record && <span className={`cat cat-${h.record.category}`}>{h.record.category}</span>}<Chip v={h.validity} /><span className={`chip kind-${h.kind}`}>{h.kind}</span><span className={`chip match-${h.match}`}>{h.match} match</span></div>
                  <div className="body clamp">{h.text}</div>
                  <div className="muted small">{h.path}:{h.lines[0]} · {h.retrievers.join('+')}</div>
                </button>
                {h.code_refs?.length > 0 && (
                  <div className="refs small">implemented in {h.code_refs.map((c) => (
                    <button key={`${c.path}:${c.line}`} className="ref" onClick={() => open(c.path)} title={`${c.path}:${c.line}`}><code>{c.name}</code></button>
                  ))}</div>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}

function Bundle() {
  const [task, setTask] = useState('')
  const [tokens, setTokens] = useState(4000)
  const [code, setCode] = useState(false)
  const [md, setMd] = useState('')
  const run = async (e) => {
    e.preventDefault()
    setMd((await api(`/api/bundle?task=${encodeURIComponent(task)}&tokens=${tokens}&code=${code ? 1 : 0}`)).markdown)
  }
  return (
    <div className="pad">
      <form onSubmit={run} className="bundle-form">
        <input placeholder="Task, e.g. Implement native navigation" value={task} onChange={(e) => setTask(e.target.value)} />
        <input type="number" min="200" step="100" value={tokens} onChange={(e) => setTokens(e.target.value)} aria-label="token budget" />
        <button>Build</button>
      </form>
      <CodeToggle code={code} setCode={setCode} />
      {md && (
        <>
          <button className="copy" onClick={() => navigator.clipboard.writeText(md)}>Copy</button>
          <pre className="bundle">{md}</pre>
        </>
      )}
    </div>
  )
}
