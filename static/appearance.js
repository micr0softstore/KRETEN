'use strict';

// Apply saved appearance before the first paint. Only visual preferences live here.
(() => {
  const root = document.documentElement;
  const palettes = ['ocean', 'lavender', 'mint', 'sunset', 'rose', 'graphite'];
  const read = (key) => {
    try { return window.localStorage.getItem(key); } catch (_) { return null; }
  };
  const systemDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  const savedMode = read('kreten_theme') || read('theme');
  const savedColor = read('kreten_color_theme');
  root.dataset.theme = ['light', 'dark'].includes(savedMode) ? savedMode : (systemDark ? 'dark' : 'light');
  root.dataset.colorTheme = palettes.includes(savedColor) ? savedColor : 'ocean';

  document.addEventListener('DOMContentLoaded', () => {
    const sync = () => {
      const dark = root.dataset.theme === 'dark';
      document.querySelectorAll('[data-theme-toggle]').forEach((button) => {
        button.setAttribute('aria-label', dark ? 'Világos megjelenés bekapcsolása' : 'Sötét megjelenés bekapcsolása');
        button.setAttribute('title', dark ? 'Világos megjelenés' : 'Sötét megjelenés');
        button.setAttribute('aria-pressed', String(dark));
      });
      document.querySelectorAll('[data-display-theme]').forEach((input) => { input.checked = input.value === root.dataset.theme; });
      document.querySelectorAll('[data-color-theme-choice]').forEach((input) => { input.checked = input.value === root.dataset.colorTheme; });
      const meta = document.querySelector('meta[name="theme-color"]');
      if (meta) meta.content = dark ? '#101523' : '#eaf0f8';
    };
    const save = (key, value) => {
      try { window.localStorage.setItem(key, value); }
      catch (_) {
        const notice = document.querySelector('[data-appearance-storage]');
        if (notice) notice.textContent = 'A megjelenés megváltozott, de a böngésző most nem engedi a beállítások mentését.';
      }
    };
    const setMode = (value) => {
      if (!['light', 'dark'].includes(value)) return;
      root.dataset.theme = value;
      save('kreten_theme', value);
      sync();
    };
    document.querySelectorAll('[data-theme-toggle]').forEach((button) => {
      button.addEventListener('click', () => setMode(root.dataset.theme === 'dark' ? 'light' : 'dark'));
    });
    document.querySelectorAll('[data-display-theme]').forEach((input) => {
      input.addEventListener('change', () => { if (input.checked) setMode(input.value); });
    });
    document.querySelectorAll('[data-color-theme-choice]').forEach((input) => {
      input.addEventListener('change', () => {
        if (!input.checked || !palettes.includes(input.value)) return;
        root.dataset.colorTheme = input.value;
        save('kreten_color_theme', input.value);
        sync();
      });
    });
    window.addEventListener('storage', (event) => {
      if (event.key === 'kreten_theme' && ['light', 'dark'].includes(event.newValue)) root.dataset.theme = event.newValue;
      if (event.key === 'kreten_color_theme' && palettes.includes(event.newValue)) root.dataset.colorTheme = event.newValue;
      sync();
    });
    sync();
  });
})();
