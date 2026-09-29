// Matriz anual: la consulta no escribe; los cambios de días se envían juntos al guardar.
(() => {
  const form = document.getElementById('calendar-form');
  if (!form) return;
  const panel = form.closest('.calendar-annual-editor');
  const q = s => form.querySelector(s);
  const cells = [...form.querySelectorAll('[data-fecha]')];
  const inputs = [...form.querySelectorAll('[data-day-input]')];
  const selected = new Set();
  let active = null;
  form.classList.add('calendar-ready');
  form.querySelectorAll('.calendar-enhanced').forEach(e => e.hidden=false);
  const changed = () => inputs.filter(e=>e.value!==e.dataset.original);
  const editedSymbol = () => panel.querySelector('[data-calendar-symbol-form][data-changed=true]');
  const multiple = q('[data-multiple-days]');
  const picker = q('#calendar-active-category');
  function status() {
    if (q('[data-selection-status]')) q('[data-selection-status]').textContent=`${selected.size} días seleccionados`;
    if (q('[data-apply-category]')) q('[data-apply-category]').textContent=selected.size?`Aplicar a ${selected.size} días`:'Aplicar al día';
    const n=changed().length;
    if(q('[data-change-status]'))q('[data-change-status]').textContent=n?`${n} días con cambios sin guardar en el año.`:'Sin cambios por guardar.';
    if(!n && !q('[name=motivo]')?.value)form.dispatchEvent(new Event('asistia:form-clean'));
    cells.forEach(c=>c.classList.toggle('calendar-selected',selected.has(c)));
  }
  function open(cell) {
    active=cell;
    cells.forEach(c=>c.querySelector('[data-open-day]').setAttribute('aria-pressed',String(c===cell)));
    const [year,month,day]=cell.dataset.fecha.split('-');
    q('[data-active-title]').textContent=`${day}/${month}/${year}`;
    const input=cell.querySelector('[data-day-input]');
    q('[data-active-state]').textContent=input&&input.value!==input.dataset.original?`${input.selectedOptions[0].dataset.label} · Cambio sin guardar`:cell.dataset.originalLabel;
    q('[data-active-detail]').textContent=cell.dataset.detail;
    if(picker)picker.value=input.value;
  }
  function totals() {
    const total={};
    form.querySelectorAll('[data-calendar-month]').forEach(row=>{
      const counts={};row.querySelectorAll('[data-fecha]').forEach(c=>{const g=c.dataset.group;counts[g]=(counts[g]||0)+1;total[g]=(total[g]||0)+1;});
      row.querySelectorAll('[data-total-group]').forEach(c=>c.textContent=counts[c.dataset.totalGroup]||0);
    });
    form.querySelectorAll('tfoot [data-total-group]').forEach(c=>c.textContent=total[c.dataset.totalGroup]||0);
  }
  function update(input) {
    const cell=input.closest('[data-fecha]'), edited=input.value!==input.dataset.original, option=input.selectedOptions[0];
    cell.dataset.group=edited?option.dataset.group||'':cell.dataset.originalGroup;
    cell.dataset.pending=edited?String(input.value==='__SIN_ASIGNAR__'):cell.dataset.originalPending;
    cell.classList.toggle('calendar-edited',edited);
    cell.querySelector('[data-day-symbol]').textContent=edited?option.dataset.symbol:cell.dataset.originalSymbol;
    cell.querySelector('.calendar-edit-mark').textContent=edited?'*':'';
    if(cell.querySelector('[data-day-warning]'))cell.querySelector('[data-day-warning]').hidden=edited;
    cell.querySelector('[data-open-day]').setAttribute('aria-label',edited?`${cell.dataset.fecha}: ${option.dataset.label}. Cambio sin guardar`:cell.dataset.detail);
    if(active===cell)open(cell);
  }
  form.querySelectorAll('[data-open-day]').forEach(button=>{
    button.addEventListener('click',()=>{
      if(form.dataset.busy==='true')return;
      const cell=button.closest('[data-fecha]');open(cell);
      if(multiple?.checked){selected.has(cell)?selected.delete(cell):selected.add(cell);status();}
    });
    button.addEventListener('keydown',event=>{
      const moves={ArrowRight:1,ArrowLeft:-1,ArrowDown:31,ArrowUp:-31};
      if(!(event.key in moves))return;
      event.preventDefault();const i=cells.indexOf(button.closest('[data-fecha]'));
      cells[Math.max(0,Math.min(cells.length-1,i+moves[event.key]))].querySelector('[data-open-day]').focus();
    });
  });
  inputs.forEach(input=>input.addEventListener('change',()=>{update(input);totals();status();}));
  [multiple,picker].filter(Boolean).forEach(e=>e.addEventListener('input',event=>event.stopPropagation()));
  multiple?.addEventListener('change',()=>{if(!multiple.checked)selected.clear();status();});
  q('[data-clear-selection]')?.addEventListener('click',()=>{selected.clear();multiple.checked=false;status();});
  function select(list){if(form.dataset.busy==='true')return;multiple.checked=true;list.forEach(c=>selected.add(c));if(list[0])open(list[0]);status();}
  form.querySelectorAll('[data-select-month]').forEach(b=>b.addEventListener('click',()=>select([...b.closest('tr').querySelectorAll('[data-fecha]')])));
  q('[data-select-pending]')?.addEventListener('click',()=>select(cells.filter(c=>c.dataset.pending==='true')));
  q('[data-apply-category]')?.addEventListener('click',()=>{
    const feedback=q('[data-edit-feedback]');
    if(editedSymbol()){feedback.textContent='Guarda o cancela el significado que estás editando antes de cambiar días.';return;}
    const targets=selected.size?[...selected]:active?[active]:[];
    if(!targets.length||!picker.value){feedback.textContent='Selecciona al menos un día y una categoría.';return;}
    const value=picker.value;
    targets.forEach(cell=>{const input=cell.querySelector('[data-day-input]');input.value=value;update(input);});
    form.dispatchEvent(new Event('input',{bubbles:true}));totals();status();
    feedback.textContent=`Categoría asignada a ${targets.length} días. Guarda los cambios al terminar.`;
  });
  q('[data-revert-days]')?.addEventListener('click',()=>{inputs.forEach(input=>{input.value=input.dataset.original;update(input);});totals();status();q('[data-edit-feedback]').textContent='Se restablecieron los días guardados.';});
  // Enviar únicamente decisiones modificadas, no volver a asignar todo el año.
  form.addEventListener('formdata',event=>inputs.filter(e=>e.value===e.dataset.original).forEach(e=>event.formData.delete(e.name)));
  panel.querySelectorAll('form[data-async]').forEach(other=>other.addEventListener('submit',event=>{
    const symbol=editedSymbol();
    const blocked=(other!==form&&changed().length)||(symbol&&symbol!==other);
    if(blocked || (other===form&&!changed().length)){
      event.preventDefault();event.stopImmediatePropagation();
      other.querySelector('.form-status').textContent=blocked?'Guarda o cancela los cambios pendientes antes de continuar.':'Cambia al menos un día antes de guardar.';
    }
  },true));
  new MutationObserver(()=>{
    const busy=form.dataset.busy==='true';
    form.querySelectorAll('input,select,button').forEach(e=>{if(!e.matches('[type=hidden]'))e.disabled=busy;});
  }).observe(form,{attributes:true,attributeFilter:['data-busy']});
  // Símbolos: equivalencias del catálogo, con excepción explícita cuando se necesita.
  const rules=JSON.parse(document.getElementById('calendar-rules').textContent);
  const normal=v=>v.normalize('NFD').replace(/[\u0300-\u036f]/g,'').trim().replace(/\s+/g,' ').toUpperCase();
  panel.querySelectorAll('[data-calendar-symbol-form]').forEach(f=>{
    function refresh(chooseName=false){
      const automatic=f.elements.modo_regla.value==='auto';
      const id=f.elements.categoria_global.value || rules.equivalencias[normal(f.elements.tipo_dia.value)];
      const rule=automatic&&rules.reglas.find(r=>String(r.categoria_id)===String(id));
      if(chooseName&&rule)f.elements.tipo_dia.value=rule.nombre;
      f.querySelector('[data-calendar-rule-choice]').hidden=!automatic;f.elements.categoria_global.disabled=!automatic;
      f.querySelector('[data-calendar-manual]').hidden=!!rule;
      f.querySelectorAll('[data-calendar-manual] select').forEach(e=>e.disabled=!!rule);
      f.querySelector('[data-calendar-rule-summary]').textContent=rule?`${rule.nombre} · ${{LECTIVO:'Lectivo',GESTION:'Gestión',NO_LECTIVO_NI_GESTION:'No lectivo ni de gestión'}[rule.grupo_actividad]||'Actividad por definir'} · ${rule.es_remunerado===true?'Remunerado':rule.es_remunerado===false?'No remunerado':'Remuneración por definir'}. Se aplicará la regla global v${rule.version}.`:automatic?'No hay una equivalencia para este significado. Elige una regla global o define una excepción.':'Excepción solo de este calendario.';
    }
    f.addEventListener('input',()=>f.dataset.changed='true');
    f.elements.modo_regla.addEventListener('change',()=>refresh());
    f.elements.categoria_global.addEventListener('change',()=>refresh(true));
    f.elements.tipo_dia.addEventListener('input',()=>refresh());
    f.querySelector('[data-calendar-symbol-cancel]').addEventListener('click',()=>{f.reset();f.dataset.changed='false';f.dispatchEvent(new Event('asistia:form-clean'));refresh();f.closest('details[data-calendar-symbol]').open=false;});
    refresh();
  });
  status();
})();
