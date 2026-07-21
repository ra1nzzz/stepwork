(() => {
  const toast = document.querySelector('[data-toast]');
  window.showToast = (message) => {
    if (!toast) return;
    toast.textContent = message;
    toast.classList.add('show');
    clearTimeout(window.__toastTimer);
    window.__toastTimer = setTimeout(() => toast.classList.remove('show'), 2400);
  };

  document.querySelectorAll('[data-tab-group]').forEach((group) => {
    group.addEventListener('click', (event) => {
      const tab = event.target.closest('[data-tab]');
      if (!tab) return;
      group.querySelectorAll('[data-tab]').forEach((item) => item.classList.remove('active'));
      tab.classList.add('active');
      const label = tab.dataset.tab;
      const target = document.querySelector(group.dataset.target || '');
      if (target) target.textContent = label;
      document.dispatchEvent(new CustomEvent('stepwork:tab', { detail: { group: group.dataset.tabGroup, label } }));
    });
  });

  document.querySelectorAll('[data-modal-open]').forEach((button) => {
    button.addEventListener('click', () => document.getElementById(button.dataset.modalOpen)?.classList.add('open'));
  });
  document.querySelectorAll('[data-modal-close]').forEach((button) => {
    button.addEventListener('click', () => button.closest('.modal-backdrop')?.classList.remove('open'));
  });
  document.querySelectorAll('.modal-backdrop').forEach((backdrop) => {
    backdrop.addEventListener('click', (event) => {
      if (event.target === backdrop) backdrop.classList.remove('open');
    });
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') document.querySelectorAll('.modal-backdrop.open').forEach((modal) => modal.classList.remove('open'));
  });
})();
