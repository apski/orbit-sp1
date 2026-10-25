// Token-cheap view of a pulled Figma frame for agents: one line per node, layout and sizing in a few words, instead of
// the raw REST JSON (the fixture: 19 KB raw -> 2 KB outline; real files carry far more fields per node). Usage via `spectra figma inspect <raw> [--depth N]`.
const AX = { HORIZONTAL: 'row', VERTICAL: 'col', NONE: 'abs', GRID: 'grid' };

export function inspect(doc, { depth = Infinity } = {}) {
  const lines = [];
  const top = doc.document.absoluteBoundingBox ?? { x: 0, y: 0 };
  const walk = (n, d, parent) => {
    if (n.visible === false || d > depth) return;
    const b = n.absoluteBoundingBox ?? { x: 0, y: 0, width: 0, height: 0 };
    const bits = [`${n.type.toLowerCase()} "${n.name}"`, `${Math.round(b.width)}x${Math.round(b.height)}@${Math.round(b.x - top.x)},${Math.round(b.y - top.y)}`];
    if (n.layoutMode && n.layoutMode !== 'NONE') {
      bits.push(`${AX[n.layoutMode]} gap ${n.primaryAxisAlignItems === 'SPACE_BETWEEN' ? 'auto' : n.itemSpacing ?? 0}`);
      const pad = ['Top', 'Right', 'Bottom', 'Left'].map(e => n[`padding${e}`] ?? 0);
      if (pad.some(Boolean)) bits.push(`pad ${pad.join('/')}`);
      if (n.primaryAxisAlignItems && n.primaryAxisAlignItems !== 'MIN') bits.push(`justify ${n.primaryAxisAlignItems.toLowerCase()}`);
      if (n.counterAxisAlignItems && n.counterAxisAlignItems !== 'MIN') bits.push(`align ${n.counterAxisAlignItems.toLowerCase()}`);
    }
    if (parent?.layoutMode && parent.layoutMode !== 'NONE') {
      const h = n.layoutSizingHorizontal, v = n.layoutSizingVertical;
      if (h || v) bits.push(`size ${(h ?? '?').toLowerCase()}/${(v ?? '?').toLowerCase()}`);
      if (n.layoutPositioning === 'ABSOLUTE') bits.push('absolute');
    }
    if (n.type === 'TEXT') bits.push(`${JSON.stringify(n.characters.length > 40 ? n.characters.slice(0, 40) + '…' : n.characters)} ${n.style?.fontSize}/${n.style?.fontWeight}`);
    if (n.type === 'INSTANCE') {
      const props = Object.entries(n.componentProperties ?? {}).map(([k, v]) => `${k.replace(/#[^#]*$/, '')}=${v.value}`);
      const meta = doc.components?.[n.componentId], set = meta?.componentSetId && doc.componentSets?.[meta.componentSetId];
      bits.push(`instance of ${set ? `${set.name} / ` : ''}${meta?.name ?? n.componentId}${props.length ? ` {${props.join(', ')}}` : ''}`);
    }
    lines.push('  '.repeat(d) + bits.join(' · '));
    (n.children ?? []).forEach(c => walk(c, d + 1, n));
  };
  walk(doc.document, 0, null);
  return lines.join('\n');
}
