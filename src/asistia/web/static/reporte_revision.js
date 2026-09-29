(() => {
  const root = document.getElementById('report-review');
  if (!root) return;
  const q = selector => root.querySelector(selector);
  const all = selector => [...root.querySelectorAll(selector)];
  const cases = all('[data-report-case]');
  const days = all('[data-report-day]');
  const select = q('#report-case');
  const feedback = q('[data-report-feedback]');
  const editor = q('[data-day-editor]');
  const form = q('[data-day-form]');
  const bulkForm = q('[data-vacios-form]');
  const blanks = all('[data-vacios-item]');
  const symbolPicker = q('#report-symbol');
  const digitization = q('[data-digitization-review]');
  const digitizationToggle = q('[data-digitization-toggle]');
  const digitizationReviewed = root.dataset.digitizationReviewed === 'true';
  let comparisonDay = null;
  let bulkActive = false;
  let currentDay = null;
  let lastSymbol = '';
  root.classList.add('report-live');
  const rules = JSON.parse(document.getElementById('report-rules')?.textContent || '{"reglas":[],"equivalencias":{}}');
  const normalized = value => value.normalize('NFD').replace(/[\u0300-\u036f]/g,'').trim().replace(/\s+/g,' ').toUpperCase();
  function updateRule(f, chooseName=false) {
    const picker = f.querySelector('[data-rule-category]');
    if (!picker) return;
    const automatic = f.elements.modo_regla.value === 'auto';
    if (chooseName && picker.value) f.elements.tipo_dia.value = rules.reglas.find(r=>String(r.categoria_id)===picker.value)?.nombre || f.elements.tipo_dia.value;
    const id = picker.value || rules.equivalencias[normalized(f.elements.tipo_dia.value)];
    const rule = automatic && rules.reglas.find(r=>String(r.categoria_id)===String(id));
    f.querySelector('[data-rule-choice]').hidden = !automatic;
    picker.disabled = !automatic;
    f.querySelectorAll('[data-rule-manual]').forEach(label=>{
      label.hidden=!!rule;label.querySelector('select').disabled=!!rule;
    });
    f.querySelector('[data-rule-summary]').textContent = rule
      ? `${rule.nombre} · ${rule.es_remunerado === true ? 'Remunerado' : rule.es_remunerado === false ? 'No remunerado' : 'Remuneración por definir'} · ${rule.es_falta === true ? 'Cuenta como ausencia' : rule.es_falta === false ? 'No cuenta como ausencia' : 'Ausencia por definir'}. Se aplicará la regla global v${rule.version} al guardar.`
      : automatic ? 'Elige una categoría global o define una excepción para este reporte.' : 'Excepción de este reporte; conserva la regla global.';
  }
  all('[data-rule-mode]').forEach(mode=>{
    const f=mode.closest('form');
    mode.addEventListener('change',()=>updateRule(f));
    f.querySelector('[data-rule-category]').addEventListener('change',()=>updateRule(f,true));
    f.elements.tipo_dia.addEventListener('input',()=>updateRule(f));
    updateRule(f);
  });
  root.addEventListener('submit',event=>{
    const other=all('[data-report-form]').find(f=>f!==event.target && (f.dataset.changed==='true' || f.dataset.busy==='true'));
    if(other){
      event.preventDefault();event.stopImmediatePropagation();
      feedback.textContent='Guarda o cancela la edición actual antes de guardar otra decisión.';
      const status=event.target.querySelector('.form-status');if(status)status.textContent=feedback.textContent;
      if(digitization.contains(other))digitization.open=true;
    }
  },true);

  function syncDigitizationToggle() {
    digitizationToggle.setAttribute('aria-expanded',String(digitization.open));
    digitizationToggle.textContent = digitization.open ? 'Ocultar revisión' : digitizationToggle.dataset.openLabel;
  }
  function toggleDigitization(event) {
    event.preventDefault();
    if(!digitization.open){
      if(comparisonDay)choose(comparisonDay.id);
      else if(!select.dataset.current && symbolPicker.value)choose(symbolPicker.value);
      else digitization.open=true;
    } else {
      const pending=[...digitization.querySelectorAll('[data-report-form]')].some(f=>f.dataset.changed==='true'||f.dataset.busy==='true');
      if(pending)feedback.textContent='Guarda o cancela los cambios de digitalización antes de ocultar esta sección.';
      else digitization.open=false;
    }
    syncDigitizationToggle();
  }
  digitizationToggle.hidden=false;
  digitizationToggle.addEventListener('click',toggleDigitization);
  digitization.querySelector('summary').addEventListener('click',toggleDigitization);
  digitization.addEventListener('toggle',syncDigitizationToggle);
  syncDigitizationToggle();

  function clean(f) {
    f.dataset.changed = 'false';
    f.dispatchEvent(new Event('asistia:form-clean'));
  }
  function maySwitch() {
    const unsaved = all('[data-report-form]').find(f => f.dataset.changed === 'true' || f.dataset.busy === 'true');
    if (!unsaved) return true;
    feedback.textContent = 'Guarda o cancela la edición actual antes de cambiar de caso.';
    if(digitization.contains(unsaved))digitization.open=true;
    const status=unsaved.querySelector('.form-status');if(status)status.textContent=feedback.textContent;
    [...unsaved.querySelectorAll('input:not([type=hidden]),textarea,select,button')].find(e=>!e.disabled && e.getClientRects().length)?.focus({preventScroll:true});
    return false;
  }
  function showSource(value) {
    if (!value) return;
    const url = new URL(value, location.origin);
    if (url.origin !== location.origin) return;
    // El visor muestra la hoja completa: cambiar de celda no reinicia su zoom o desplazamiento.
    ['celda','fila','columna'].forEach(key=>url.searchParams.delete(key));
    const frame = q('[data-report-source]');
    const actual = new URL(frame.src);
    const hojaVisible = frame.contentDocument?.querySelector('[data-viewer-sheet]')?.value;
    const mismaHoja = actual.origin === url.origin && actual.pathname === url.pathname && actual.hash === url.hash
      && (!url.searchParams.has('hoja') || (hojaVisible && hojaVisible === url.searchParams.get('hoja')));
    if (frame.src !== url.href && !mismaHoja) frame.src = url.href;
    q('[data-source-link]').href = url.href;
  }
  function revealCell(cell) {
    if (!cell) return;
    const scroller = q('.report-grid-scroll');
    const rect = cell.getBoundingClientRect();
    const bounds = scroller.getBoundingClientRect();
    const fixed = q('.report-grid tbody th')?.getBoundingClientRect().width || 180;
    // Desplaza solamente la tabla: mantiene la comparación y la acción en pantalla.
    scroller.scrollLeft += rect.left - bounds.left - fixed - Math.max(4, (bounds.width - fixed - rect.width) / 2);
    scroller.scrollTop += rect.top - bounds.top - 65;
  }
  function clearSelection() {
    clearDayHighlight();
    cases.forEach(e => {e.hidden = true;});
    editor.hidden = true;
    q('[data-no-case]').hidden = true;
    q('.report-case-content').scrollTop = 0;
  }
  function clearDayHighlight() {
    all('.report-selected').forEach(e=>e.classList.remove('report-selected'));
    all('[data-report-day], [data-calendar-date] button').forEach(e=>e.removeAttribute('aria-current'));
  }
  function highlightDay(cell) {
    cell.classList.add('report-selected');cell.setAttribute('aria-current','true');
    all('[data-calendar-date]').filter(e=>e.dataset.calendarDate===cell.dataset.isoDate).forEach(e=>{
      e.classList.add('report-selected');e.querySelector('button')?.setAttribute('aria-current','date');
    });
  }
  function symbolFor(cell) {
    if (!cell || ['VACIO','NO_APLICA'].includes(cell.dataset.capture)) return null;
    return cases.find(e=>e.dataset.targetCode && e.dataset.targetCode===cell.dataset.code);
  }
  function showScope(mode, article=null) {
    q('[data-edit-scope]').hidden = false;
    root.dataset.reviewScope = mode;
    all('[data-review-mode]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.reviewMode===mode)));
    q('[data-symbol-picker]').hidden = mode !== 'symbol';
    if (mode === 'symbol') {
      lastSymbol = article.dataset.reportCase;
      symbolPicker.value = lastSymbol;
    }
    q('[data-scope-description]').textContent = mode === 'symbol'
      ? `${article.dataset.targetCode ? `Significado de «${article.dataset.targetCode}»` : 'Nuevo símbolo'} para todo el reporte. Las correcciones individuales se conservan.`
      : mode === 'day' ? 'Revisión de una persona y fecha. «Por símbolo» permite volver al significado compartido.'
      : 'Puedes atender la observación o cambiar entre símbolos y días.';
  }
  function configureDay(cell, withEditor) {
    if (!cell) return;
    highlightDay(cell);
    revealCell(cell);
    showSource(cell.dataset.source);
    if (!withEditor) return;
    currentDay = cell;
    editor.hidden = false;
    q('[data-day-heading]').textContent = `${cell.dataset.person} · ${cell.dataset.date}`;
    q('[data-day-source]').textContent = `Recibido: ${cell.dataset.received} · ${cell.dataset.locator}`;
    q('[data-day-meaning]').textContent = `${cell.dataset.meaning}${cell.dataset.capture === 'DERIVADO' ? ' · Dato derivado; no es una marca escrita en la fuente.' : ''}`;
    const symbol = symbolFor(cell);
    const link = q('[data-day-symbol]');
    link.hidden = !symbol;
    link.dataset.symbol = symbol?.dataset.reportCase || '';
    link.textContent = symbol ? `Definir «${cell.dataset.code}» para todo el reporte` : '';
    if (!form) return;
    form.reset();
    clean(form);
    form.action = cell.dataset.action;
    form.elements.captura.value = cell.dataset.capture === 'DERIVADO' ? 'REGISTRADO' : cell.dataset.capture;
    const codes = [...form.elements.codigo.options];
    const code = codes.find(o => o.value === cell.dataset.code);
    form.elements.codigo.value = code?.value || '';
    q('[data-day-fields]').hidden = true;
    q('[data-day-confirm]').hidden = ['DERIVADO','ILEGIBLE','PENDIENTE'].includes(cell.dataset.capture);
    q('[data-day-correct]').hidden = false;
    q('[data-day-save]').hidden = true;
    q('[data-day-cancel]').hidden = true;
    captureChanged();
  }
  function captureChanged() {
    if (form) {
      form.elements.codigo.disabled = form.elements.captura.value !== 'REGISTRADO';
      form.elements.codigo.required = !form.elements.codigo.disabled;
    }
  }
  function choose(id, force=false) {
    if (!force && !maySwitch()) {
      if (select.dataset.current) select.value = select.dataset.current;
      return false;
    }
    if(!force)digitization.open=true;
    comparisonDay=null;
    clearSelection();
    bulkActive = id === 'vacios-grupo';
    root.classList.toggle('report-bulk-active',bulkActive);
    if (!bulkActive) all('.report-bulk-selected').forEach(e => e.classList.remove('report-bulk-selected'));
    const article = cases.find(e => e.dataset.reportCase === id);
    const cell = days.find(e => e.id === id || e.id === article?.dataset.targetDay);
    if (article) {
      article.hidden = false;
      showSource(article.dataset.source);
      if (article.dataset.targetCode) {
        const matching = days.filter(e => e.dataset.code === article.dataset.targetCode);
        matching.forEach(e => e.classList.add('report-selected'));
        revealCell(matching[0]);
      }
      if (article.dataset.personTarget) {
        const row = all('[data-person-row]').find(e => e.dataset.personRow === article.dataset.personTarget);
        row?.classList.add('report-selected');
        revealCell(row?.querySelector('[data-report-day]'));
      }
    }
    if (cell) configureDay(cell, !article || id.startsWith('dia-'));
    const isSymbol = article && (article.dataset.targetCode || id === 'codigo-nuevo');
    showScope(isSymbol ? 'symbol' : !editor.hidden || id === 'dia-seleccionar' ? 'day' : 'observation', article);
    if (!article && !cell) q('[data-no-case]').hidden = false;
    const option = [...select.options].find(o => o.value === id);
    if (option) select.value = id;
    else select.selectedIndex = -1;
    select.dataset.current = option?.value || '';
    feedback.textContent = '';
    updateBlanks();
    return true;
  }
  function updateBlanks(changed=false) {
    if (!bulkForm) return;
    const selected = blanks.filter(e => e.checked);
    const ids = new Set(selected.map(e => e.value));
    days.forEach(cell => {
      cell.classList.toggle('report-bulk-selected',bulkActive && ids.has(cell.dataset.dayId));
      if (bulkActive && cell.dataset.capture === 'VACIO') {
        cell.setAttribute('role','button');cell.setAttribute('aria-pressed',String(ids.has(cell.dataset.dayId)));
      } else {cell.removeAttribute('aria-pressed');cell.removeAttribute('role');}
    });
    const dates = new Set(selected.map(e => e.dataset.date));
    const expected = selected.filter(e => e.dataset.expected === 'true').length;
    q('[data-vacios-summary]').textContent = `${selected.length} vacíos seleccionados en ${dates.size} fechas. Se guardarán como «No correspondía asistir».${expected ? ` ${expected} tienen asistencia esperada: coteja esas fechas antes de guardar.` : ''}`;
    q('[data-vacios-save]').disabled = !selected.length;
    all('[data-vacios-group]').forEach(group => {
      const items = [...group.querySelectorAll('[data-vacios-item]')];
      const active = items.every(e => e.checked);
      const button = group.querySelector('[data-vacios-date]');
      button.setAttribute('aria-pressed',String(active));
      button.textContent = `${active ? 'Quitar' : 'Seleccionar'} ${items.length} vacíos`;
    });
    if (changed) bulkForm.dispatchEvent(new Event('input',{bubbles:true}));
  }
  function focusBlank(box) {
    const cell = days.find(e=>e.dataset.dayId === box?.value);
    if (cell) {showSource(cell.dataset.source);revealCell(cell);}
  }
  blanks.forEach(box => box.addEventListener('change',()=>{updateBlanks();focusBlank(box);}));
  blanks.forEach(box => box.addEventListener('click',event=>{if(bulkForm.dataset.busy==='true')event.preventDefault();}));
  q('[data-vacios-all]')?.addEventListener('click',()=>{blanks.forEach(e=>e.checked=true);updateBlanks(true);focusBlank(blanks[0]);});
  q('[data-vacios-none]')?.addEventListener('click',()=>{blanks.forEach(e=>e.checked=false);updateBlanks(true);});
  all('[data-vacios-date]').forEach(button=>button.addEventListener('click',()=>{
    const items = [...button.closest('[data-vacios-group]').querySelectorAll('[data-vacios-item]')];
    const active = !items.every(e=>e.checked);items.forEach(e=>e.checked=active);updateBlanks(true);focusBlank(items[0]);
  }));
  q('[data-vacios-cancel]')?.addEventListener('click',()=>{
    if (bulkForm.dataset.busy === 'true') return;
    bulkForm.reset();clean(bulkForm);bulkActive=false;updateBlanks();
    choose(select.options[0]?.value || '',true);
    q('[data-report-code="vacios-grupo"]').focus({preventScroll:true});
    feedback.textContent='Selección cancelada; no se guardaron cambios.';
  });
  function tab(name, focus=false) {
    if (!maySwitch()) return;
    all('[data-report-screen]').forEach(e => e.hidden = e.id !== name);
    all('[data-report-tab]').forEach(e => {
      const active = e.dataset.reportTab === name;
      e.setAttribute('aria-selected', String(active)); e.tabIndex = active ? 0 : -1;
      if (active && focus) e.focus({preventScroll:true});
    });
    history.replaceState(null,'',`${location.pathname}${location.search}${name === 'historial' ? '#historial' : ''}`);
  }
  q('.report-tabs').setAttribute('role','tablist');
  all('[data-report-tab]').forEach((link,index,links) => {
    link.setAttribute('role','tab'); link.id = `tab-${link.dataset.reportTab}`;
    link.setAttribute('aria-controls',link.dataset.reportTab);
    const panel = q(`#${link.dataset.reportTab}`);
    panel.setAttribute('role','tabpanel'); panel.setAttribute('aria-labelledby',link.id);
    link.addEventListener('click',event => {event.preventDefault(); tab(link.dataset.reportTab);});
    link.addEventListener('keydown',event => {
      if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? links.length-1 : (index+(event.key === 'ArrowRight' ? 1 : -1)+links.length)%links.length;
      tab(links[next].dataset.reportTab,true);
    });
  });
  // La consulta mensual permanece visible; abrir/cerrar el original no cambia la edición.
  const calendarScroll = q('[data-report-calendar-scroll]');
  const reportScroll = q('.report-grid-scroll');
  if (calendarScroll?.dataset.aligned === 'true') {
    [[reportScroll,calendarScroll],[calendarScroll,reportScroll]].forEach(([from,to])=>{
      from.addEventListener('scroll',()=>{
        if (Math.abs(to.scrollLeft-from.scrollLeft)>1) to.scrollLeft=from.scrollLeft;
      },{passive:true});
    });
  }

  root.addEventListener('asistia:report-day-changing',event=>{
    if(event.detail.initial)return;
    const pending=[...digitization.querySelectorAll('[data-report-form]')].some(f=>f.dataset.changed==='true'||f.dataset.busy==='true');
    if(pending){
      event.preventDefault();event.detail.error='Guarda o cancela la edición de digitalización antes de cambiar el día de comparación.';
      feedback.textContent=event.detail.error;
    }
  });
  root.addEventListener('asistia:report-day-selected',event=>{
    const cell=days.find(e=>e.dataset.dayId===event.detail.id);if(!cell)return;
    if(event.detail.initial&&digitization.open)return;
    if(!event.detail.initial&&digitization.open&&root.dataset.reviewScope==='day')choose(cell.id,true);
    else {
      clearDayHighlight();highlightDay(cell);
      if(!event.detail.initial){revealCell(cell);showSource(cell.dataset.source);}
    }
    if(!event.detail.initial)comparisonDay=cell;
  });
  function requestComparison(cell) {
    const request=new CustomEvent('asistia:report-day-request',{cancelable:true,detail:{id:cell.dataset.dayId}});
    const accepted=root.dispatchEvent(request);
    q('[data-day-sync-status="report"]').textContent=accepted?'':request.detail.error;
    return accepted;
  }
  days.forEach(cell => cell.addEventListener('click',event => {
    if (event.ctrlKey || event.metaKey || event.shiftKey) return;
    event.preventDefault();
    if (digitizationReviewed && !digitization.open) {
      // Comparar una declaración ya revisada no reactiva su editor ni descarta formularios.
      if(!requestComparison(cell))return;
      clearDayHighlight();
      configureDay(cell,false);comparisonDay=cell;
    } else if (bulkActive && digitization.open) {
      if (bulkForm.dataset.busy === 'true') return;
      const box = blanks.find(e=>e.value === cell.dataset.dayId);
      if (!box) {feedback.textContent='La selección en grupo solo incluye vacíos. Cancela la selección para revisar otra marca.';return;}
      box.checked=!box.checked;updateBlanks(true);showSource(cell.dataset.source);
    } else if(maySwitch()&&requestComparison(cell))choose(cell.id);
  }));
  days.forEach(cell=>cell.addEventListener('keydown',event=>{
    if(bulkActive && event.key===' '){event.preventDefault();cell.click();}
  }));
  all('[data-report-code]').forEach(link => link.addEventListener('click',event => {event.preventDefault(); choose(link.dataset.reportCode);}));
  q('[data-review-mode="symbol"]').disabled = !symbolPicker.options.length;
  q('[data-review-mode="symbol"]').addEventListener('click',()=>{
    const id = lastSymbol || symbolFor(currentDay)?.dataset.reportCase || symbolPicker.options[0]?.value;
    if (id && choose(id)) symbolPicker.focus({preventScroll:true});
  });
  q('[data-review-mode="day"]').addEventListener('click',()=>{
    if (choose(currentDay?.id || 'dia-seleccionar') && !currentDay) {
      q('.report-grid-scroll').focus({preventScroll:true});
    }
  });
  symbolPicker.addEventListener('change',()=>{
    if (!choose(symbolPicker.value)) symbolPicker.value = lastSymbol;
  });
  q('[data-day-symbol]').addEventListener('click',()=>{
    if (choose(q('[data-day-symbol]').dataset.symbol)) symbolPicker.focus({preventScroll:true});
  });
  select.addEventListener('change',() => choose(select.value));
  function step(offset, later=false) {
    if (!select.options.length) return;
    const index = (Math.max(0,select.selectedIndex)+offset+select.options.length)%select.options.length;
    if (choose(select.options[index].value) && later) feedback.textContent = 'La observación sigue pendiente. No se ha confirmado ni descartado.';
  }
  q('[data-report-prev]')?.addEventListener('click',()=>step(-1));
  q('[data-report-next]')?.addEventListener('click',()=>step(1));
  q('[data-report-later]')?.addEventListener('click',()=>step(1,true));
  all('[data-report-form]').forEach(f => {
    f.addEventListener('input',()=>{
      f.dataset.changed = 'true';
      const button = f.querySelector('[data-legend-save]');
      if (button) button.textContent = 'Guardar corrección';
    });
  });
  all('[data-report-cancel]').forEach(button => button.addEventListener('click',()=>{
    const f = button.closest('form');
    f.reset(); clean(f); feedback.textContent = 'Edición cancelada; no se guardaron cambios.';
    updateRule(f);
    f.querySelector('[data-legend-save]').textContent = 'Confirmar clasificación';
  }));
  q('[data-day-correct]')?.addEventListener('click',()=>{
    q('[data-day-fields]').hidden = false;
    q('[data-day-confirm]').hidden = true;
    q('[data-day-correct]').hidden = true;
    q('[data-day-save]').hidden = false;
    q('[data-day-cancel]').hidden = false;
    form.elements.captura.focus({preventScroll:true});
  });
  q('[data-day-cancel]')?.addEventListener('click',()=>{
    configureDay(currentDay,true); feedback.textContent = 'Edición cancelada; no se guardaron cambios.';
    q('[data-day-correct]').focus({preventScroll:true});
  });
  form?.elements.captura.addEventListener('change',captureChanged);
  // Historial conserva el acceso a la aprobación completa, sin exigirla en la comparación.
  const initial = location.hash === '#historial' ? 'historial' : 'comparar';
  const returnToSection = ['#report-calendar-heading','#coherencia','#observaciones'].includes(location.hash) ? location.hash : null;
  const codeHash = location.hash.startsWith('#codigo-') ? location.hash.slice(1) : null;
  if(codeHash || location.hash==='#observaciones')digitization.open=true;
  choose(codeHash || select.value, true); tab(initial);
  if (returnToSection) {
    history.replaceState(null,'',`${location.pathname}${location.search}${returnToSection}`);
    requestAnimationFrame(()=>q(returnToSection)?.scrollIntoView({block:'start'}));
  }
})();
