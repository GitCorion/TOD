(function () {
  document.documentElement.classList.add('js');
  try {
    var saved = localStorage.getItem('catalogo-theme');
    if (saved === 'light' || saved === 'dark') {
      document.documentElement.dataset.theme = saved;
    }
    if (localStorage.getItem('tod.sidebarCollapsed') === '1') {
      document.documentElement.classList.add('sidebar-collapsed');
    }
  } catch (_) {
    /* almacenamiento no disponible */
  }
})();
