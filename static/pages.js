'use strict';
const csrfToken = () => document.querySelector('meta[name="csrf-token"]').content;
async function sendChange(url, body, method = 'POST') {
  const response = await fetch(url, {method, credentials:'same-origin', headers:{'Content-Type':'application/json','X-CSRFToken':csrfToken()}, ...(method === 'DELETE' ? {} : {body: JSON.stringify(body)})});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'A kérés nem sikerült.');
  return data;
}
document.querySelectorAll('[data-json-form], [data-analysis-form]').forEach(form => form.addEventListener('submit', async event => {
  event.preventDefault();
  if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) return;
  const output = form.querySelector('output');
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true; output.textContent = 'Folyamatban…';
  try {
    const data = await sendChange(form.action, Object.fromEntries(new FormData(form)));
    output.textContent = form.hasAttribute('data-analysis-form') ? Object.entries(data.results).map(([key, value]) => `${key}: ${value === null ? 'Nincs elég adat' : value}`).join(' · ') : 'A módosítást a KRÉTA fogadta.';
  } catch (error) { output.textContent = error.message || 'Kapcsolódási hiba.'; }
  finally { button.disabled = false; }
}));
document.querySelectorAll('[data-mutation-url]').forEach(button => button.addEventListener('click', async () => {
  if (button.dataset.confirm && !window.confirm(button.dataset.confirm)) return;
  const output = button.parentElement.querySelector('output');
  button.disabled = true;
  try { await sendChange(button.dataset.mutationUrl, {isPermitted:button.dataset.permission === 'true'}, button.dataset.method || 'POST'); output.textContent = 'A módosítást a KRÉTA fogadta.'; }
  catch (error) { output.textContent = error.message || 'Kapcsolódási hiba.'; }
  finally { button.disabled = false; }
}));
