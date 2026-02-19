export function renderModelSelect(elm, values) {
  if (!elm) return;
  const current = String(elm.value || '').trim();
  elm.innerHTML = '';
  const list = Array.isArray(values) ? values : [];
  const seen = new Set();
  const normalized = [];
  for (const raw of list) {
    const text = String(raw || '').trim();
    const key = text.toLowerCase();
    if (!text || seen.has(key)) continue;
    seen.add(key);
    normalized.push(text);
  }
  if (current && !seen.has(current.toLowerCase())) {
    normalized.unshift(current);
  }
  for (const text of normalized) {
    const option = document.createElement('option');
    option.value = text;
    option.textContent = text;
    elm.appendChild(option);
  }
  if (normalized.length) {
    elm.value = normalized.includes(current) ? current : normalized[0];
  }
}
