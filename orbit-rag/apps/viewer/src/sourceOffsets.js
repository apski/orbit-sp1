// rehype plugin: wrap every hast text node in <span data-o={sourceOffset}> so a DOM
// selection maps back to exact source offsets (UTF-16, same as JS strings), and
// split text at highlight boundaries so highlights render as marks.
export function sourceOffsets({ source, highlights }) {
  const hl = highlights.filter((h) => h.start != null && h.end > h.start)
  const segment = (a, b) => {
    const on = hl.filter((h) => h.start <= a && h.end >= b)
    const props = { dataO: a }
    if (on.length) {
      props.className = ['hl', `cat-${on[0].category}`, `v-${on[0].validity}`]
      props.dataCtx = on.map((h) => h.id).join(' ')
    }
    return { type: 'element', tagName: 'span', properties: props, children: [{ type: 'text', value: source.slice(a, b) }] }
  }
  const split = (t) => {
    const p = t.position
    if (!p || p.start.offset == null) return [t]
    const idx = source.slice(p.start.offset, p.end.offset).indexOf(t.value)
    if (idx < 0 || !t.value) return [t] // escaped/entity text: not selectable as source
    const s = p.start.offset + idx
    const e = s + t.value.length
    const cuts = new Set([s, e])
    for (const h of hl) for (const x of [h.start, h.end]) if (x > s && x < e) cuts.add(x)
    const pts = [...cuts].sort((x, y) => x - y)
    return pts.slice(0, -1).map((a, i) => segment(a, pts[i + 1]))
  }
  const walk = (node) => {
    if (!node.children) return
    node.children = node.children.flatMap((c) => (c.type === 'text' ? split(c) : (walk(c), [c])))
  }
  return () => (tree) => walk(tree)
}

// DOM point -> source offset, using the nearest span[data-o].
export function pointToSource(node, offset, isEnd) {
  if (node.nodeType === Node.TEXT_NODE) {
    const span = node.parentElement?.closest('[data-o]')
    return span ? Number(span.dataset.o) + offset : null
  }
  const spans = (el) => (el?.nodeType === Node.ELEMENT_NODE ? [...(el.matches('[data-o]') ? [el] : []), ...el.querySelectorAll('[data-o]')] : [])
  if (isEnd && offset > 0) {
    const last = spans(node.childNodes[offset - 1]).pop()
    if (last) return Number(last.dataset.o) + last.textContent.length
  }
  const first = spans(node.childNodes[offset])[0]
  return first ? Number(first.dataset.o) : null
}
