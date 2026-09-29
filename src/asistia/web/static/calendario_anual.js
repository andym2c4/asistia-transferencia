// Un único detalle flotante evita repetir la explicación en cada día.
(() => {
  const root = document.querySelector('.annual-calendar');
  if (!root) return;
  const tooltip = root.querySelector('#annual-tooltip');
  let active, pinned = false;
  function close() {
    active?.removeAttribute('aria-describedby');
    active = null;
    pinned = false;
    tooltip.hidden = true;
  }
  function show(target, persistent = false) {
    close(); active = target; pinned = persistent;
    tooltip.textContent = target.dataset.calendarHint;
    tooltip.hidden = false;
    target.setAttribute('aria-describedby', tooltip.id);
    const box = target.getBoundingClientRect();
    tooltip.style.left = `${Math.max(8, Math.min(box.left, innerWidth - tooltip.offsetWidth - 8))}px`;
    const y = box.bottom + 8;
    tooltip.style.top = `${y + tooltip.offsetHeight < innerHeight ? y : Math.max(8, box.top - tooltip.offsetHeight - 8)}px`;
  }
  root.querySelectorAll('[data-calendar-hint]').forEach(target => {
    target.removeAttribute('title'); // El título nativo queda como alternativa sin JavaScript.
    target.addEventListener('mouseenter', () => show(target));
    target.addEventListener('mouseleave', () => { if (!pinned) close(); });
    target.addEventListener('focus', () => show(target, true));
    target.addEventListener('blur', close);
    target.addEventListener('click', () => show(target, true));
  });
  document.addEventListener('keydown', event => { if (event.key === 'Escape') close(); });
  document.addEventListener('click', event => { if (!event.target.closest('[data-calendar-hint]')) close(); });
  const reposition = () => {
    if (active && pinned) show(active, true);
    else close();
  };
  window.addEventListener('scroll', reposition, true);
  window.addEventListener('resize', reposition);
})();
