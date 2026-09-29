(() => {
  const panel=document.getElementById('coherencia'); if(!panel)return;
  const rows=[...panel.querySelectorAll('[data-coherence-case]')];
  const form=panel.querySelector('[data-coherence-form]');
  const rule=panel.querySelector('[data-coherence-rule]');
  const state=panel.querySelector('[data-coherence-state]');
  const all=panel.querySelector('[data-coherence-all]');
  const boxes=[...panel.querySelectorAll('[name=casos]')];
  function update(){
    rows.forEach(row=>row.hidden=!!((rule.value&&!row.dataset.rules.split(' ').includes(rule.value)) || (state.value==='pendientes'&&row.dataset.resolved==='true') || (state.value==='verificados'&&row.dataset.resolved!=='true')));
    const visible=rows.filter(r=>!r.hidden), selected=boxes.filter(b=>b.checked);
    panel.querySelector('[data-coherence-count]').textContent=`${visible.length} casos visibles`;
    panel.querySelector('[data-coherence-empty]').hidden=!!visible.length;
    if(form){
      const vb=boxes.filter(b=>!b.closest('tr').hidden);
      all.checked=!!vb.length&&vb.every(b=>b.checked);all.indeterminate=vb.some(b=>b.checked)&&!all.checked;
      panel.querySelector('[data-coherence-selection]').textContent=`${selected.length} casos seleccionados${selected.some(b=>b.closest('tr').hidden)?' (incluye casos fuera del filtro)':''}.`;
    }
  }
  if(rule){rule.addEventListener('change',update);state.addEventListener('change',update);update();}
  boxes.forEach(b=>{b.addEventListener('input',e=>e.stopPropagation());b.addEventListener('change',update);});
  all?.addEventListener('input',e=>e.stopPropagation());
  all?.addEventListener('change',()=>{boxes.filter(b=>!b.closest('tr').hidden).forEach(b=>b.checked=all.checked);update();});
  panel.querySelector('[data-coherence-cancel]')?.addEventListener('click',()=>{
    if(form.dataset.busy==='true')return;
    form.reset();form.dataset.changed='false';form.dispatchEvent(new Event('asistia:form-clean'));update();form.querySelector('.form-status').textContent='Selección cancelada. Las fuentes no cambiaron.';
  });
  form?.addEventListener('submit',e=>{
    if(!boxes.some(b=>b.checked)){e.preventDefault();e.stopImmediatePropagation();form.querySelector('.form-status').textContent='Selecciona al menos un caso.';}
  },true);
  panel.querySelectorAll('[data-coherence-day]').forEach(link=>link.addEventListener('click',e=>{
    const day=document.querySelector(`[data-day-id="${link.dataset.coherenceDay}"]`);if(!day)return;
    e.preventDefault();day.click();day.scrollIntoView({block:'center',inline:'center'});day.focus({preventScroll:true});
  }));
  document.querySelectorAll('a[href="#coherencia"]').forEach(link=>link.addEventListener('click',e=>{
    e.preventDefault();document.querySelector('[data-report-tab="comparar"]')?.click();
    if(document.querySelector('#comparar').hidden)return;
    history.replaceState(null,'',`${location.pathname}${location.search}#coherencia`);
    panel.scrollIntoView({block:'start'});
  }));
  if(location.hash==='#coherencia')requestAnimationFrame(()=>panel.scrollIntoView({block:'start'}));
})();
