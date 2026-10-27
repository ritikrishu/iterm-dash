export function createClickRouter({ onSingle, onDouble, windowMs = 200, setTimer = setTimeout, clearTimer = clearTimeout }) {
  let pendingId = null;
  let timer = null;
  return (id) => {
    if (timer !== null && pendingId === id) {
      clearTimer(timer);
      timer = null;
      pendingId = null;
      onDouble(id);
      return;
    }
    if (timer !== null) {
      clearTimer(timer);
      timer = null;
      const prev = pendingId;
      pendingId = null;
      onSingle(prev);
    }
    pendingId = id;
    timer = setTimer(() => {
      timer = null;
      pendingId = null;
      onSingle(id);
    }, windowMs);
  };
}
