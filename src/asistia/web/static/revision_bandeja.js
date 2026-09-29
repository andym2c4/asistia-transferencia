// La bandeja completa se filtra en memoria; nunca limita la búsqueda a las filas visibles.
(() => {
  let dispose = () => {}, snapshot = () => null;
  function initialize() {
    const previous = snapshot(); dispose();
    const controller = new AbortController(), signal = controller.signal;
    snapshot = () => null; dispose = () => controller.abort();
    const root = document.querySelector('#revision-directory');
    const table = root?.querySelector('table');
    if (!table) return;
    const body = table.tBodies[0], scroll = table.parentElement;
    const rows = [...body.rows];
    const search = root.querySelector('[data-revision-search]');
    const status = root.querySelector('[data-revision-status]');
    const popup = root.querySelector('[data-column-popover]');
    const valueSearch = popup.querySelector('input[type=search]');
    const valuesBox = popup.querySelector('[data-facet-values]');
    const allValues = popup.querySelector('[data-all-values]');
    const titles = [...table.querySelectorAll('[data-sort]')].map(b => b.textContent);
    const collator = new Intl.Collator('es', {numeric:true, sensitivity:'base'});
    const normalize = text => text.normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLocaleLowerCase('es');
    const cellValue = (row, column) => row.cells[column].dataset.value;
    const universes = titles.map((_,column) => [...new Set(rows.map(row => cellValue(row,column)))].sort(collator.compare));
    const needles = new Map(rows.map(row => [row,normalize(row.dataset.search)]));
    const selected = new Map(previous?.selected || []);
    selected.forEach((values,key) => {universes[key]=[...new Set([...universes[key],...values])].sort(collator.compare);});
    let state = previous?.state || '', column = previous?.column || 0, descending = previous?.descending || false, openColumn = null;
    search.value = previous?.query || '';
    snapshot = () => ({selected:[...selected],state,column,descending,query:search.value});
    function matches(row, except = null, withState = true) {
      return (!withState || !state || row.dataset.state === state)
        && needles.get(row).includes(normalize(search.value.trim()))
        && [...selected].every(([key,values]) => key === except || values.has(cellValue(row,key)));
    }
    function sizeTable() {
      const visible = [...body.rows].filter(row => !row.hidden);
      const height = table.tHead.getBoundingClientRect().height
        + visible.slice(0,6).reduce((sum,row) => sum+row.getBoundingClientRect().height,0);
      scroll.style.setProperty('--revision-height', `${Math.ceil(height)+1}px`);
    }
    function positionPopup() {
      if (popup.hidden) return;
      const rect = root.querySelector(`[data-filter="${openColumn}"]`).getBoundingClientRect();
      popup.style.left = `${Math.max(8,Math.min(rect.right-popup.offsetWidth,innerWidth-popup.offsetWidth-8))}px`;
      popup.style.top = `${Math.max(8,Math.min(rect.bottom+6,innerHeight-popup.offsetHeight-8))}px`;
    }
    function filterButtons() {
      root.querySelectorAll('[data-filter]').forEach(button => {
        const key = Number(button.dataset.filter);
        button.hidden = false;
        button.classList.toggle('is-filtered', selected.has(key));
        button.setAttribute('aria-expanded', String(openColumn === key));
        button.setAttribute('aria-label', `Filtrar por ${titles[key]}${selected.has(key)?' · filtro activo':''}`);
      });
    }
    function closePopup(restore = false) {
      const previous = openColumn;
      popup.hidden = true; openColumn = null; filterButtons();
      if (restore && previous !== null) root.querySelector(`[data-filter="${previous}"]`).focus({preventScroll:true});
    }
    function renderValues() {
      if (openColumn === null) return;
      const focused = document.activeElement?.dataset.facetValue, top = valuesBox.scrollTop;
      const counts = new Map();
      rows.filter(row => matches(row,openColumn)).forEach(row => {
        const value = cellValue(row,openColumn); counts.set(value,(counts.get(value)||0)+1);
      });
      const values = universes[openColumn];
      const chosen = selected.get(openColumn) || new Set(values);
      popup.querySelector('strong').textContent = `Filtrar · ${titles[openColumn]}`;
      valuesBox.replaceChildren();
      values.filter(value => normalize(value).includes(normalize(valueSearch.value.trim()))).forEach(value => {
        const label = document.createElement('label'), input = document.createElement('input');
        const name = document.createElement('span'), count = document.createElement('span');
        input.type = 'checkbox'; input.dataset.facetValue = value; input.checked = chosen.has(value);
        name.textContent = value; count.textContent = counts.get(value)||0; count.className = 'facet-count';
        label.append(input,name,count); valuesBox.append(label);
        if (focused === value) input.focus({preventScroll:true});
      });
      if (!valuesBox.childElementCount) {
        const message = document.createElement('p'); message.textContent = 'No hay valores coincidentes.'; valuesBox.append(message);
      }
      valuesBox.scrollTop = top;
      allValues.checked = chosen.size === values.length;
      allValues.indeterminate = chosen.size > 0 && chosen.size < values.length;
      positionPopup();
    }
    function refresh() {
      rows.forEach(row => {row.hidden = !matches(row);});
      const visible = rows.filter(row => !row.hidden).length;
      root.querySelector('[data-revision-count]').textContent = `${visible} de ${rows.length} instituciones`;
      status.textContent = `${visible} instituciones · Orden ${descending?'descendente':'ascendente'} por ${titles[column]}.`;
      root.querySelector('[data-no-matches]').hidden = visible > 0;
      scroll.scrollTop = 0;
      root.querySelectorAll('[data-state]').forEach(button => {
        if (button.tagName !== 'BUTTON') return;
        const value = button.dataset.state;
        button.setAttribute('aria-pressed',String(state === value));
        button.querySelector('span').textContent = rows.filter(row => matches(row,null,false) && (!value || row.dataset.state === value)).length;
      });
      const chips = root.querySelector('[data-filter-summary]'); chips.replaceChildren();
      selected.forEach((values,key) => {
        const button = document.createElement('button'); button.type = 'button'; button.className = 'filter-chip';
        button.dataset.removeFilter = key; button.textContent = `${titles[key]} · ${values.size} valores ×`;
        button.setAttribute('aria-label',`Quitar filtro de ${titles[key]}`); chips.append(button);
      });
      filterButtons(); sizeTable(); renderValues();
    }
    function sort() {
      const value = row => row.cells[column].dataset.sortValue ?? cellValue(row,column);
      [...rows].sort((a,b) => (descending?-1:1)*collator.compare(value(a),value(b))).forEach(row => body.append(row));
      table.querySelectorAll('[data-sort]').forEach(button => {
        button.disabled = false;
        button.closest('th').setAttribute('aria-sort',Number(button.dataset.sort)===column?(descending?'descending':'ascending'):'none');
      });
      refresh();
    }
    search.closest('label').hidden = false;
    root.querySelector('.revision-status-filters').hidden = false;
    search.addEventListener('input',refresh);
    valueSearch.addEventListener('input',renderValues);
    popup.addEventListener('change',event => {
      const input = event.target;
      if (input === allValues) {
        if (input.checked) selected.delete(openColumn);
        else selected.set(openColumn,new Set());
      } else if (input.matches('[data-facet-value]')) {
        const values = new Set(selected.get(openColumn) || universes[openColumn]);
        if (input.checked) values.add(input.dataset.facetValue); else values.delete(input.dataset.facetValue);
        if (values.size === universes[openColumn].length) selected.delete(openColumn); else selected.set(openColumn,values);
      } else return;
      refresh();
    });
    root.addEventListener('click',event => {
      const filter = event.target.closest('[data-filter]');
      if (filter) {
        const key = Number(filter.dataset.filter);
        if (openColumn === key) return closePopup(true);
        openColumn = key; valueSearch.value = ''; popup.hidden = false;
        filterButtons(); renderValues(); valueSearch.focus({preventScroll:true}); return;
      }
      if (event.target.closest('[data-close-filter]')) return closePopup(true);
      if (event.target.closest('[data-clear-column]')) {selected.delete(openColumn); refresh(); return;}
      const remove = event.target.closest('[data-remove-filter]');
      if (remove) {selected.delete(Number(remove.dataset.removeFilter)); refresh(); search.focus({preventScroll:true}); return;}
      const order = event.target.closest('[data-sort]');
      if (order) {const key = Number(order.dataset.sort); descending = key === column ? !descending : false; column = key; sort(); return;}
      const quick = event.target.closest('button[data-state]');
      if (quick) {state = quick.dataset.state; refresh(); return;}
      if (event.target.closest('[data-clear-all]')) {selected.clear(); search.value = ''; state = ''; refresh(); search.focus();}
    });
    document.addEventListener('click',event => {
      if (!popup.hidden && !popup.contains(event.target) && !event.target.closest('[data-filter]')) closePopup();
    }, {signal});
    document.addEventListener('keydown',event => {if (event.key==='Escape' && !popup.hidden) {event.preventDefault(); closePopup(true);}}, {signal});
    document.addEventListener('scroll',event => {if (!popup.contains(event.target)) positionPopup();},{capture:true,signal});
    const observer = new ResizeObserver(() => {sizeTable(); positionPopup();});
    observer.observe(table);
    window.addEventListener('resize',positionPopup,{signal});
    dispose = () => {controller.abort(); observer.disconnect();};
    sort();
  }
  document.addEventListener('asistia:revision-actualizada',initialize);
  initialize();
})();
