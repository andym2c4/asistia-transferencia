(() => {
  const form=document.querySelector('[data-attendance-adjust-form]'); if(!form)return;
  const root=document.getElementById('report-review');
  const panel=form.closest('[data-coherence-adjustment]'), q=s=>form.querySelector(s);
  const day=q('[data-adjust-day]'), person=q('[data-adjust-person]'), value=q('[data-adjust-value]');
  const grid=q('[data-comparison-grid]'), days=q('[data-comparison-days]'), save=q('[data-adjust-save]');
  const status=q('.form-status'), result=q('[data-comparison-result]'), reasons=q('[data-comparison-reasons]');
  const retry=q('[data-comparison-retry]'), reload=q('[data-comparison-reload]');
  let selected='', sequence=0, controller=null, ready=null;
  const options=[...day.options].filter(o=>o.value);
  for(const option of person.options){if(!options.some(o=>o.dataset.person===option.value)){option.disabled=true;option.textContent+=' · Sin datos diarios';}}
  const set=(name,text)=>{q(`[data-comparison-${name}]`).textContent=text;};
  const date=s=>new Date(`${s}T12:00:00`).toLocaleDateString('es-PE',{weekday:'long',day:'numeric',month:'long',year:'numeric'});
  const pay=v=>v===true?'Remunerado':v===false?'No remunerado':'Remuneración por determinar';
  const absence=v=>v===true?'Cuenta como falta':v===false?'No cuenta como falta':'Falta por determinar';
  const blocked=()=>form.dataset.changed==='true'||form.dataset.busy==='true';
  function clean(){form.dataset.changed='false';form.dispatchEvent(new Event('asistia:form-clean'));}
  function protect(){status.textContent='Guarda o cancela el cambio del día seleccionado antes de elegir otro.';panel.open=true;}
  function renderDays(data){
    const focused=days.contains(document.activeElement)?document.activeElement.dataset.comparisonDay:null;
    days.replaceChildren();days.dataset.person=person.value;
    for(const d of data.mes){
      const current=d.id===selected, preview=current&&data.propuesta;
      const state=preview?data.cruce.estado:d.estado, code=preview?data.final.codigo:d.codigo;
      const button=document.createElement('button');button.type='button';button.dataset.comparisonDay=d.id;button.dataset.state=state;
      const calendarDate=new Date(`${d.fecha}T12:00:00`), weekdayIndex=(calendarDate.getDay()+6)%7;
      const monthStart=(new Date(calendarDate.getFullYear(),calendarDate.getMonth(),1).getDay()+6)%7;
      button.style.gridColumn=String(weekdayIndex+1);
      button.style.gridRow=String(Math.floor((calendarDate.getDate()+monthStart-1)/7)+1);
      button.classList.toggle('is-preview',preview);button.setAttribute('aria-pressed',String(current));
      const flag=state==='compatible'?'✓':state==='alerta'?'!':'?';
      const num=document.createElement('span');num.textContent=d.fecha.slice(8);
      const weekday=document.createElement('small');weekday.className='comparison-weekday';
      weekday.textContent=['Do','Lu','Ma','Mi','Ju','Vi','Sá'][new Date(`${d.fecha}T12:00:00`).getDay()];
      const mark=document.createElement('strong');mark.textContent=code;
      const icon=document.createElement('small');icon.textContent=`${flag}${d.ajustado?' •':''}`;
      num.append(' ',weekday);button.append(num,mark,icon);
      button.setAttribute('aria-label',`${date(d.fecha)} · ${code} · ${preview?data.final.significado:d.significado} · ${state==='compatible'?'Sin diferencias':state==='alerta'?'Por revisar':'Información pendiente'}${preview?' · Vista previa sin guardar':''}${d.ajustado?' · Con ajuste de RRHH':''}`);
      button.addEventListener('click',()=>select(d.id));days.append(button);
    }
    if(focused)days.querySelector(`[data-comparison-day="${focused}"]`)?.focus({preventScroll:true});
  }
  function render(data){
    ready=data;grid.setAttribute('aria-busy','false');grid.classList.remove('is-loading');
    set('title',date(data.fecha));set('state',data.propuesta?'Vista previa · sin guardar':data.guardado.ajustado?'Resultado guardado':'Declaración sin ajuste');
    q('[data-comparison-state]').className=`badge ${data.propuesta?'warn':''}`;
    set('calendar-code',data.calendario.codigo);set('calendar-name',data.calendario.significado);
    set('calendar-detail',`${data.calendario.actividad===true?'Actividad programada':data.calendario.actividad===false?'Sin actividad programada':'Actividad por determinar'} · ${pay(data.calendario.remunerado)}`);
    set('calendar-version',`Calendario ${data.calendario.version?'v'+data.calendario.version+' · ':''}${data.calendario.estado}`);
    set('declared-code',data.declarado.codigo);set('declared-name',data.declarado.significado);
    set('declared-detail',`${absence(data.declarado.falta)} · ${pay(data.declarado.remunerado)}`);
    set('final-code',data.final.codigo);set('final-name',data.final.significado);
    set('final-detail',`${absence(data.final.falta)} · ${pay(data.final.remunerado)}`);
    set('saved',data.propuesta?`Actualmente guardado: ${data.guardado.marca.codigo} · ${data.guardado.marca.significado}`:data.guardado.ajustado?`${data.guardado.vigente?'Ajuste de RRHH guardado':'Ajuste anterior pendiente de cotejo'}${data.guardado.autor?' · '+data.guardado.autor:''}`:'Se usará la declaración digitalizada.');
    result.dataset.state=data.cruce.estado;
    let title=data.cruce.estado==='compatible'?'Sin diferencias con el calendario':data.cruce.estado==='alerta'?'El cruce mantiene observaciones':'Falta información para comprobar el cruce';
    if(!data.propuesta&&data.cruce.caso_resuelto&&data.cruce.estado!=='compatible')title='Caso verificado por RRHH · diferencias conservadas';
    if(!data.propuesta&&data.cruce.decision==='PENDIENTE')title+=' · Revisión dejada pendiente';
    set('result-title',title);reasons.replaceChildren();
    const messages=data.cruce.motivos.length?data.cruce.motivos:[data.final.motivo];
    for(const text of messages){const li=document.createElement('li');li.textContent=text;reasons.append(li);}
    const detail=value.selectedOptions[0]?.dataset.detail;
    q('[data-adjust-context]').textContent=detail||'Elige otra categoría para previsualizar su efecto. Solo se aplica al guardar.';
    value.querySelector('[value="RESTAURAR"]').disabled=!data.guardado.ajustado;
    save.disabled=!data.propuesta;renderDays(data);
  }
  async function refresh(){
    const seq=++sequence;controller?.abort();controller=new AbortController();const requestController=controller;ready=null;
    save.disabled=true;retry.hidden=true;reload.hidden=true;
    if(days.dataset.person!==person.value)days.querySelectorAll('button').forEach(b=>b.disabled=true);
    grid.setAttribute('aria-busy','true');grid.classList.add('is-loading');
    result.dataset.state='loading';set('state',value.value?'Vista previa · calculando':'Consultando');set('result-title','Calculando el cruce del día…');reasons.replaceChildren();
    for(const area of ['calendar','declared','final']){set(area+'-code','…');set(area+'-name','Consultando…');set(area+'-detail','');}
    set('calendar-version','');set('saved','');
    if(!selected){set('result-title','Este reporte no tiene días disponibles para comparar.');return;}
    const current=options.find(o=>o.value===selected);set('title',date(current.dataset.date));
    const url=new URL(form.dataset.previewUrl,location.origin);
    for(const name of ['huella','huella_cruce','huella_opciones'])url.searchParams.set(name,form.elements[name].value);
    url.searchParams.set('dia',selected);if(value.value)url.searchParams.set('valor',value.value);
    const timer=setTimeout(()=>requestController.abort(),15000);
    try{
      const response=await fetch(url,{credentials:'same-origin',cache:'no-store',signal:requestController.signal});
      if(seq!==sequence)return;
      if(response.redirected||!response.headers.get('content-type')?.includes('application/json'))throw new Error('La sesión venció o la comparación no está disponible. Recarga para continuar.');
      const data=await response.json();if(seq!==sequence)return;
      if(!response.ok){reload.hidden=response.status!==409;throw new Error(data.error||'No se pudo comprobar el día.');}
      if(data.dia!==selected||data.valor!==value.value)return;
      render(data);
    }catch(error){
      if(seq!==sequence)return;
      grid.setAttribute('aria-busy','false');set('state','Comparación no disponible');result.dataset.state='pendiente';
      set('result-title',error.message==='Failed to fetch'||error.name==='AbortError'?'No pudimos cargar la comparación. Tu selección se conserva; puedes reintentar.':error.message);
      retry.hidden=false;save.disabled=true;
    }finally{clearTimeout(timer);}
  }
  function clearSyncStatus(){root.querySelectorAll('[data-day-sync-status]').forEach(e=>e.textContent='');}
  function select(id,scroll=false,initial=false){
    const option=options.find(o=>o.value===id);if(!option)return false;
    const restore=()=>{day.value=selected;person.value=options.find(o=>o.value===selected)?.dataset.person;};
    if(blocked()&&id!==selected){restore();protect();return false;}
    const change=new CustomEvent('asistia:report-day-changing',{cancelable:true,detail:{id,initial}});
    if(!root.dispatchEvent(change)){restore();status.textContent=change.detail.error;return false;}
    panel.open=true;
    if(id!==selected){selected=id;day.value=id;person.value=option.dataset.person;value.value='';form.elements.motivo.value='';clean();status.textContent='';refresh();}
    days.querySelectorAll('[data-comparison-day]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.comparisonDay===selected)));
    clearSyncStatus();
    root.dispatchEvent(new CustomEvent('asistia:report-day-selected',{detail:{id,initial}}));
    if(scroll){panel.querySelector('[data-coherence-workspace]').scrollIntoView({block:'start'});value.focus({preventScroll:true});}
    return true;
  }
  root.addEventListener('asistia:report-day-request',event=>{
    if(!select(event.detail.id)){event.detail.error=status.textContent;event.preventDefault();}
  });
  root.querySelectorAll('[data-report-calendar-scroll] [data-calendar-date] button').forEach(button=>button.addEventListener('click',()=>{
    const date=button.closest('[data-calendar-date]').dataset.calendarDate;
    const option=options.find(o=>o.dataset.person===person.value&&o.dataset.date===date);
    const message=root.querySelector('[data-day-sync-status="calendar"]');
    if(!option){message.textContent='La persona seleccionada no tiene un dato digitalizado en esta fecha. Se conserva el día anterior.';return;}
    if(!select(option.value))message.textContent=status.textContent;
  }));
  day.addEventListener('input',e=>e.stopPropagation());person.addEventListener('input',e=>e.stopPropagation());
  day.addEventListener('change',()=>select(day.value));
  person.addEventListener('change',()=>{
    const date=options.find(o=>o.value===selected)?.dataset.date;
    select((options.find(o=>o.dataset.person===person.value&&o.dataset.date===date)||options.find(o=>o.dataset.person===person.value))?.value);
  });
  value.addEventListener('change',()=>{form.dataset.changed=!!value.value||!!form.elements.motivo.value?'true':'false';if(form.dataset.changed==='false')clean();refresh();});
  q('[data-adjust-cancel]').addEventListener('click',()=>{if(form.dataset.busy==='true')return;value.value='';form.elements.motivo.value='';clean();clearSyncStatus();status.textContent='Cambio cancelado. Se muestra el valor guardado.';refresh();});
  panel.querySelector('summary').addEventListener('click',event=>{if(panel.open&&blocked()){event.preventDefault();status.textContent='Guarda o cancela el cambio antes de ocultar la comparación.';}});
  document.querySelectorAll('[data-coherence-adjust-day]').forEach(button=>button.addEventListener('click',()=>select(button.dataset.coherenceAdjustDay,true)));
  retry.addEventListener('click',refresh);
  form.addEventListener('submit',event=>{if(!ready||!ready.propuesta||ready.dia!==selected||ready.valor!==value.value){event.preventDefault();event.stopImmediatePropagation();status.textContent='Espera la comparación del día antes de guardar. Si falló, pulsa Reintentar comparación.';}},true);
  form.querySelectorAll('[data-comparison-enhancement]').forEach(e=>e.hidden=false);form.classList.add('coherence-ready');
  const requested=new URLSearchParams(location.search).get('dia_coherencia');
  const initial=options.find(o=>o.value===requested)?.value||document.querySelector('[data-coherence-case][data-resolved="false"] [data-coherence-adjust-day]')?.dataset.coherenceAdjustDay||options[0]?.value;
  if(!initial){
    save.disabled=true;person.disabled=true;value.disabled=true;grid.hidden=true;
    set('state','Sin datos diarios');set('result-title','Este reporte no tiene días digitalizados disponibles para comparar.');
    return;
  }
  select(initial,false,true);
})();
