// Consulta local de todas las filas; cada tabla conserva sus propios filtros y orden.
(() => {
  const collator = new Intl.Collator('es', {numeric: true, sensitivity: 'base'});
  const normalize = value => value.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('es');
  const compact = value => value.replace(/\s+/g, ' ').trim();
  document.querySelectorAll('[data-inline-table]').forEach(root => {
    const table = root.querySelector('table'), body = table.tBodies[0], rows = [...body.rows];
    const scroll = root.querySelector('.inline-table-scroll'), search = root.querySelector('[data-inline-search]');
    const popup = root.querySelector('[data-column-popover]'), valueSearch = popup.querySelector('input[type=search]');
    const valuesBox = popup.querySelector('[data-facet-values]'), allValues = popup.querySelector('[data-all-values]');
    const titles = [...table.querySelectorAll('[data-sort]')].map(button => button.textContent);
    const value = (row, key) => row.cells[key].dataset.value ?? compact(row.cells[key].textContent);
    const universes = titles.map((_, key) => [...new Set(rows.map(row => value(row, key)))].sort(collator.compare));
    const needles = new Map(rows.map(row => [row, normalize(compact(row.textContent))]));
    const selected = new Map();
    let column = null, descending = false, openColumn = null;
    function matches(row, except = null) {
      return needles.get(row).includes(normalize(search.value.trim()))
        && [...selected].every(([key, values]) => key === except || values.has(value(row, key)));
    }
    function sizeTable() {
      if (!table.getClientRects().length) return; // Un desplegable cerrado se mide al abrirse.
      const visible = [...body.rows].filter(row => !row.hidden);
      const height = table.tHead.getBoundingClientRect().height + visible.slice(0, 6).reduce((sum, row) => sum + row.getBoundingClientRect().height, 0);
      const scrollbar = scroll.offsetHeight - scroll.clientHeight;
      scroll.style.setProperty('--inline-height', `${Math.ceil(height) + scrollbar + 1}px`);
    }
    function positionPopup() {
      if (popup.hidden) return;
      const rect = root.querySelector(`[data-filter="${openColumn}"]`).getBoundingClientRect();
      popup.style.left = `${Math.max(8, Math.min(rect.right - popup.offsetWidth, innerWidth - popup.offsetWidth - 8))}px`;
      popup.style.top = `${Math.max(8, Math.min(rect.bottom + 6, innerHeight - popup.offsetHeight - 8))}px`;
    }
    function filterButtons() {
      root.querySelectorAll('[data-filter]').forEach(button => {
        const key = Number(button.dataset.filter);
        button.hidden = false;
        button.classList.toggle('is-filtered', selected.has(key));
        button.setAttribute('aria-expanded', String(openColumn === key));
        button.setAttribute('aria-label', `Filtrar por ${titles[key]}${selected.has(key) ? ' · filtro activo' : ''}`);
      });
    }
    function closePopup(restore = false) {
      const previous = openColumn;
      popup.hidden = true; openColumn = null; filterButtons();
      if (restore && previous !== null) root.querySelector(`[data-filter="${previous}"]`).focus({preventScroll: true});
    }
    function renderValues() {
      if (openColumn === null) return;
      const focused = document.activeElement?.dataset.facetValue, top = valuesBox.scrollTop, counts = new Map();
      rows.filter(row => matches(row, openColumn)).forEach(row => {const v = value(row, openColumn); counts.set(v, (counts.get(v) || 0) + 1);});
      const values = universes[openColumn], chosen = selected.get(openColumn) || new Set(values);
      popup.querySelector('strong').textContent = `Filtrar · ${titles[openColumn]}`;
      valuesBox.replaceChildren();
      values.filter(v => normalize(v).includes(normalize(valueSearch.value.trim()))).forEach(v => {
        const label = document.createElement('label'), input = document.createElement('input');
        const name = document.createElement('span'), count = document.createElement('span');
        input.type = 'checkbox'; input.dataset.facetValue = v; input.checked = chosen.has(v);
        name.textContent = v || 'Sin dato'; count.textContent = counts.get(v) || 0; count.className = 'facet-count';
        label.append(input, name, count); valuesBox.append(label);
        if (focused === v) input.focus({preventScroll: true});
      });
      if (!valuesBox.childElementCount) {const p = document.createElement('p'); p.textContent = 'No hay valores coincidentes.'; valuesBox.append(p);}
      valuesBox.scrollTop = top;
      allValues.checked = chosen.size === values.length;
      allValues.indeterminate = chosen.size > 0 && chosen.size < values.length;
      positionPopup();
    }
    function refresh() {
      rows.forEach(row => {row.hidden = !matches(row);});
      const visible = rows.filter(row => !row.hidden).length;
      root.querySelector('[data-inline-status]').textContent = `${visible} de ${rows.length} filas${column === null ? '' : ` · Orden ${descending ? 'descendente' : 'ascendente'} por ${titles[column]}`}.`;
      root.querySelector('[data-no-matches]').hidden = visible > 0 || !rows.length;
      scroll.scrollTop = 0;
      const chips = root.querySelector('[data-filter-summary]'); chips.replaceChildren();
      selected.forEach((values, key) => {
        const button = document.createElement('button'); button.type = 'button'; button.className = 'filter-chip';
        button.dataset.removeFilter = key; button.textContent = `${titles[key]} · ${values.size} valores ×`;
        button.setAttribute('aria-label', `Quitar filtro de ${titles[key]}`); chips.append(button);
      });
      filterButtons(); sizeTable(); renderValues();
    }
    root.querySelector('.inline-table-toolbar').hidden = false;
    root.querySelectorAll('[data-sort]').forEach(button => {button.disabled = false;});
    search.addEventListener('input', refresh);
    valueSearch.addEventListener('input', renderValues);
    popup.addEventListener('change', event => {
      const input = event.target;
      if (input === allValues) {
        if (input.checked) selected.delete(openColumn); else selected.set(openColumn, new Set());
      } else if (input.matches('[data-facet-value]')) {
        const values = new Set(selected.get(openColumn) || universes[openColumn]);
        if (input.checked) values.add(input.dataset.facetValue); else values.delete(input.dataset.facetValue);
        if (values.size === universes[openColumn].length) selected.delete(openColumn); else selected.set(openColumn, values);
      } else return;
      refresh();
    });
    root.addEventListener('click', event => {
      const filter = event.target.closest('[data-filter]');
      if (filter) {
        const key = Number(filter.dataset.filter);
        if (openColumn === key) return closePopup(true);
        openColumn = key; valueSearch.value = ''; popup.hidden = false;
        filterButtons(); renderValues(); valueSearch.focus({preventScroll: true}); return;
      }
      if (event.target.closest('[data-close-filter]')) return closePopup(true);
      if (event.target.closest('[data-clear-column]')) {selected.delete(openColumn); refresh(); return;}
      const remove = event.target.closest('[data-remove-filter]');
      if (remove) {selected.delete(Number(remove.dataset.removeFilter)); refresh(); search.focus({preventScroll: true}); return;}
      const order = event.target.closest('[data-sort]');
      if (order) {
        const key = Number(order.dataset.sort); descending = key === column ? !descending : false; column = key;
        const sortValue = row => row.cells[column].dataset.sortValue ?? value(row, column);
        [...rows].sort((a, b) => (descending ? -1 : 1) * collator.compare(sortValue(a), sortValue(b))).forEach(row => body.append(row));
        table.querySelectorAll('[data-sort]').forEach(button => button.closest('th').setAttribute('aria-sort', Number(button.dataset.sort) === column ? (descending ? 'descending' : 'ascending') : 'none'));
        refresh(); return;
      }
      if (event.target.closest('[data-clear-all]')) {selected.clear(); search.value = ''; refresh(); search.focus();}
    });
    document.addEventListener('click', event => {if (!popup.hidden && !popup.contains(event.target) && !root.contains(event.target.closest('[data-filter]'))) closePopup();});
    document.addEventListener('keydown', event => {if (event.key === 'Escape' && !popup.hidden) {event.preventDefault(); closePopup(true);}});
    document.addEventListener('scroll', event => {if (!popup.contains(event.target)) positionPopup();}, {capture: true});
    root.addEventListener('toggle', sizeTable, true);
    const observer = new ResizeObserver(() => {sizeTable(); positionPopup();}); observer.observe(table);
    window.addEventListener('resize', positionPopup);
    refresh();
  });
})();
