// Se aplica antes de pintar. Solo se guarda una preferencia visual en este navegador.
(() => {
  const key = 'asistia-theme';
  const valid = value => value === 'dark' ? 'dark' : 'light';
  let preference = 'light';
  try { preference = valid(localStorage.getItem(key)); } catch { /* almacenamiento restringido */ }
  function apply(value) {
    preference = valid(value);
    document.documentElement.style.colorScheme = preference;
    document.querySelectorAll('[data-theme-toggle]').forEach(button => {
      button.setAttribute('aria-checked', String(preference === 'dark'));
      button.title = preference === 'dark' ? 'Cambiar a tema claro' : 'Cambiar a tema oscuro';
    });
  }
  apply(preference);
  document.addEventListener('DOMContentLoaded', () => {
    apply(preference);
    document.querySelectorAll('[data-theme-toggle]').forEach(button => button.addEventListener('click', () => {
      apply(preference === 'dark' ? 'light' : 'dark');
      try { localStorage.setItem(key,preference); } catch { /* el cambio sigue disponible en esta página */ }
    }));
  });
  window.addEventListener('storage', event => {if (event.key === key || event.key === null) apply(event.newValue);});
})();
