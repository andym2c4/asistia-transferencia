// Cola local por archivo: el servidor conserva originales/resultados; no guarda archivos pendientes del navegador.
(() => {
  const form = document.querySelector('[data-report-upload]');
  if (!form) return;
  const input = form.querySelector('[type=file]'), list = form.querySelector('[data-upload-list]');
  const queueBox = form.querySelector('[data-upload-queue]'), start = form.querySelector('[data-upload-start]');
  const pause = form.querySelector('[data-upload-pause]'), status = form.querySelector('[data-upload-status]');
  const login = form.querySelector('[data-upload-login]'), refreshButton = form.querySelector('[data-upload-refresh]');
  const received = form.querySelector('[name=recibido_real_en]');
  const entries = [];
  let running = false, pauseRequested = false;
  input.required = false;
  form.querySelector('[data-queue-help]').textContent = 'Puedes añadir varios archivos a la cola. Se procesarán uno por uno; mantén esta ventana abierta hasta terminar.';
  const pending = () => entries.some(e => e.state === 'pending');
  const receivedCount = () => entries.filter(e => e.state === 'completed').length;
  function link(label, href) {
    const url = new URL(href, location.origin);
    if (url.origin !== location.origin) throw new Error('Destino no permitido');
    const a = document.createElement('a'); a.textContent = label+' ↗'; a.href = url.href; a.target = '_blank'; a.rel = 'noopener'; return a;
  }
  function render() {
    queueBox.hidden = !entries.length;
    list.replaceChildren();
    entries.forEach(entry => {
      const row = document.createElement('li'), main = document.createElement('div'), heading = document.createElement('div');
      const name = document.createElement('strong'), badge = document.createElement('span'), detail = document.createElement('p');
      row.dataset.uploadItem = entry.id; row.dataset.state = entry.state;
      name.textContent = entry.file.name; badge.className = 'badge';
      badge.textContent = entry.result ? `${entry.result.duplicado?'Ya recibido · ':''}${entry.result.titulo}` : ({pending:'En cola',processing:'Procesando…',error:'Sin confirmar',invalid:'No enviado'})[entry.state];
      if (['invalid','error'].includes(entry.state) || (entry.result && entry.result.estado !== 'PROCESADO')) badge.classList.add('warn');
      heading.className = 'upload-item-heading'; heading.append(name,badge); main.append(heading);
      detail.className = 'small muted'; detail.textContent = entry.message || `${(entry.file.size/1024/1024).toLocaleString('es',{maximumFractionDigits:2})} MB`; main.append(detail);
      const actions = document.createElement('div'); actions.className = 'upload-item-actions';
      if (entry.result) {
        actions.append(link(entry.result.continuar_label,entry.result.continuar_url));
        if (entry.result.continuar_url !== entry.result.url) actions.append(link('Ver resultado',entry.result.url));
        const reports = document.createElement('ul'); reports.className = 'upload-report-links';
        entry.result.reportes.forEach(report => {const li=document.createElement('li');li.append(link('Abrir revisión · '+report.etiqueta,report.url));reports.append(li);});
        main.append(reports);
      } else if (entry.state !== 'processing') {
        if (entry.state === 'error') {
          const retry = document.createElement('button'); retry.type='button';retry.textContent='Reintentar';retry.disabled=running;
          retry.addEventListener('click',()=>{entry.state='pending';entry.message='';render();processQueue();});actions.append(retry);
        }
        const remove = document.createElement('button');remove.type='button';remove.textContent='Quitar';remove.className='link-button';
        remove.addEventListener('click',()=>{entries.splice(entries.indexOf(entry),1);render();input.focus({preventScroll:true});});actions.append(remove);
      }
      row.append(main,actions); list.append(row);
    });
    start.disabled = running || !pending(); start.textContent = receivedCount() ? 'Continuar cola' : 'Cargar y procesar';
    pause.hidden = !running; pause.disabled = pauseRequested; pause.textContent = pauseRequested ? 'Se pausará al terminar este archivo' : 'Pausar después de este archivo';
    received.disabled = running;
    form.querySelector('[data-upload-counter]').textContent = entries.length ? `${receivedCount()} de ${entries.length} archivos recibidos` : '';
    form.setAttribute('aria-busy',String(running));
  }
  input.addEventListener('change',()=>{
    for (const file of input.files) {
      const message = !file.size ? 'El archivo está vacío. Selecciona el original.' : file.size>20*1024*1024 ? 'Supera 20 MB. Selecciona un archivo más pequeño.' : '';
      entries.push({file,id:crypto.randomUUID(),state:message?'invalid':'pending',message,meta:null,result:null});
    }
    input.value=''; render();
  });
  pause.addEventListener('click',()=>{pauseRequested=true;render();});
  async function refreshDirectory() {
    try {
      const url = new URL('/revision',location.origin);
      url.searchParams.set('periodo',form.elements.periodo.value);url.searchParams.set('nivel',form.elements.nivel.value);
      const response = await fetch(url,{credentials:'same-origin',cache:'no-store'});
      if (!response.ok || response.redirected) throw new Error();
      const doc = new DOMParser().parseFromString(await response.text(),'text/html');
      const root = doc.querySelector('#revision-directory'), recent = doc.querySelector('[data-upload-recent]');
      if (!root || !recent) throw new Error();
      document.querySelector('#revision-directory').replaceWith(root);
      const current = document.querySelector('[data-upload-recent]'), open = current.querySelector('details')?.open;
      current.replaceWith(recent); if (open && recent.querySelector('details')) recent.querySelector('details').open=true;
      document.dispatchEvent(new CustomEvent('asistia:revision-actualizada'));
      refreshButton.hidden=true;
    } catch {
      refreshButton.hidden=false;
      status.textContent += ' La lista de instituciones no pudo actualizarse; los resultados de la cola se conservan aquí.';
    }
  }
  refreshButton.addEventListener('click',refreshDirectory);
  async function processQueue() {
    if (running || !pending()) return;
    if (!form.reportValidity()) return;
    running=true;pauseRequested=false;login.hidden=true;let interrupted=false;
    render();
    while (pending() && !pauseRequested) {
      const entry = entries.find(e=>e.state==='pending'); entry.state='processing';entry.message='Conservando el original e identificando su contenido…';
      if (!entry.meta) entry.meta={periodo:form.elements.periodo.value,nivel:form.elements.nivel.value,recibido_real_en:received.value};
      status.textContent=`Procesando ${entry.file.name}. ${receivedCount()} archivos recibidos.`;render();
      try {
        const session = await fetch('/sesion',{credentials:'same-origin',cache:'no-store'});
        if (session.redirected && new URL(session.url).pathname==='/ingresar') {
          login.hidden=false;throw new Error('La sesión venció. Inicia sesión en otra pestaña y reintenta; los archivos por enviar siguen en esta cola.');
        }
        if (!session.ok || session.redirected) throw new Error('No se pudo comprobar la sesión. Reintenta cuando vuelva la conexión.');
        const csrf = (await session.json()).csrf;
        const body = new FormData();body.set('csrf',csrf);body.set('operacion',entry.id);body.set('tipo','asistencia');
        Object.entries(entry.meta).forEach(([key,value])=>body.set(key,value));body.set('archivos',entry.file,entry.file.name);
        const response = await fetch(form.action,{method:'POST',body,credentials:'same-origin',headers:{Accept:'application/json','X-ASISTIA-Async':'1'}});
        const json = response.headers.get('Content-Type')?.includes('application/json');
        const result = json ? await response.json() : null;
        if (result?.redirect?.startsWith('/ingresar') || (response.redirected && new URL(response.url).pathname==='/ingresar')) {
          login.hidden=false;throw new Error('La sesión venció antes de recibir el archivo. Inicia sesión y reintenta.');
        }
        if (!response.ok || !result?.carga_id) {
          if ([400,413].includes(response.status)) entry.meta=null;
          let message = result?.error;
          if (!json) message=new DOMParser().parseFromString(await response.text(),'text/html').querySelector('[data-error]')?.textContent;
          throw new Error(message || 'No pudimos confirmar el resultado. Reintenta este archivo; se comprobará su recepción anterior.');
        }
        entry.result=result;entry.state='completed';entry.message=result.mensaje;
      } catch (error) {
        entry.state='error';entry.message=(error instanceof TypeError || error instanceof SyntaxError)?'La conexión se interrumpió. Reintenta este archivo para comprobar si ya fue recibido.':error.message;
        status.textContent=entry.message;interrupted=true;render();break;
      }
      render();
    }
    if (!interrupted) status.textContent=pauseRequested && pending() ? 'Cola pausada. El archivo en curso terminó; puedes continuar con los restantes.' : `Cola finalizada: ${receivedCount()} archivos recibidos${entries.some(e=>['error','invalid'].includes(e.state))?' · quedan archivos por atender':''}. Consulta los resultados de cada archivo.`;
    if (receivedCount()) await refreshDirectory();
    running=false;render();
  }
  form.addEventListener('submit',event=>{event.preventDefault();processQueue();});
  window.addEventListener('beforeunload',event=>{
    if (entries.some(e=>['pending','processing','error'].includes(e.state))) {event.preventDefault();event.returnValue='';}
  });
  render();
})();
