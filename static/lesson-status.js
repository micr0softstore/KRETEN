'use strict';
(() => {
  function remainingText(milliseconds) {
    const seconds = Math.max(0, Math.ceil(milliseconds / 1000));
    return `${Math.floor(seconds / 60)} perc ${String(seconds % 60).padStart(2, '0')} mp van hátra`;
  }

  function currentStatus(lessons, now) {
    const active = lessons.find(item => item.start <= now && now < item.end);
    const upcoming = lessons.find(item => item.start > now);
    const previous = lessons.filter(item => item.end <= now).reduce((last, item) => !last || item.end > last.end ? item : last, null);
    const state = {kind: 'empty', label: 'SZABAD IDŐ', date: '', title: 'Nincs közelgő tanóra.', details: 'Nincs több közelgő óra a következő hétre.', teacher: '', remaining: '', progress: 0, progress_label: ''};
    let start, end;
    if (active) {
      Object.assign(state, {kind: 'lesson', label: 'MOST ÓRÁD VAN', title: active.subject, date: active.date, details: `${active.time} / ${active.classroom}`, teacher: active.teacher, progress_label: 'Az óra eltelt része'});
      start = active.start; end = active.end;
    } else if (upcoming && previous && previous.date === upcoming.date) {
      Object.assign(state, {kind: 'break', label: 'KÉT ÓRA KÖZÖTT', title: 'Szünet', date: upcoming.date, details: `Következik: ${upcoming.subject} · ${upcoming.time}`, teacher: upcoming.classroom, progress_label: 'A szünet eltelt része'});
      start = previous.end; end = upcoming.start;
    } else if (upcoming) {
      Object.assign(state, {kind: 'upcoming', label: 'A KÖVETKEZŐ ÓRÁD', title: upcoming.subject, date: upcoming.date, details: `${upcoming.time} / ${upcoming.classroom}`, teacher: upcoming.teacher});
      return state;
    } else {
      return state;
    }
    state.remaining = remainingText(end - now);
    state.progress = Math.round(Math.min(100, Math.max(0, (now - start) / (end - start) * 100)));
    return state;
  }

  function mount(card) {
    let payload;
    try { payload = JSON.parse(card.dataset.lessonClock); } catch (_) { return; }
    if (!Number.isFinite(payload.server_now) || !Array.isArray(payload.lessons)) return;
    // Offset the browser clock using the server's time. Epochs also keep the
    // school's timezone independent of the browser's timezone.
    const offset = payload.server_now - Date.now();
    const parts = {};
    ['label', 'date', 'title', 'details', 'teacher', 'remaining', 'timer', 'progress'].forEach(name => { parts[name] = card.querySelector(`[data-lesson-${name}]`); });
    const render = () => {
      const state = currentStatus(payload.lessons, Date.now() + offset);
      ['label', 'date', 'title', 'details', 'teacher', 'remaining'].forEach(name => {
        const value = name === 'date' ? state.date || 'Szabad idő' : state[name];
        if (parts[name].textContent !== value) parts[name].textContent = value;
      });
      parts.timer.hidden = !['lesson', 'break'].includes(state.kind);
      parts.progress.value = state.progress;
      parts.progress.setAttribute('aria-label', state.progress_label);
      parts.progress.textContent = `${state.progress}%`;
    };
    render();
    // No live region: the clock remains readable without announcements every second.
    window.setInterval(render, 1000);
    document.addEventListener('visibilitychange', render);
    window.addEventListener('pageshow', render);
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {currentStatus, remainingText};
  if (typeof document !== 'undefined') document.querySelectorAll('[data-lesson-clock]').forEach(mount);
})();
