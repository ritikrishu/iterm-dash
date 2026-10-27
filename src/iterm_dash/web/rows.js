const HEX = /^#[0-9a-fA-F]{6}$/;
const FLAG_CLASSES = [[1, 'b'], [2, 'i'], [4, 'u'], [8, 'f'], [16, 's'], [32, 'w']];
const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

function esc(s) {
  // The HTML parser silently drops U+0000, which would erase a terminal blank cell.
  return String(s).replace(/\u0000/g, ' ').replace(/[&<>"']/g, (ch) => ESC[ch]);
}

export function renderRunsHTML(runs) {
  let out = '';
  for (const [text, fg, bg, flags] of runs) {
    const style = [];
    if (typeof fg === 'string' && HEX.test(fg)) style.push(`color:${fg}`);
    if (typeof bg === 'string' && HEX.test(bg)) style.push(`background:${bg}`);
    const classes = FLAG_CLASSES.filter(([bit]) => (flags | 0) & bit).map(([, c]) => c);
    let attrs = '';
    if (classes.length) attrs += ` class="${classes.join(' ')}"`;
    if (style.length) attrs += ` style="${style.join(';')}"`;
    out += attrs ? `<span${attrs}>${esc(text)}</span>` : esc(text);
  }
  return out;
}
