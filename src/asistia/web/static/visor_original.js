(() => {
  const root = document.querySelector('[data-original-viewer]');
  if (!root) return;
  const q = s => root.querySelector(s);
  const image = q('[data-viewer-image]'), viewport = q('[data-viewer-scroll]');
  const sheets = q('[data-viewer-sheet]'), pages = q('[data-viewer-page]');
  const zoom = q('[data-viewer-zoom]'), status = q('[data-viewer-status]');
  let angle = 0;
  const stage = q('[data-viewer-stage]');
  let info, sequence = 0, activeURL = '', imageURL = null;
  const requested = new URL(location.href).searchParams;
  let sheet = requested.get('hoja') || '';
  let page = Math.max(1, Number(new URLSearchParams(location.hash.slice(1)).get('page')) || 1);
  function size() {
    if (!image.naturalWidth) return;
    const swapped = Math.abs(angle % 180) === 90;
    const width = swapped ? image.naturalHeight : image.naturalWidth;
    const height = swapped ? image.naturalWidth : image.naturalHeight;
    const scale = zoom.value === 'fit' ? viewport.clientWidth / width : Number(zoom.value);
    stage.style.width = `${width * scale}px`; stage.style.height = `${height * scale}px`;
    image.style.width = `${image.naturalWidth * scale}px`;
    image.style.height = `${image.naturalHeight * scale}px`;
    image.style.transform = `translate(-50%, -50%) rotate(${angle}deg)`;
  }
  function buttons() {
    q('[data-page-prev]').disabled = page <= 1;
    q('[data-page-next]').disabled = page >= (info?.paginas || 1);
    pages.value = String(page);
  }
  function failed(message) {
    image.hidden = true;
    status.textContent = message;
    q('[data-viewer-retry]').hidden = false;
    root.setAttribute('aria-busy','false');
  }
  async function responseError(response, fallback) {
    if (response.headers.get('Content-Type')?.includes('application/json')) {
      const data = await response.json();
      return new Error(data.error || fallback);
    }
    return new Error(fallback);
  }
  async function showPage() {
    const ticket = ++sequence;
    image.hidden = true; root.setAttribute('aria-busy','true');
    status.textContent = 'Cargando documento…'; q('[data-viewer-retry]').hidden = true;
    buttons();
    const url = new URL(info.imagen_url,location.origin);
    url.pathname = url.pathname.replace(/\/\d+$/,`/${page}`);
    activeURL = url.href;
    try {
      const response = await fetch(activeURL,{credentials:'same-origin'});
      if (!response.ok || !response.headers.get('Content-Type')?.startsWith('image/')) throw await responseError(response,'No se pudo cargar la página. Reintenta o descarga el original.');
      const blob = await response.blob();
      if (ticket !== sequence) return;
      if (imageURL) URL.revokeObjectURL(imageURL);
      imageURL = URL.createObjectURL(blob);
      image.onload = () => {
        if (ticket !== sequence) return;
        image.hidden = false; size(); viewport.scrollTo(0,0);
        image.alt = `${info.hoja ? 'Hoja '+info.hoja+' · ' : ''}Página ${page} de ${info.paginas}. Documento original de solo lectura.`;
        status.textContent = `${info.hoja ? info.hoja+' · ' : ''}Página ${page} de ${info.paginas}`;
        root.setAttribute('aria-busy','false');
      };
      image.onerror = () => {if(ticket===sequence) failed('No se pudo mostrar la imagen. Reintenta o descarga el original.');};
      image.src = imageURL;
    } catch (error) {if(ticket===sequence) failed(error instanceof TypeError?'No se pudo cargar la página. Comprueba la conexión y reintenta.':error.message);}
  }
  async function load() {
    const ticket = ++sequence;
    root.setAttribute('aria-busy','true'); image.hidden = true;
    status.textContent = 'Preparando la vista del documento…';q('[data-viewer-retry]').hidden=true;
    const url = new URL(root.dataset.manifest,location.origin);
    if (sheet) url.searchParams.set('hoja',sheet);
    try {
      const response = await fetch(url,{credentials:'same-origin'});
      if (!response.ok || !response.headers.get('Content-Type')?.includes('application/json')) throw await responseError(response,'No se pudo preparar el visor. Reintenta o descarga el original.');
      const data = await response.json();
      if (ticket !== sequence) return;
      info = data; sheet = info.hoja || ''; page = Math.min(page,info.paginas);
      sheets.replaceChildren(...info.hojas.map(name=>new Option(name,name,name===sheet,name===sheet)));
      q('[data-sheet-control]').hidden = !info.hojas.length;
      pages.replaceChildren(...Array.from({length:info.paginas},(_,i)=>new Option(`${i+1} de ${info.paginas}`,String(i+1))));
      q('[data-page-control]').hidden = info.paginas <= 1;
      await showPage();
    } catch (error) {if(ticket===sequence) failed(error instanceof TypeError?'No se pudo preparar la vista. Comprueba la conexión y reintenta.':error.message);}
  }
  sheets.addEventListener('change',()=>{sheet=sheets.value;page=1;load();});
  pages.addEventListener('change',()=>{page=Number(pages.value);showPage();});
  q('[data-page-prev]').addEventListener('click',()=>{if(page>1){page--;showPage();}});
  q('[data-page-next]').addEventListener('click',()=>{if(page<info.paginas){page++;showPage();}});
  root.querySelectorAll('[data-rotate]').forEach(button => button.addEventListener('click',()=>{
    angle = (angle + Number(button.dataset.rotate) + 360) % 360; size();
    q('[data-rotate-reset]').textContent = `${angle}°`; viewport.scrollTo(0,0);
  }));
  q('[data-rotate-reset]').addEventListener('click',()=>{angle=0;size();q('[data-rotate-reset]').textContent='0°';viewport.scrollTo(0,0);});
  zoom.addEventListener('change',size);new ResizeObserver(size).observe(viewport);
  q('[data-viewer-retry]').addEventListener('click',load);
  q('[data-viewer-fullscreen]').hidden = !document.fullscreenEnabled;
  q('[data-viewer-fullscreen]').addEventListener('click',async()=>{
    try {if(document.fullscreenElement) await document.exitFullscreen();else await root.requestFullscreen();}
    catch {status.textContent='Abre «Ampliar» para consultar el documento en otra pestaña.';}
  });
  document.addEventListener('fullscreenchange',()=>{q('[data-viewer-fullscreen]').textContent=document.fullscreenElement?'Salir de pantalla completa':'Pantalla completa';size();});
  window.addEventListener('hashchange',()=>{const n=Number(new URLSearchParams(location.hash.slice(1)).get('page'));if(info&&Number.isInteger(n)&&n>=1&&n<=info.paginas){page=n;showPage();}});
  load();
})();
