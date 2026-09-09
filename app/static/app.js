(function () {
  'use strict';
  function toast(message, kind) {
    if (!message) return;
    var stack = document.querySelector('.se-toast-stack');
    if (!stack) {
      stack = document.createElement('div');
      stack.className = 'se-toast-stack';
      stack.setAttribute('aria-live', 'polite');
      document.body.appendChild(stack);
    }
    var item = document.createElement('div');
    item.className = 'se-toast se-toast--' + (kind || 'success');
    item.textContent = message;
    stack.appendChild(item);
    window.setTimeout(function () { item.remove(); }, 4200);
  }
  window.seToast = toast;

  document.addEventListener('DOMContentLoaded', function () {
    var params = new URLSearchParams(window.location.search);
    if (params.get('success')) toast(params.get('success'), 'success');
    if (params.get('error') && params.get('error') !== 'pago') toast(params.get('error'), 'error');

    document.querySelectorAll('form').forEach(function (form) {
      form.addEventListener('submit', function () {
        var btn = form.querySelector('button[type="submit"], button:not([type])');
        if (!btn || form.dataset.noLoading === 'true') return;
        btn.dataset.originalText = btn.textContent;
        btn.disabled = true;
        btn.textContent = 'Procesando…';
        window.setTimeout(function () {
          btn.disabled = false;
          if (btn.dataset.originalText) btn.textContent = btn.dataset.originalText;
        }, 8000);
      });
    });

    document.addEventListener('keydown', function (event) {
      if (event.key !== 'Escape') return;
      document.querySelectorAll('[id^="modal-"]:not(.hidden)').forEach(function (modal) {
        modal.classList.add('hidden');
      });
    });
  });

  document.addEventListener('htmx:responseError', function () {
    toast('No se pudo completar la operación. Revisa los datos e inténtalo nuevamente.', 'error');
  });
})();
