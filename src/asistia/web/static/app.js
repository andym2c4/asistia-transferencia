// Mejora progresiva: conserva el formulario en memoria si el servidor no confirma el guardado.
const dirtyForms = new Set();
document.querySelectorAll('[data-dirty]').forEach(form => {
  form.addEventListener('input', () => dirtyForms.add(form));
  form.addEventListener('asistia:form-clean', () => dirtyForms.delete(form));
});
window.addEventListener('beforeunload', event => { if (dirtyForms.size) { event.preventDefault(); event.returnValue = ''; } });
document.querySelectorAll('[data-back]').forEach(button => button.addEventListener('click', () => history.back()));
document.querySelectorAll('form[data-async]').forEach(form => {
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (form.dataset.busy === 'true') return;
    if (form.hasAttribute('data-calendar-confirm') && dirtyForms.size) {
      form.querySelector('.form-status').textContent = 'Hay cambios sin guardar. Guarda o cancela esos cambios antes de confirmar la calendarización.';
      return;
    }
    const body = new FormData(form);
    if (event.submitter?.name) body.set(event.submitter.name, event.submitter.value);
    let status = form.querySelector('.form-status');
    if (!status) { status = document.createElement('p'); status.className = 'form-status'; status.setAttribute('role','status'); form.append(status); }
    const buttons = [...form.querySelectorAll('button')];
    form.dataset.busy = 'true';
    buttons.forEach(button => { button.disabled = true; });
    status.textContent = body.has('archivos') ? 'Conservando y procesando los archivos. Espera el resultado de cada uno…' : 'Guardando. Espera la confirmación…';
    try {
      const actual = await fetch('/sesion', {credentials:'same-origin',cache:'no-store'});
      if (actual.redirected && new URL(actual.url).pathname === '/ingresar') {
        status.textContent = 'La sesión venció. Tus cambios se conservan en este formulario, pero no se guardaron. Inicia sesión en otra pestaña y vuelve para comprobar el registro.';
        const link = document.createElement('a'); link.href = '/ingresar'; link.target = '_blank'; link.rel = 'noopener'; link.textContent = ' Iniciar sesión ↗'; status.append(link);
        return;
      }
      if (!actual.ok) {
        status.textContent = 'No pudimos comprobar tu sesión. El formulario se conserva. Inténtalo cuando vuelva la conexión.';
        return;
      }
      if (actual.ok && !actual.redirected) {
        const sesion = await actual.json();
        body.set('csrf',sesion.csrf);
        document.querySelectorAll('input[name=csrf]').forEach(input => { input.value = sesion.csrf; });
      }
      const response = await fetch(form.action, {method: 'POST', body: form.hasAttribute('data-urlencoded') ? new URLSearchParams(body) : body, credentials: 'same-origin', headers:{'X-ASISTIA-Async':'1'}});
      if (!response.ok) {
        const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
        status.textContent = doc.querySelector('[data-error]')?.textContent || 'No se pudo confirmar el guardado. Comprueba el estado del registro antes de reintentar.';
        status.setAttribute('role','alert');
        return;
      }
      const result = await response.json();
      const destination = new URL(result.redirect, location.origin);
      if (destination.origin !== location.origin) throw new Error('Destino no permitido');
      if (destination.pathname === '/ingresar') {
        status.textContent = 'La sesión venció. Tus cambios siguen en este formulario, pero no están guardados. Inicia sesión en otra pestaña, vuelve y comprueba el registro.';
        const link = document.createElement('a'); link.href = '/ingresar'; link.target = '_blank'; link.rel = 'noopener'; link.textContent = ' Iniciar sesión ↗'; status.append(link);
        return;
      }
      dirtyForms.clear();
      if (destination.pathname === location.pathname && destination.search === location.search && destination.hash) {
        // Un cambio de fragmento no vuelve a consultar el estado recién guardado.
        history.replaceState(null,'',destination.href);
        window.location.reload();
      } else window.location.assign(destination.href);
    } catch {
      status.textContent = 'No pudimos confirmar el guardado. El formulario se conserva aquí. Abre el registro en otra pestaña y comprueba su estado antes de reintentar.';
      status.setAttribute('role','alert');
    } finally {
      form.dataset.busy = 'false';
      buttons.forEach(button => { button.disabled = false; });
    }
  });
});
const tipo = document.getElementById('tipo');
const corte = document.getElementById('fecha-corte');
if (tipo && corte) {
  const update = () => { corte.required = tipo.value === 'nexus'; corte.disabled = tipo.value !== 'nexus'; };
  tipo.addEventListener('change', update); update();
}

// Delegación para conservar la navegación también tras filtrar el directorio.
document.addEventListener('click', event => {
  const row = event.target.closest('[data-institution-row]');
  if (!row || event.target.closest('a,button,input,select') || window.getSelection()?.toString()) return;
  const link = row.querySelector('[data-institution-link]');
  if (event.ctrlKey || event.metaKey) window.open(link.href, '_blank', 'noopener');
  else window.location.assign(link.href);
});
