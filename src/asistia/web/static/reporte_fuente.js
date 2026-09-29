(() => {
  const zoom = document.querySelector('[data-source-zoom]');
  zoom?.addEventListener('change', () => {
    const scale = Number(zoom.value);
    const image = document.querySelector('[data-source-image]');
    const cells = document.querySelector('[data-source-content]');
    if (image) image.style.width = `${scale * 100}%`;
    if (cells) cells.style.fontSize = `${scale * 13}px`;
  });
  document.querySelector('.source-selected')?.scrollIntoView({block:'center',inline:'center'});
})();
