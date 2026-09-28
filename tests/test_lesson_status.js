'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {currentStatus, remainingText} = require('../static/lesson-status.js');

const epoch = value => Date.parse(`2026-09-29T${value}+02:00`);
const lesson = (start, end, extra = {}) => ({start: epoch(start), end: epoch(end), date: '2026-09-29', subject: 'Matematika', time: `${start} – ${end}`, classroom: '101', teacher: 'Minta Tanár', ...extra});
const lessons = [lesson('08:00', '08:45'), lesson('09:00', '09:45', {subject: 'Angol'})];

test('one mounted timetable can transition lesson, break, next lesson and day end', () => {
  const moments = ['07:59:59', '08:00:00', '08:44:59', '08:45:00', '08:59:59', '09:00:00', '09:45:00'];
  assert.deepEqual(moments.map(time => currentStatus(lessons, epoch(time)).kind),
    ['upcoming', 'lesson', 'lesson', 'break', 'break', 'lesson', 'empty']);
  assert.equal(currentStatus(lessons, epoch('09:00')).title, 'Angol');
});

test('remaining time and elapsed progress are bounded at exact edges', () => {
  const start = currentStatus(lessons, epoch('08:00'));
  assert.equal(start.progress, 0);
  assert.equal(start.remaining, '45 perc 00 mp van hátra');
  assert.equal(currentStatus(lessons, epoch('08:22:30')).progress, 50);
  const lastSecond = currentStatus(lessons, epoch('08:44:59'));
  assert.equal(lastSecond.remaining, '0 perc 01 mp van hátra');
  assert.ok(lastSecond.progress <= 100);
  const middleBreak = currentStatus(lessons, epoch('08:52:30'));
  assert.equal(middleBreak.remaining, '7 perc 30 mp van hátra');
  assert.equal(middleBreak.progress, 50);
  assert.equal(remainingText(-500), '0 perc 00 mp van hátra');
});

test('overnight gap and time before the first lesson have no break countdown', () => {
  const tomorrow = {...lessons[0], date: '2026-09-30', start: lessons[0].start + 86400000, end: lessons[0].end + 86400000};
  const state = currentStatus([...lessons, tomorrow], epoch('10:00'));
  assert.equal(state.kind, 'upcoming');
  assert.equal(state.remaining, '');
  assert.equal(currentStatus(lessons, epoch('07:30')).kind, 'upcoming');
});

test('epoch comparison is independent of browser display timezone', () => {
  const oldTimezone = process.env.TZ;
  try {
    process.env.TZ = 'America/Los_Angeles';
    assert.equal(currentStatus(lessons, Date.parse('2026-09-29T06:30:00Z')).remaining, '15 perc 00 mp van hátra');
  } finally {
    if (oldTimezone === undefined) delete process.env.TZ;
    else process.env.TZ = oldTimezone;
  }
});

test('empty and overlapping schedules never invent a break', () => {
  assert.equal(currentStatus([], epoch('08:30')).kind, 'empty');
  const overlapping = [lessons[0], lesson('08:30', '09:00')];
  assert.equal(currentStatus(overlapping, epoch('08:50')).kind, 'lesson');
});

test('mounted card uses server time and updates visible countdown and progress through transitions', () => {
  const fs = require('node:fs');
  const vm = require('node:vm');
  const parts = Object.fromEntries(['label', 'date', 'title', 'details', 'teacher', 'remaining', 'timer', 'progress'].map(name => [name, {
    textContent: '', hidden: false, attributes: {}, setAttribute(key, value) { this.attributes[key] = value; }
  }]));
  const card = {
    dataset: {lessonClock: JSON.stringify({server_now: epoch('08:44:59'), lessons})},
    querySelector(selector) { return parts[selector.match(/data-lesson-(.+)\]/)[1]]; }
  };
  let browserNow = epoch('20:00:00'); // Deliberately incorrect local clock.
  let tick;
  const context = {
    Date: {now: () => browserNow},
    document: {querySelectorAll: () => [card], addEventListener() {}},
    window: {setInterval(callback) { tick = callback; }, addEventListener() {}}
  };
  vm.runInNewContext(fs.readFileSync(require.resolve('../static/lesson-status.js'), 'utf8'), context);
  assert.equal(parts.title.textContent, 'Matematika');
  assert.equal(parts.remaining.textContent, '0 perc 01 mp van hátra');
  assert.equal(parts.timer.hidden, false);
  browserNow += 1000;
  tick();
  assert.equal(parts.title.textContent, 'Szünet');
  assert.equal(parts.remaining.textContent, '15 perc 00 mp van hátra');
  assert.equal(parts.progress.value, 0);
  assert.equal(parts.progress.attributes['aria-label'], 'A szünet eltelt része');
  browserNow += 15 * 60 * 1000;
  tick();
  assert.equal(parts.title.textContent, 'Angol');
  assert.equal(parts.remaining.textContent, '45 perc 00 mp van hátra');
  browserNow += 45 * 60 * 1000;
  tick();
  assert.equal(parts.timer.hidden, true);
});
