// Filtros sin navegación de documento: conserva foco, orden y todos los resultados.
(() => {
  const root = document.querySelector('#institution-directory');
  if (!root) return;
  root.classList.add('directory-live');
  const search = root.querySelector('.directory-search');
  const query = search.querySelector('#q');
  const level = search.querySelector('[name=nivel_ie]');
  const period = search.querySelector('[name=actualizado]');
  const status = root.querySelector('[data-directory-status]');
  const retry = root.querySelector('[data-directory-retry]');
  const collator = new Intl.Collator('es', {numeric:true, sensitivity:'base'});
  const popup = root.querySelector('[data-column-popover]');
  const valueSearch = popup.querySelector('#column-value-search');
  const valuesBox = popup.querySelector('[data-facet-values]');
  const allValues = popup.querySelector('[data-all-values]');
  let {facetas: facets, seleccion: selected} = JSON.parse(root.querySelector('[data-directory-facets]').textContent);
  let openColumn = null;
  let column = 0, descending = false, timer, controller, requestId = 0, observer;
  const isChecked = (key,value) => !selected[key] || selected[key].valores.includes(value) === (selected[key].modo === 'incluir');
  function filterButtons() {
    root.querySelectorAll('[data-filter]').forEach(button => {
      button.hidden = false;
      button.classList.toggle('is-filtered', !!selected[button.dataset.filter]);
      button.setAttribute('aria-expanded', String(openColumn === button.dataset.filter));
      button.setAttribute('aria-label', `Filtrar por ${facets.find(f=>f.columna===button.dataset.filter).titulo}${selected[button.dataset.filter]?' · filtro activo':''}`);
    });
    search.querySelector('[name=fc]').value = Object.keys(selected).length ? JSON.stringify(selected) : '';
  }
  function positionPopup() {
    if (popup.hidden) return;
    const anchor = root.querySelector(`[data-filter="${openColumn}"]`);
    if (!anchor) {closePopup(); return;}
    const r = anchor.getBoundingClientRect();
    popup.style.left = `${Math.max(8,Math.min(r.right-popup.offsetWidth,innerWidth-popup.offsetWidth-8))}px`;
    popup.style.top = `${Math.max(8,Math.min(r.bottom+6,innerHeight-popup.offsetHeight-8))}px`;
  }
  function closePopup(restoreFocus = false) {
    const previous = openColumn;
    popup.hidden = true; openColumn = null; filterButtons();
    if (restoreFocus && previous) root.querySelector(`[data-filter="${previous}"]`)?.focus({preventScroll:true});
  }
  function renderValues() {
    if (!openColumn) return;
    const focusedValue = document.activeElement?.dataset.facetValue;
    const scroll = valuesBox.scrollTop;
    const facet = facets.find(f=>f.columna===openColumn);
    popup.querySelector('#column-filter-title').textContent = `Filtrar · ${facet.titulo}`;
    const needle = valueSearch.value.trim().toLocaleLowerCase('es');
    const shown = facet.valores.filter(v=>v.etiqueta.toLocaleLowerCase('es').includes(needle)).sort((a,b)=>collator.compare(a.etiqueta,b.etiqueta));
    valuesBox.replaceChildren();
    shown.forEach(value => {
      const label = document.createElement('label'), input = document.createElement('input'), name = document.createElement('span'), count = document.createElement('span');
      input.type = 'checkbox'; input.dataset.facetValue = value.valor; input.checked = isChecked(openColumn,value.valor);
      name.textContent = value.etiqueta; count.textContent = value.cantidad; count.className = 'facet-count';
      label.append(input,name,count); valuesBox.append(label);
      if (focusedValue === value.valor) input.focus({preventScroll:true});
    });
    if (!shown.length) {const empty=document.createElement('p'); empty.textContent='No hay valores coincidentes.'; valuesBox.append(empty);}
    valuesBox.scrollTop = scroll;
    const checked = facet.valores.filter(v=>isChecked(openColumn,v.valor)).length;
    allValues.checked = !selected[openColumn] || (facet.valores.length>0 && checked===facet.valores.length);
    allValues.indeterminate = checked>0 && checked<facet.valores.length;
    positionPopup();
  }
  function updateColumn() {filterButtons(); renderValues(); refresh();}
  valueSearch.addEventListener('input', renderValues);
  popup.addEventListener('change', event => {
    const input = event.target;
    if (input === allValues) {
      if (input.checked) delete selected[openColumn];
      else selected[openColumn] = {modo:'incluir',valores:[]};
    } else if (input.matches('[data-facet-value]')) {
      const rule = selected[openColumn] || {modo:'excluir',valores:[]};
      const values = new Set(rule.valores);
      if (input.checked === (rule.modo === 'incluir')) values.add(input.dataset.facetValue);
      else values.delete(input.dataset.facetValue);
      selected[openColumn] = {modo:rule.modo,valores:[...values]};
      if (rule.modo === 'excluir' && !values.size) delete selected[openColumn];
    } else return;
    updateColumn();
  });
  document.addEventListener('click', event => {
    if (!popup.hidden && !popup.contains(event.target) && !event.target.closest('[data-filter]')) closePopup();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !popup.hidden) {event.preventDefault(); closePopup(true);}
  });
  document.addEventListener('focusin', event => {
    if (!popup.hidden && !popup.contains(event.target) && !event.target.closest('[data-filter]')) closePopup();
  });
  window.addEventListener('resize',positionPopup);
  window.addEventListener('scroll',positionPopup,true);
  function sizeTable() {
    const box = root.querySelector('[data-eight-rows]');
    if (!box) return;
    const table = box.querySelector('table');
    const resize = () => {
      const rows = [...table.tBodies[0].rows].slice(0,8);
      const h = table.tHead.getBoundingClientRect().height + rows.reduce((s,r)=>s+r.getBoundingClientRect().height,0);
      box.style.setProperty('--directory-height', `${Math.ceil(h)}px`);
    };
    resize();
    observer?.disconnect();
    if ('ResizeObserver' in window) { observer = new ResizeObserver(resize); observer.observe(table); }
  }
  function sort() {
    const table = root.querySelector('.institution-table');
    if (!table) return;
    const body = table.tBodies[0];
    const values = new Map([...body.rows].map(row => {
      const cell = row.cells[column].cloneNode(true);
      cell.querySelectorAll('small').forEach(el=>el.remove());
      return [row, cell.textContent.trim()];
    }));
    [...body.rows].sort((a,b) => (descending?-1:1)*collator.compare(values.get(a),values.get(b))).forEach(row=>body.append(row));
    table.querySelectorAll('[data-sort]').forEach(button => {
      button.disabled = false;
      button.closest('th').setAttribute('aria-sort', Number(button.dataset.sort) === column ? (descending?'descending':'ascending') : 'none');
    });
    sizeTable();
  }
  function filters() {
    const url = new URL(location.pathname, location.origin);
    for (const [k,v] of [['q',query.value.trim()],['nivel_ie',level.value],['actualizado',period.value]]) if (v) url.searchParams.set(k,v);
    if (Object.keys(selected).length) url.searchParams.set('fc',JSON.stringify(selected));
    return url;
  }
  function cleanLinks() {
    const create = root.querySelector('[data-create-institution]');
    if (create) {
      const url = new URL(create.href);
      create.href = url.pathname + filters().search;
    }
    root.querySelectorAll('[data-institution-link]').forEach(link => {
      const url = new URL(link.href);
      for (const [k,v] of [...url.searchParams]) if (!v) url.searchParams.delete(k);
      link.href = url.pathname + url.search;
    });
  }
  async function refresh() {
    clearTimeout(timer);
    controller?.abort();
    controller = new AbortController();
    const id = ++requestId, url = filters();
    const results = root.querySelector('[data-directory-fragment=results]');
    results.setAttribute('aria-busy','true');
    status.textContent = 'Actualizando resultados…'; retry.hidden = true;
    try {
      const response = await fetch(url, {credentials:'same-origin',cache:'no-store',signal:controller.signal});
      if (id !== requestId) return;
      if (response.redirected && new URL(response.url).pathname === '/ingresar') throw new Error('La sesión venció. Abre Ingresar en otra pestaña y vuelve a intentarlo.');
      if (!response.ok) throw new Error('No pudimos actualizar los resultados. Conservamos la lista anterior.');
      const doc = new DOMParser().parseFromString(await response.text(),'text/html');
      if (id !== requestId) return;
      if (!doc.querySelector('#institution-directory')) throw new Error('No pudimos actualizar los resultados. Conservamos la lista anterior.');
      const focused = document.activeElement;
      const focusLevel = focused?.dataset.level;
      const focusPeriod = focused?.id === 'actualizado';
      const opened = root.querySelector('.monthly-update details')?.open;
      facets = JSON.parse(doc.querySelector('[data-directory-facets]').textContent).facetas;
      root.querySelectorAll('[data-directory-fragment]').forEach(fragment => {
        const next = doc.querySelector(`[data-directory-fragment="${fragment.dataset.directoryFragment}"]`);
        fragment.replaceChildren(...next.childNodes);
      });
      root.querySelector('[data-directory-fragment=results]').removeAttribute('aria-busy');
      root.querySelector('.monthly-update details').open = opened;
      // Los controles escritos permanecen; solo sus enlaces auxiliares cambian.
      search.querySelector('a')?.remove();
      const clear = doc.querySelector('.directory-search a');
      if (clear) search.append(clear);
      period.value = root.querySelector('#actualizado')?.value || '';
      sort(); cleanLinks(); filterButtons(); renderValues();
      root.querySelector('.directory-scroll')?.scrollTo({top:0});
      history.replaceState(null,'',url.pathname+url.search+location.hash);
      if (focusPeriod) root.querySelector('#actualizado')?.focus({preventScroll:true});
      if (focusLevel !== undefined) [...root.querySelectorAll('[data-level]')].find(a=>a.dataset.level===focusLevel)?.focus({preventScroll:true});
      status.textContent = root.querySelector('[data-directory-fragment=count]').textContent.trim();
    } catch (error) {
      if (error.name === 'AbortError' || id !== requestId) return;
      status.textContent = error.message.includes('sesión venció') ? error.message : 'No pudimos actualizar los resultados. La lista anterior se conserva; revisa la conexión y reintenta.';
      retry.hidden = false;
    } finally {
      if (id === requestId) root.querySelector('[data-directory-fragment=results]').removeAttribute('aria-busy');
    }
  }
  query.addEventListener('input', () => {
    clearTimeout(timer); controller?.abort(); requestId++;
    timer = setTimeout(refresh,100);
  });
  root.addEventListener('submit', event => { if (event.target.matches('.directory-search,.monthly-update form')) { event.preventDefault(); refresh(); } });
  root.addEventListener('change', event => { if (event.target.id === 'actualizado') { period.value = event.target.value; refresh(); } });
  root.addEventListener('click', event => {
    const filterButton = event.target.closest('[data-filter]');
    if (filterButton) {
      if (openColumn === filterButton.dataset.filter) {closePopup(true); return;}
      openColumn = filterButton.dataset.filter; valueSearch.value = ''; valuesBox.scrollTop = 0;
      popup.hidden = false; filterButtons(); renderValues(); valueSearch.focus({preventScroll:true}); return;
    }
    if (event.target.closest('[data-close-filter]')) {closePopup(true); return;}
    if (event.target.closest('[data-clear-column]')) {delete selected[openColumn]; updateColumn(); return;}
    const remove = event.target.closest('[data-remove-filter]');
    if (remove) {delete selected[remove.dataset.removeFilter]; updateColumn(); query.focus({preventScroll:true}); return;}
    const sortButton = event.target.closest('[data-sort]');
    if (sortButton) {
      const next = Number(sortButton.dataset.sort);
      descending = column === next ? !descending : false; column = next;
      sort(); status.textContent = `Ordenado por ${sortButton.textContent}, ${descending?'descendente':'ascendente'}.`;
      return;
    }
    if (event.target.closest('[data-directory-retry]')) { refresh(); return; }
    const link = event.target.closest('.level-filters a,.directory-search a,.empty a');
    if (!link || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    const params = new URL(link.href).searchParams;
    if (link.matches('.level-filters a')) level.value = link.dataset.level;
    else { query.value = ''; level.value = ''; selected = {}; filterButtons(); }
    period.value = params.get('actualizado') || period.value;
    refresh();
  });
  sort(); cleanLinks(); filterButtons();
})();
