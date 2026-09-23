'use strict';

(() => {
  // This preference contains colors and motion only, never account data.
  const storageKey = 'kreten_wallpaper';
  const defaults = { color1: '#3e8ef7', color2: '#6c5ce7', animated: true };
  const presets = {
    original: [defaults.color1, defaults.color2],
    sunset: ['#f48c45', '#db4078'],
    mint: ['#22b892', '#3b7be6'],
  };
  const root = document.documentElement;
  const motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
  const colorPattern = /^#[0-9a-f]{6}$/i;
  let preferences = { ...defaults };
  let storageAvailable = true;

  const parse = (value) => {
    let stored;
    try { stored = JSON.parse(value); } catch (_) { return { ...defaults }; }
    if (!stored || typeof stored !== 'object') return { ...defaults };
    return {
      color1: typeof stored.color1 === 'string' && colorPattern.test(stored.color1) ? stored.color1.toLowerCase() : defaults.color1,
      color2: typeof stored.color2 === 'string' && colorPattern.test(stored.color2) ? stored.color2.toLowerCase() : defaults.color2,
      animated: typeof stored.animated === 'boolean' ? stored.animated : defaults.animated,
    };
  };

  try {
    const stored = window.localStorage.getItem(storageKey);
    if (stored !== null) preferences = parse(stored);
    else {
      // Older concept builds used these two appearance keys.
      const color1 = window.localStorage.getItem('bgColor1');
      const color2 = window.localStorage.getItem('bgColor2');
      preferences = parse(JSON.stringify({ color1, color2 }));
    }
  }
  catch (_) { storageAvailable = false; }

  const render = () => {
    root.style.setProperty('--wallpaper-color-one', preferences.color1);
    root.style.setProperty('--wallpaper-color-two', preferences.color2);
    root.dataset.wallpaperMotion = preferences.animated && !motionQuery.matches && !document.hidden ? 'running' : 'paused';
    document.querySelectorAll('[data-wallpaper-color]').forEach((input) => { input.value = preferences[input.dataset.wallpaperColor]; });
    document.querySelectorAll('[data-wallpaper-value]').forEach((output) => { output.textContent = preferences[output.dataset.wallpaperValue]; });
    document.querySelectorAll('[data-wallpaper-preset]').forEach((button) => {
      const palette = presets[button.dataset.wallpaperPreset];
      button.setAttribute('aria-pressed', String(Boolean(palette && palette[0] === preferences.color1 && palette[1] === preferences.color2)));
    });
    document.querySelectorAll('[data-wallpaper-animation]').forEach((checkbox) => {
      checkbox.checked = preferences.animated && !motionQuery.matches;
      checkbox.disabled = motionQuery.matches;
    });
    document.querySelectorAll('[data-wallpaper-motion-note]').forEach((note) => {
      note.textContent = motionQuery.matches ? 'A rendszer csökkentett mozgás beállítása miatt szünetel.' : 'A színek lassan körbefordulnak.';
    });
    document.querySelectorAll('[data-wallpaper-storage]').forEach((note) => {
      note.textContent = storageAvailable ? 'A változtatások automatikusan mentődnek.' : 'A böngésző letiltotta a helyi tárolást; a beállítások csak ezen az oldalon érvényesek.';
    });
  };

  const save = () => {
    try { window.localStorage.setItem(storageKey, JSON.stringify(preferences)); storageAvailable = true; }
    catch (_) { storageAvailable = false; }
    render();
  };

  render();
  document.querySelectorAll('[data-wallpaper-color]').forEach((input) => {
    input.addEventListener('input', () => {
      const key = input.dataset.wallpaperColor;
      if ((key === 'color1' || key === 'color2') && colorPattern.test(input.value)) {
        preferences[key] = input.value.toLowerCase();
        save();
      }
    });
  });
  document.querySelectorAll('[data-wallpaper-preset]').forEach((button) => {
    button.addEventListener('click', () => {
      const palette = presets[button.dataset.wallpaperPreset];
      if (!palette) return;
      [preferences.color1, preferences.color2] = palette;
      save();
    });
  });
  document.querySelectorAll('[data-wallpaper-animation]').forEach((checkbox) => {
    checkbox.addEventListener('change', () => { preferences.animated = checkbox.checked; save(); });
  });
  document.querySelectorAll('[data-wallpaper-reset]').forEach((button) => {
    button.addEventListener('click', () => { preferences = { ...defaults }; save(); });
  });

  const dialog = document.getElementById('wallpaper-settings');
  if (dialog) {
    document.querySelectorAll('[data-wallpaper-open]').forEach((button) => {
      button.addEventListener('click', () => { if (!dialog.open) dialog.showModal(); });
    });
    dialog.querySelectorAll('[data-wallpaper-close]').forEach((button) => {
      button.addEventListener('click', () => dialog.close());
    });
    dialog.addEventListener('click', (event) => {
      const rect = dialog.getBoundingClientRect();
      if (event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) dialog.close();
    });
  }
  motionQuery.addEventListener('change', render);
  document.addEventListener('visibilitychange', render);
  window.addEventListener('storage', (event) => {
    if (event.key !== storageKey && event.key !== null) return;
    preferences = parse(event.newValue);
    render();
  });
})();
