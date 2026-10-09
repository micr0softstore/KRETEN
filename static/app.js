'use strict';

// Only appearance preferences are saved in the browser. Authentication stays on the server.
const readPreference = (key) => {
  try { return window.localStorage.getItem(key); } catch (_) { return null; }
};
const savePreference = (key, value) => {
  try { window.localStorage.setItem(key, value); } catch (_) { /* Private browsing can disable storage. */ }
};

(() => {
  const root = document.documentElement;
  const sidebar = document.querySelector('.sidebar');
  const backdrop = document.querySelector('.sidebar-backdrop');
  let menuTrigger = null;
  const closeMenu = () => {
    document.body.classList.remove('menu-open');
    if (backdrop) backdrop.hidden = true;
    document.querySelectorAll('[data-open-menu]').forEach((button) => button.setAttribute('aria-expanded', 'false'));
    if (sidebar && window.innerWidth <= 760) sidebar.inert = true;
    if (menuTrigger) { menuTrigger.focus(); menuTrigger = null; }
  };
  if (sidebar) sidebar.inert = window.innerWidth <= 760;
  document.querySelectorAll('[data-open-menu]').forEach((button) => {
    button.addEventListener('click', () => {
      menuTrigger = button;
      document.body.classList.add('menu-open');
      if (sidebar) sidebar.inert = false;
      if (backdrop) backdrop.hidden = false;
      document.querySelectorAll('[data-open-menu]').forEach((trigger) => trigger.setAttribute('aria-expanded', 'true'));
      const close = sidebar && sidebar.querySelector('[data-close-menu]');
      if (close) close.focus();
    });
  });
  document.querySelectorAll('[data-close-menu]').forEach((button) => button.addEventListener('click', closeMenu));
  document.addEventListener('keydown', (event) => {
    if (!document.body.classList.contains('menu-open')) return;
    if (event.key === 'Escape') { event.preventDefault(); closeMenu(); }
    if (event.key === 'Tab' && sidebar) {
      const focusable = [...sidebar.querySelectorAll('a[href], button:not([disabled]), input:not([type="hidden"])')]
        .filter((element) => element.getClientRects().length > 0);
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });
  window.addEventListener('resize', () => {
    if (!sidebar) return;
    if (window.innerWidth > 760) {
      document.body.classList.remove('menu-open');
      if (backdrop) backdrop.hidden = true;
      sidebar.inert = false;
      document.querySelectorAll('[data-open-menu]').forEach((button) => button.setAttribute('aria-expanded', 'false'));
      menuTrigger = null;
    } else if (!document.body.classList.contains('menu-open')) sidebar.inert = true;
  });

  document.querySelectorAll('[data-password-toggle]').forEach((button) => {
    button.addEventListener('click', () => {
      const password = button.parentElement.querySelector('input');
      const reveal = password.type === 'password';
      password.type = reveal ? 'text' : 'password';
      button.setAttribute('aria-label', reveal ? 'Jelszó elrejtése' : 'Jelszó megjelenítése');
      button.setAttribute('aria-pressed', String(reveal));
    });
  });

  const setGradePrivacy = (enabled) => {
    root.classList.toggle('grades-private', enabled);
    document.querySelectorAll('[data-grade-privacy]').forEach((checkbox) => { checkbox.checked = enabled; });
    document.querySelectorAll('[data-private-grade]').forEach((grade) => {
      grade.classList.remove('grade-revealed');
      if (enabled) {
        grade.setAttribute('tabindex', '0');
        grade.setAttribute('title', 'Kattints a jegy megjelenítéséhez');
      } else {
        grade.removeAttribute('tabindex');
        grade.removeAttribute('title');
      }
    });
  };
  setGradePrivacy(readPreference('kreten_private_grades') === 'true');
  document.querySelectorAll('[data-grade-privacy]').forEach((checkbox) => {
    checkbox.addEventListener('change', () => {
      savePreference('kreten_private_grades', String(checkbox.checked));
      setGradePrivacy(checkbox.checked);
    });
  });
  document.querySelectorAll('[data-private-grade]').forEach((grade) => {
    const reveal = () => { if (root.classList.contains('grades-private')) grade.classList.toggle('grade-revealed'); };
    grade.addEventListener('click', (event) => {
      if (root.classList.contains('grades-private')) { event.preventDefault(); event.stopPropagation(); reveal(); }
    });
    grade.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); reveal(); }
    });
  });

  const normalize = (value) => String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('hu').trim();
  document.querySelectorAll('[data-filter-input]').forEach((input) => {
    const target = document.getElementById(input.dataset.filterTarget);
    if (!target) return;
    const filter = () => {
      const term = normalize(input.value);
      let count = 0;
      target.querySelectorAll('[data-filter-text]').forEach((item) => {
        const matches = normalize(item.dataset.filterText).includes(term);
        item.hidden = !matches;
        if (matches) count++;
      });
      document.querySelectorAll('[data-filter-empty]').forEach((empty) => {
        if (empty.dataset.filterEmpty === target.id) empty.hidden = count > 0;
      });
    };
    input.addEventListener('input', filter);
    input.addEventListener('change', filter);
  });

  const picker = document.querySelector('[data-institution-picker]');
  if (!picker) return;
  const form = document.querySelector('[data-login-form]');
  const searchPanel = picker.querySelector('.institution-search-panel');
  const manualPanel = picker.querySelector('.institution-manual-panel');
  const search = document.getElementById('institution-search');
  const codeInput = document.getElementById('institute_code');
  const list = document.getElementById('institution-options');
  const help = document.getElementById('institution-help');
  const status = picker.querySelector('[data-institution-status]');
  const modeToggle = picker.querySelector('[data-institution-mode]');
  let institutions = [];
  let matches = [];
  let activeIndex = -1;
  let manualMode = true;
  let selectedInstitution = null;
  let remoteDirectory = false;
  let remoteResults = null;
  let remoteQuery = '';
  let queryTimer = null;
  let queryAbort = null;
  let querySequence = 0;
  const cleanInstitutions = (items) => (Array.isArray(items) ? items : [])
    .filter((item) => item && typeof item.name === 'string' && typeof item.code === 'string' && item.code.trim())
    .map((item) => ({ name: item.name, code: item.code.trim(), id: item.id == null ? '' : String(item.id) }))
    .sort((left, right) => left.name.localeCompare(right.name, 'hu'));
  const cancelQuery = () => {
    if (queryTimer) window.clearTimeout(queryTimer);
    queryTimer = null;
    if (queryAbort) queryAbort.abort();
    queryAbort = null;
    querySequence++;
    list.removeAttribute('aria-busy');
  };
  const closeList = () => {
    list.hidden = true;
    search.setAttribute('aria-expanded', 'false');
    search.removeAttribute('aria-activedescendant');
  };
  const activate = (index) => {
    activeIndex = index;
    const options = list.querySelectorAll('[role="option"]');
    options.forEach((option, i) => option.setAttribute('aria-selected', String(i === index)));
    if (options[index]) {
      search.setAttribute('aria-activedescendant', options[index].id);
      options[index].scrollIntoView({ block: 'nearest' });
    } else search.removeAttribute('aria-activedescendant');
  };
  const choose = (institution) => {
    cancelQuery();
    selectedInstitution = institution;
    codeInput.value = institution.code;
    search.value = institution.name;
    search.setCustomValidity('');
    help.textContent = `Kiválasztva: ${institution.code}${institution.id ? ' · OM-azonosító: ' + institution.id : ''}`;
    closeList();
  };
  const renderList = () => {
    const term = selectedInstitution && search.value === selectedInstitution.name ? '' : normalize(search.value);
    const allMatches = !term && !selectedInstitution ? [] : remoteDirectory && remoteResults !== null && remoteQuery === term
      ? remoteResults
      : institutions.filter((institution) => normalize(`${institution.name} ${institution.code} ${institution.id || ''}`).includes(term));
    if (selectedInstitution && !allMatches.some((institution) => institution.code === selectedInstitution.code)) allMatches.unshift(selectedInstitution);
    matches = allMatches.slice(0, 60);
    list.replaceChildren();
    matches.forEach((institution, index) => {
      const option = document.createElement('li');
      option.className = 'institution-option';
      option.id = `institution-option-${index}`;
      option.setAttribute('role', 'option');
      option.setAttribute('aria-selected', 'false');
      const name = document.createElement('span');
      name.textContent = institution.name;
      const details = document.createElement('small');
      details.textContent = `${institution.code}${institution.id ? ' · ' + institution.id : ''}`;
      option.append(name, details);
      option.addEventListener('pointerdown', (event) => event.preventDefault());
      option.addEventListener('click', () => { choose(institution); search.focus(); });
      list.append(option);
    });
    if (!matches.length) {
      const empty = document.createElement('li');
      empty.className = 'institution-option-empty';
      empty.textContent = !term ? 'Írd be az iskolád nevét, intézménykódját vagy OM-azonosítóját.' : 'Nincs találat. Próbálj másik nevet, vagy add meg az intézménykódot kézzel.';
      list.append(empty);
    }
    list.hidden = false;
    search.setAttribute('aria-expanded', 'true');
    activate(-1);
    if (!selectedInstitution) help.textContent = !term ? 'Keress név, intézménykód vagy OM-azonosító alapján.' : allMatches.length > 60 ? `${allMatches.length} találat. A pontosabb listához írj be több betűt.` : `${allMatches.length} intézmény. Válassz a listából.`;
  };
  const showQueryLoading = () => {
    matches = [];
    list.replaceChildren();
    const loading = document.createElement('li');
    loading.className = 'institution-option-empty';
    loading.textContent = 'Intézmények keresése…';
    list.append(loading);
    list.hidden = false;
    list.setAttribute('aria-busy', 'true');
    search.setAttribute('aria-expanded', 'true');
    activate(-1);
    help.textContent = 'Keresés a KRÉTA intézményjegyzékében…';
  };
  const refreshOptions = () => {
    const term = normalize(search.value);
    if (!remoteDirectory || selectedInstitution || term.length < 2) {
      cancelQuery();
      renderList();
      if (remoteDirectory && !selectedInstitution) help.textContent = 'Az összes iskola kereséséhez írj be legalább 2 karaktert.';
      return;
    }
    if (remoteResults !== null && remoteQuery === term) { cancelQuery(); renderList(); return; }
    cancelQuery();
    const sequence = querySequence;
    const queryText = search.value.trim();
    showQueryLoading();
    queryTimer = window.setTimeout(async () => {
      queryTimer = null;
      const controller = new AbortController();
      queryAbort = controller;
      const requestTimeout = window.setTimeout(() => controller.abort(), 12000);
      try {
        const response = await fetch(`/api/institutions?q=${encodeURIComponent(queryText)}`, {
          headers: { Accept: 'application/json' }, signal: controller.signal, credentials: 'same-origin'
        });
        if (!response.ok) throw new Error('Institution search unavailable');
        const data = await response.json();
        if (sequence !== querySequence || normalize(search.value) !== term || manualMode || selectedInstitution) return;
        remoteQuery = term;
        remoteResults = cleanInstitutions(data.institutions);
        status.textContent = data.source === 'fallback'
          ? 'Az élő kereső most nem érhető el. Próbáld újra később, vagy add meg az intézménykódot kézzel.'
          : data.stale ? 'A legutóbb elérhető találatokat mutatjuk. Az intézménykód kézzel is megadható.' : '';
        if (search.getAttribute('aria-expanded') === 'true' && document.activeElement === search) renderList();
      } catch (_) {
        if (sequence !== querySequence || normalize(search.value) !== term || manualMode || selectedInstitution) return;
        remoteResults = null;
        remoteQuery = '';
        if (search.getAttribute('aria-expanded') === 'true' && document.activeElement === search) renderList();
        status.textContent = 'Az intézménykeresés most nem sikerült. Próbáld újra, vagy add meg a kódot kézzel.';
        help.textContent = 'Csak az előzőleg betöltött intézmények között talált eredményeket mutatjuk.';
      } finally {
        window.clearTimeout(requestTimeout);
        if (sequence === querySequence) { queryAbort = null; list.removeAttribute('aria-busy'); }
      }
    }, 250);
  };
  const setManualMode = (manual, focus = true) => {
    cancelQuery();
    manualMode = manual;
    searchPanel.hidden = manual;
    manualPanel.hidden = !manual;
    search.required = !manual;
    codeInput.required = manual;
    search.setCustomValidity('');
    modeToggle.textContent = manual ? 'Vissza az intézménykeresőhöz' : 'Intézménykód megadása kézzel';
    closeList();
    if (!manual && (!selectedInstitution || codeInput.value !== selectedInstitution.code)) {
      selectedInstitution = null;
      search.value = '';
      codeInput.value = '';
      help.textContent = 'Keress név, intézménykód vagy OM-azonosító alapján.';
    }
    if (focus) (manual ? codeInput : search).focus();
  };
  modeToggle.addEventListener('click', () => setManualMode(!manualMode));
  search.addEventListener('input', () => {
    selectedInstitution = null;
    codeInput.value = '';
    search.setCustomValidity('');
    refreshOptions();
  });
  search.addEventListener('focus', refreshOptions);
  search.addEventListener('click', () => { if (list.hidden) refreshOptions(); });
  search.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (list.hidden) refreshOptions();
      if (matches.length) activate(event.key === 'ArrowDown' ? Math.min(activeIndex + 1, matches.length - 1) : Math.max(activeIndex - 1, 0));
    } else if (event.key === 'Enter' && !list.hidden) {
      event.preventDefault();
      if (activeIndex >= 0 && matches[activeIndex]) choose(matches[activeIndex]);
      else if (matches.length === 1) choose(matches[0]);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      closeList();
    } else if (event.key === 'Tab') closeList();
  });
  document.addEventListener('pointerdown', (event) => { if (!picker.contains(event.target)) closeList(); });
  if (form) form.addEventListener('submit', (event) => {
    codeInput.value = codeInput.value.trim();
    if (!manualMode && (!selectedInstitution || codeInput.value !== selectedInstitution.code)) {
      event.preventDefault();
      search.setCustomValidity('Válassz egy intézményt a listából, vagy add meg az intézménykódot kézzel.');
      search.reportValidity();
      return;
    }
    const submit = form.querySelector('[type="submit"]');
    if (submit) { submit.disabled = true; submit.textContent = 'Bejelentkezés…'; }
  });
  window.addEventListener('pageshow', () => {
    const submit = form && form.querySelector('[type="submit"]');
    if (submit && submit.disabled) { submit.disabled = false; submit.textContent = 'Bejelentkezés'; }
  });
  const abort = new AbortController();
  const timeout = window.setTimeout(() => abort.abort(), 12000);
  fetch('/api/institutions', { headers: { Accept: 'application/json' }, signal: abort.signal, credentials: 'same-origin' })
    .then((response) => { if (!response.ok) throw new Error('Institution list unavailable'); return response.json(); })
    .then((data) => {
      institutions = cleanInstitutions(data.institutions);
      remoteDirectory = data.source === 'fallback';
      if (!institutions.length && !remoteDirectory) throw new Error('Institution list empty');
      modeToggle.hidden = false;
      // Preserve any manual input typed while the request was running.
      if (!codeInput.value.trim() && document.activeElement !== codeInput) setManualMode(false, false);
      else modeToggle.textContent = 'Vissza az intézménykeresőhöz';
      status.textContent = data.source === 'fallback'
        ? 'A teljes lista helyett az intézmények között név vagy kód alapján kereshetsz.'
        : data.stale ? 'A legutóbb elérhető intézménylistát mutatjuk. Szükség esetén a kód kézzel is megadható.' : '';
      help.textContent = 'Keress név, intézménykód vagy OM-azonosító alapján.';
    })
    .catch(() => {
      status.textContent = 'Az intézménylista most nem érhető el. Add meg az iskolád intézménykódját kézzel.';
      manualMode = true;
      manualPanel.hidden = false;
      searchPanel.hidden = true;
      codeInput.required = true;
      search.required = false;
    })
    .finally(() => window.clearTimeout(timeout));
})();
