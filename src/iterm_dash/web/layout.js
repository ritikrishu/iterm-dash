export function chooseLayout({ width, height, count, cols, rows, charWidthPx, lineHeight, minFontPx, maxFontPx, headerPx, gapPx }) {
  if (!(count > 0)) return { columns: 0, cellRows: 0, fontPx: 0, visibleRows: 0 };
  const fontW = (c) => ((width - gapPx * (c - 1)) / c) / (cols * charWidthPx);
  let maxByFont = 0;
  for (let c = 1; c <= count; c++) if (fontW(c) >= minFontPx) maxByFont = c;
  const cap = Math.max(Math.min(2, count), maxByFont);
  const cellRows = Math.ceil(count / cap);
  const columns = Math.ceil(count / cellRows);
  const cellHeight = (height - gapPx * (cellRows - 1)) / cellRows - headerPx;
  const fontH = cellHeight / (rows * lineHeight);
  const fontPx = Math.min(fontW(columns), maxFontPx, Math.max(fontH, minFontPx));
  const visibleRows = Math.max(1, Math.min(rows, Math.floor(cellHeight / (fontPx * lineHeight) + 1e-9)));
  return { columns, cellRows, fontPx, visibleRows };
}
