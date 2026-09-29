// La fecha ISO mantiene juntos el año y el mes, independientemente de su etiqueta.
(() => {
  const button = document.querySelector('[data-report-sort]');
  if (!button) return;
  const body = button.closest('table').tBodies[0];
  let descending = true;
  button.disabled = false;
  button.addEventListener('click', () => {
    descending = !descending;
    [...body.rows].sort((a,b) => (descending ? -1 : 1)*a.dataset.periodo.localeCompare(b.dataset.periodo)).forEach(row => body.append(row));
    button.closest('th').setAttribute('aria-sort', descending ? 'descending' : 'ascending');
  });
})();
