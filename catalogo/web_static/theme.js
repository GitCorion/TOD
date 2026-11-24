(function () {
  document.documentElement.classList.add('js');
  try {
    var saved = localStorage.getItem('catalogo-theme');
    if (saved === 'light' || saved === 'dark') {
      document.documentElement.dataset.theme = saved;
    }
  } catch (_) {
    /* almacenamiento no disponible */
  }
})();
