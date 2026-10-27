const ORDER = ['waiting', 'error', 'done'];

export function nextNeedsYou(cells, lastId) {
  const list = [];
  for (const tone of ORDER) {
    cells
      .filter((c) => c.tone === tone)
      .sort((x, y) => x.changedAt - y.changedAt)
      .forEach((c) => list.push(c.id));
  }
  if (list.length === 0) return null;
  const i = lastId == null ? -1 : list.indexOf(lastId);
  return list[(i + 1) % list.length];
}

export function moveFocus(index, key, columns, count) {
  let target = index;
  if (key === 'ArrowLeft') target = index - 1;
  else if (key === 'ArrowRight') target = index + 1;
  else if (key === 'ArrowUp') target = index - columns;
  else if (key === 'ArrowDown') target = index + columns;
  return target >= 0 && target <= count - 1 ? target : index;
}

export function shouldHandleKey(event) {
  return !(event.metaKey || event.ctrlKey || event.altKey);
}

// The cell to focus after the visible cells change: the focused cell if it is still shown, else the cell that
// took its place, else cell 1, so Space and Enter work as soon as the grid has cells.
export function keepFocus(prevIds, nextIds, focusedId) {
  if (!nextIds.length) return null;
  if (focusedId != null && nextIds.includes(focusedId)) return focusedId;
  const i = focusedId == null ? -1 : prevIds.indexOf(focusedId);
  return nextIds[i >= 0 ? Math.min(i, nextIds.length - 1) : 0];
}
