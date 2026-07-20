import './style.css';

type Source = { id: number; name: string; question_count: number; used_in_tests: number };
type Answer = { id?: number; text: string; correct: boolean; position?: number };
type Question = { id: number; source_id: number; source_name: string; question_text: string; answers: Answer[] };
type TestRule = { source_id: number; source_name?: string; question_count: number; available_questions?: number };
type TestItem = { id: number; name: string; time_limit_minutes: number; is_active: boolean; total_questions: number; attempt_count: number; rules: TestRule[] };
type ParsedQuestion = { question: string; answers: Answer[]; valid: boolean; problems: string[]; duplicate_in_file: boolean; duplicate_in_database: boolean; source_name?: string | null };
type ErrorReport = { id: number; status: string; message_text: string | null; created_at: string; fixed_at: string | null; attempt_id: number | null; question_id: number | null; question_text: string | null; source_name: string | null; answers: Answer[]; question: Question | null; user: { full_name: string | null; telegram_id: number | null; phone: string | null; username: string | null } };
type DuplicateGroup = { key: string; count: number; keep_id: number; items: Question[] };

const app = document.querySelector<HTMLDivElement>('#app')!;
const modalRoot = document.querySelector<HTMLDivElement>('#modal-root')!;
const toastElement = document.querySelector<HTMLDivElement>('#toast')!;
let token = localStorage.getItem('admin_token') || '';
let sourceCache: Source[] = [];
let parsedImport: ParsedQuestion[] = [];
let debounceTimer: number | undefined;
let selectedQuestionIds = new Set<number>();

function esc(value: unknown): string {
  return String(value ?? '').replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]!));
}
function toast(message: string, type: 'ok' | 'error' = 'ok'): void {
  toastElement.textContent = message; toastElement.className = `toast show ${type}`;
  window.setTimeout(() => toastElement.className = 'toast', 3200);
}
async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers || {});
  if (!(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  if (token) headers.set('Authorization', `Bearer ${token}`);
  const response = await fetch(path, { ...options, headers });
  const data = await response.json().catch(() => ({ detail: 'Server javobi noto‘g‘ri' }));
  if (response.status === 401 && path !== '/api/auth/admin/login') { logout(); throw new Error('Sessiya muddati tugagan'); }
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail));
  return data as T;
}
function loading(): void { document.querySelector<HTMLElement>('#content')!.innerHTML = '<div class="loading"><div class="spinner"></div><p>Yuklanmoqda…</p></div>'; }
function logout(): void { token = ''; localStorage.removeItem('admin_token'); location.hash = ''; showLogin(); }

function showLogin(): void {
  app.innerHTML = `<main class="login-page"><form class="login-card" id="login-form"><div class="brand-mark">T</div><h1>Admin panel</h1><p>Testlar va savollarni boshqarish</p><label>Login<input name="username" autocomplete="username" required autofocus></label><label>Parol<input name="password" type="password" autocomplete="current-password" required></label><button class="primary" type="submit">Kirish</button><div class="form-error" id="login-error"></div></form></main>`;
  document.querySelector<HTMLFormElement>('#login-form')!.addEventListener('submit', async (event) => {
    event.preventDefault(); const target = event.currentTarget as HTMLFormElement; const form = new FormData(target); const button = target.querySelector<HTMLButtonElement>('button')!;
    button.disabled = true;
    try {
      const result = await api<{ access_token: string }>('/api/auth/admin/login', { method: 'POST', body: JSON.stringify({ username: form.get('username'), password: form.get('password') }) });
      token = result.access_token; localStorage.setItem('admin_token', token); showShell();
    } catch (error) { document.querySelector('#login-error')!.textContent = error instanceof Error ? error.message : String(error); }
    finally { button.disabled = false; }
  });
}

function showShell(): void {
  app.innerHTML = `<div class="shell"><aside class="sidebar" id="sidebar"><div class="brand"><div class="brand-mark small">T</div><div><strong>Test Bot</strong><span>Admin panel</span></div></div><nav>
    <a href="#dashboard" data-nav="dashboard">📊 <span>Bosh sahifa</span></a>
    <a href="#sources" data-nav="sources">📚 <span>Manbalar</span></a>
    <a href="#search" data-nav="search">🔍 <span>Qidiruv</span></a>
    <a href="#tests" data-nav="tests">📝 <span>Testlar</span></a>
    <a href="#reports" data-nav="reports">⚠️ <span>Xatoliklar</span></a>
    <a href="#import" data-nav="import">📥 <span>Import</span></a>
  </nav><button class="logout" id="logout">🚪 Chiqish</button></aside><div class="main"><header class="topbar"><button class="menu" id="menu">☰</button><div><strong id="page-title">Bosh sahifa</strong><span id="page-subtitle">Tizim holati</span></div><a class="open-app" href="/app/" target="_blank">Mini App ↗</a></header><section id="content" class="content"></section></div></div>`;
  document.querySelector('#logout')?.addEventListener('click', logout);
  document.querySelector('#menu')?.addEventListener('click', () => document.querySelector('#sidebar')?.classList.toggle('open'));
  window.addEventListener('hashchange', route);
  if (!location.hash) location.hash = '#dashboard'; else void route();
}

function setTitle(title: string, subtitle = ''): void {
  document.querySelector('#page-title')!.textContent = title; document.querySelector('#page-subtitle')!.textContent = subtitle;
  const section = location.hash.slice(1).split('/')[0] || 'dashboard';
  document.querySelectorAll('[data-nav]').forEach(el => el.classList.toggle('active', (el as HTMLElement).dataset.nav === section));
  document.querySelector('#sidebar')?.classList.remove('open');
}
async function route(): Promise<void> {
  const routePath = location.hash.slice(1).split('?')[0];
  const [section, id] = routePath.split('/');
  if (!token) return showLogin();
  if (section === 'sources' && id) return showSource(Number(id));
  switch (section) {
    case 'sources': return showSources();
    case 'search': return showSearch();
    case 'tests': return showTests();
    case 'reports': return showReports();
    case 'import': return showImport();
    default: return showDashboard();
  }
}

async function showDashboard(): Promise<void> {
  setTitle('Bosh sahifa', 'Real vaqt statistikasi'); loading();
  try {
    const stats = await api<any>('/api/admin/stats'); const max = Math.max(1, ...stats.last_7_days.map((d: any) => d.count));
    document.querySelector('#content')!.innerHTML = `<div class="stats-grid"><article><span>👥</span><div><strong>${stats.users.total}</strong><small>Jami foydalanuvchilar</small></div></article><article><span>📝</span><div><strong>${stats.attempts.today}</strong><small>Bugungi testlar</small></div></article><article><span>🎯</span><div><strong>${stats.attempts.average}%</strong><small>O‘rtacha natija</small></div></article><article><span>⚠️</span><div><strong>${stats.reports.open}</strong><small>Ochiq xatoliklar</small></div></article></div>
    <div class="dashboard-grid"><section class="panel"><div class="panel-title"><h2>Oxirgi 7 kun</h2><span>Test urinishlari</span></div><div class="bar-chart">${stats.last_7_days.map((d: any) => `<div><span style="height:${Math.max(5, d.count / max * 100)}%" title="${d.count}"></span><b>${d.count}</b><small>${d.date}</small></div>`).join('')}</div></section><section class="panel"><div class="panel-title"><h2>Asosiy ko‘rsatkichlar</h2></div><dl class="facts"><div><dt>Bugun qo‘shilgan</dt><dd>${stats.users.today}</dd></div><div><dt>Shu hafta</dt><dd>${stats.users.week}</dd></div><div><dt>Jami testlar</dt><dd>${stats.attempts.total}</dd></div><div><dt>Eng mashhur</dt><dd>${esc(stats.popular_test?.name || '—')}</dd></div></dl></section></div>
    <section class="panel"><div class="panel-title"><h2>Oxirgi urinishlar</h2></div><div class="table-wrap"><table><thead><tr><th>Foydalanuvchi</th><th>Test</th><th>Natija</th><th>Vaqt</th></tr></thead><tbody>${stats.recent_attempts.map((r: any) => `<tr><td>${esc(r.user)}</td><td>${esc(r.test)}</td><td><span class="score ${r.percentage >= 70 ? 'good' : r.percentage >= 50 ? 'mid' : 'bad'}">${r.percentage}%</span></td><td>${new Date(r.finished_at).toLocaleString('uz-UZ')}</td></tr>`).join('') || '<tr><td colspan="4" class="empty-cell">Urinishlar yo‘q</td></tr>'}</tbody></table></div></section>`;
  } catch (error) { showError(error); }
}

async function loadSources(): Promise<Source[]> { sourceCache = await api<Source[]>('/api/admin/sources'); return sourceCache; }
async function showSources(): Promise<void> {
  setTitle('Manbalar', 'Savollar bazalari'); loading();
  try { const sources = await loadSources(); document.querySelector('#content')!.innerHTML = `<section class="panel"><div class="panel-title"><div><h2>Manbalar</h2><span>${sources.length} ta manba</span></div><button class="primary compact" id="new-source">+ Yangi manba</button></div><div class="table-wrap"><table><thead><tr><th>№</th><th>Manba nomi</th><th>Savollar</th><th>Testlarda</th><th>Amallar</th></tr></thead><tbody>${sources.map((s, i) => `<tr><td>${i + 1}</td><td><a class="source-link" href="#sources/${s.id}">${esc(s.name)}</a></td><td><strong>${s.question_count}</strong></td><td>${s.used_in_tests}</td><td class="actions"><button data-edit-source="${s.id}">✏️</button><button class="danger-icon" data-delete-source="${s.id}">🗑</button></td></tr>`).join('') || '<tr><td colspan="5" class="empty-cell">Manba mavjud emas</td></tr>'}</tbody></table></div></section>`;
    document.querySelector('#new-source')?.addEventListener('click', () => sourceModal());
    document.querySelectorAll<HTMLElement>('[data-edit-source]').forEach(b => b.addEventListener('click', () => sourceModal(sources.find(s => s.id === Number(b.dataset.editSource)))));
    document.querySelectorAll<HTMLElement>('[data-delete-source]').forEach(b => b.addEventListener('click', () => deleteSource(Number(b.dataset.deleteSource))));
  } catch (error) { showError(error); }
}
function sourceModal(source?: Source): void {
  openModal(`<form id="source-form"><h2>${source ? 'Manbani tahrirlash' : 'Yangi manba'}</h2><label>Manba nomi<input name="name" value="${esc(source?.name || '')}" required minlength="2" autofocus></label><div class="modal-actions"><button type="button" class="ghost" data-close>Bekor qilish</button><button class="primary" type="submit">Saqlash</button></div></form>`);
  document.querySelector<HTMLFormElement>('#source-form')!.addEventListener('submit', async e => { e.preventDefault(); const name = String(new FormData(e.currentTarget as HTMLFormElement).get('name')); try { await api(`/api/admin/sources${source ? `/${source.id}` : ''}`, { method: source ? 'PUT' : 'POST', body: JSON.stringify({ name }) }); closeModal(); toast('✅ Manba saqlandi'); await showSources(); } catch (error) { toast(error instanceof Error ? error.message : String(error), 'error'); } });
}
async function deleteSource(id: number): Promise<void> { if (!confirm('Manba va undagi barcha savollar o‘chirilsinmi?')) return; try { await api(`/api/admin/sources/${id}`, { method: 'DELETE' }); toast('🗑 Manba o‘chirildi'); await showSources(); } catch (error) { toast(error instanceof Error ? error.message : String(error), 'error'); } }

async function showSource(sourceId: number, page = 1, search = ''): Promise<void> {
  setTitle('Manba savollari', 'Savollarni qidirish va tahrirlash');
  selectedQuestionIds = new Set<number>();
  loading();
  try {
    const data = await api<any>(`/api/admin/sources/${sourceId}/questions?page=${page}&search=${encodeURIComponent(search)}`);
    const refresh = () => showSource(sourceId, page, search);
    document.querySelector('#content')!.innerHTML = `<section class="panel"><div class="panel-title"><div><a class="back-link" href="#sources">← Manbalar</a><h2>${esc(data.source.name)}</h2><span>${data.total} ta savol</span></div><div class="toolbar"><button class="secondary compact" id="find-duplicates">Dublikatlar</button><button class="secondary compact" id="source-import">📥 Import</button><button class="secondary compact" id="move-selected" disabled>⇄ Ko‘chirish</button><button class="primary compact" id="new-question">+ Savol qo‘shish</button></div></div><div class="search-row"><input id="source-search" placeholder="Savol yoki javobdan qidirish…" value="${esc(search)}"></div><div class="bulk-line"><label><input id="select-page-questions" type="checkbox"> Sahifadagi savollarni tanlash</label><span id="selected-count">0 ta tanlandi</span></div><div class="question-list">${data.items.map((q: Question) => questionCard(q, true)).join('') || '<div class="empty-block">Savollar topilmadi</div>'}</div><div class="pagination"><button class="ghost compact" id="prev-page" ${page <= 1 ? 'disabled' : ''}>← Oldingi</button><span>${page} / ${data.pages}</span><button class="ghost compact" id="next-page" ${page >= data.pages ? 'disabled' : ''}>Keyingi →</button></div></section>`;
    document.querySelector('#new-question')?.addEventListener('click', () => questionModal(undefined, sourceId));
    document.querySelector('#find-duplicates')?.addEventListener('click', () => showDuplicates(sourceId, refresh));
    document.querySelector('#source-import')?.addEventListener('click', () => { location.hash = `#import?source=${sourceId}`; });
    document.querySelector('#move-selected')?.addEventListener('click', () => moveSelectedQuestions(sourceId, refresh));
    bindQuestionActions(refresh);
    bindQuestionSelection();
    document.querySelector<HTMLInputElement>('#select-page-questions')?.addEventListener('change', e => {
      const checked = (e.target as HTMLInputElement).checked;
      document.querySelectorAll<HTMLInputElement>('[data-select-question]').forEach(input => {
        input.checked = checked;
        const id = Number(input.dataset.selectQuestion);
        if (checked) selectedQuestionIds.add(id); else selectedQuestionIds.delete(id);
      });
      updateSelectionState();
    });
    document.querySelector<HTMLInputElement>('#source-search')!.addEventListener('input', e => { clearTimeout(debounceTimer); const value = (e.target as HTMLInputElement).value; debounceTimer = window.setTimeout(() => showSource(sourceId, 1, value), 300); });
    document.querySelector('#prev-page')?.addEventListener('click', () => showSource(sourceId, page - 1, search));
    document.querySelector('#next-page')?.addEventListener('click', () => showSource(sourceId, page + 1, search));
  } catch (error) { showError(error); }
}
function questionCard(q: Question, selectable = false): string {
  const correct = q.answers.find(a => a.correct);
  const sourceBadge = `<span class="source-badge">${esc(q.source_name)}</span>`;
  const selector = selectable ? `<label class="question-select"><input type="checkbox" data-select-question="${q.id}">${sourceBadge}</label>` : sourceBadge;
  return `<article class="question-item"><div class="question-head">${selector}<div class="actions"><button data-edit-question="${q.id}">✏️</button><button class="danger-icon" data-delete-question="${q.id}">🗑</button></div></div><h3>${esc(q.question_text)}</h3><p class="correct-answer">✅ ${esc(correct?.text || 'To‘g‘ri javob belgilanmagan')}</p><details><summary>Barcha variantlar (${q.answers.length})</summary>${q.answers.map(a => `<div class="answer-row ${a.correct ? 'correct' : ''}">${a.correct ? '✓' : '•'} ${esc(a.text)}</div>`).join('')}</details></article>`;
}
function bindQuestionActions(refresh: () => void): void {
  document.querySelectorAll<HTMLElement>('[data-edit-question]').forEach(b => b.addEventListener('click', async () => { try { const q = await api<Question>(`/api/admin/questions/${b.dataset.editQuestion}`); questionModal(q, q.source_id, refresh); } catch (e) { toast(String(e), 'error'); } }));
  document.querySelectorAll<HTMLElement>('[data-delete-question]').forEach(b => b.addEventListener('click', async () => { if (!confirm('Savol o‘chirilsinmi?')) return; try { await api(`/api/admin/questions/${b.dataset.deleteQuestion}`, { method: 'DELETE' }); toast('🗑 Savol o‘chirildi'); refresh(); } catch (e) { toast(e instanceof Error ? e.message : String(e), 'error'); } }));
}
function bindQuestionSelection(): void {
  document.querySelectorAll<HTMLInputElement>('[data-select-question]').forEach(input => input.addEventListener('change', () => {
    const id = Number(input.dataset.selectQuestion);
    if (input.checked) selectedQuestionIds.add(id); else selectedQuestionIds.delete(id);
    updateSelectionState();
  }));
}
function updateSelectionState(): void {
  const count = selectedQuestionIds.size;
  const moveButton = document.querySelector<HTMLButtonElement>('#move-selected');
  if (moveButton) moveButton.disabled = count === 0;
  const counter = document.querySelector('#selected-count');
  if (counter) counter.textContent = `${count} ta tanlandi`;
}
async function moveSelectedQuestions(currentSourceId: number, refresh: () => void): Promise<void> {
  if (!selectedQuestionIds.size) return toast('Avval savollarni tanlang', 'error');
  if (!sourceCache.length) await loadSources();
  const targets = sourceCache.filter(source => source.id !== currentSourceId);
  if (!targets.length) return toast('Ko‘chirish uchun boshqa manba yo‘q', 'error');
  openModal(`<form id="move-form"><h2>Savollarni ko‘chirish</h2><p class="modal-hint">${selectedQuestionIds.size} ta savol boshqa manbaga o‘tkaziladi.</p><label>Yangi manba<select name="target_source_id">${targets.map(source => `<option value="${source.id}">${esc(source.name)} (${source.question_count})</option>`).join('')}</select></label><div class="modal-actions"><button type="button" class="ghost" data-close>Bekor qilish</button><button class="primary" type="submit">Ko‘chirish</button></div><div class="form-error" id="move-error"></div></form>`);
  document.querySelector<HTMLFormElement>('#move-form')!.addEventListener('submit', async event => {
    event.preventDefault();
    const button = (event.currentTarget as HTMLFormElement).querySelector<HTMLButtonElement>('button[type=submit]')!;
    const form = new FormData(event.currentTarget as HTMLFormElement);
    button.disabled = true;
    try {
      const result = await api<any>('/api/admin/questions/move', { method: 'POST', body: JSON.stringify({ question_ids: [...selectedQuestionIds], target_source_id: Number(form.get('target_source_id')) }) });
      closeModal();
      sourceCache = [];
      toast(`✅ ${result.moved} ta savol ko‘chirildi`);
      refresh();
    } catch (error) {
      document.querySelector('#move-error')!.textContent = error instanceof Error ? error.message : String(error);
      button.disabled = false;
    }
  });
}
async function showDuplicates(sourceId: number, refresh: () => void): Promise<void> {
  openModal('<div class="duplicate-modal"><h2>Dublikat savollar</h2><div class="loading small"><div class="spinner"></div><p>Tekshirilmoqda...</p></div></div>');
  try {
    const data = await api<{ source: Source; groups: DuplicateGroup[]; group_count: number; duplicate_question_count: number; delete_candidate_count: number }>(`/api/admin/sources/${sourceId}/duplicates`);
    if (!data.groups.length) {
      openModal(`<div class="duplicate-modal"><h2>Dublikat savollar</h2><p class="modal-hint">${esc(data.source.name)} manbasida takrorlangan savol topilmadi.</p><div class="modal-actions"><button class="primary" data-close>Yopish</button></div></div>`);
      return;
    }
    openModal(`<div class="duplicate-modal wide-modal"><h2>Dublikat savollar</h2><p class="modal-hint">${data.group_count} ta guruh, ${data.delete_candidate_count} ta ortiqcha savol topildi.</p><div class="duplicate-actions"><button class="primary compact" id="dedupe-auto">Har guruhdan bittadan qoldirish</button><button class="danger-button compact" id="dedupe-selected" disabled>Tanlanganlarni o'chirish</button></div><div class="duplicate-list">${data.groups.map((group, groupIndex) => `<section class="duplicate-group"><div class="duplicate-title"><strong>Guruh ${groupIndex + 1}</strong><span>${group.count} ta takror</span></div>${group.items.map(item => `<article class="duplicate-item ${item.id === group.keep_id ? 'keep' : ''}"><label><input type="checkbox" data-duplicate-question="${item.id}" ${item.id === group.keep_id ? 'disabled' : ''}> <span>ID ${item.id}${item.id === group.keep_id ? ' - qoldiriladi' : ''}</span></label><h3>${esc(item.question_text)}</h3><small>${esc(item.source_name)} · To'g'ri: ${esc(item.answers.find(answer => answer.correct)?.text || 'Belgilanmagan')}</small></article>`).join('')}</section>`).join('')}</div></div>`);
    const updateDeleteButton = () => {
      const checked = document.querySelectorAll<HTMLInputElement>('[data-duplicate-question]:checked').length;
      const button = document.querySelector<HTMLButtonElement>('#dedupe-selected');
      if (button) {
        button.disabled = checked === 0;
        button.textContent = checked ? `${checked} ta tanlanganni o'chirish` : "Tanlanganlarni o'chirish";
      }
    };
    document.querySelectorAll<HTMLInputElement>('[data-duplicate-question]').forEach(input => input.addEventListener('change', updateDeleteButton));
    document.querySelector('#dedupe-auto')?.addEventListener('click', async () => {
      if (!confirm('Har bir dublikat guruhidan faqat bittadan savol qoldirilsinmi?')) return;
      try {
        const result = await api<any>(`/api/admin/sources/${sourceId}/duplicates/deduplicate`, { method: 'POST', body: JSON.stringify({}) });
        closeModal();
        toast(`${result.deleted} ta dublikat savol o'chirildi`);
        refresh();
      } catch (error) {
        toast(error instanceof Error ? error.message : String(error), 'error');
      }
    });
    document.querySelector('#dedupe-selected')?.addEventListener('click', async () => {
      const ids = [...document.querySelectorAll<HTMLInputElement>('[data-duplicate-question]:checked')].map(input => Number(input.dataset.duplicateQuestion));
      if (!ids.length || !confirm(`${ids.length} ta tanlangan savol o'chirilsinmi?`)) return;
      try {
        const result = await api<any>('/api/admin/questions/bulk-delete', { method: 'POST', body: JSON.stringify({ question_ids: ids }) });
        closeModal();
        toast(`${result.deleted} ta savol o'chirildi`);
        refresh();
      } catch (error) {
        toast(error instanceof Error ? error.message : String(error), 'error');
      }
    });
  } catch (error) {
    openModal(`<div class="duplicate-modal"><h2>Dublikat savollar</h2><div class="error-box"><strong>Xatolik</strong><p>${esc(error instanceof Error ? error.message : String(error))}</p></div><div class="modal-actions"><button class="primary" data-close>Yopish</button></div></div>`);
  }
}
async function questionModal(question?: Question, defaultSourceId?: number, refresh?: () => void | Promise<void>): Promise<void> {
  if (!sourceCache.length) await loadSources(); const answers = question?.answers.length ? question.answers : [{ text: '', correct: true }, { text: '', correct: false }, { text: '', correct: false }, { text: '', correct: false }];
  openModal(`<form id="question-form" class="wide-modal"><h2>${question ? 'Savolni tahrirlash' : 'Yangi savol'}</h2><label>Manba<select name="source_id">${sourceCache.map(s => `<option value="${s.id}" ${s.id === (question?.source_id || defaultSourceId) ? 'selected' : ''}>${esc(s.name)} (${s.question_count})</option>`).join('')}</select></label><label>Savol matni<textarea name="question_text" rows="4" required>${esc(question?.question_text || '')}</textarea></label><div class="field-label">Javob variantlari</div><div id="answer-editor">${answers.map((a, i) => answerEditorRow(a, i)).join('')}</div><button type="button" class="secondary compact" id="add-answer">+ Variant qo‘shish</button><div class="form-error" id="question-error"></div><div class="modal-actions"><button type="button" class="ghost" data-close>Bekor qilish</button><button class="primary" type="submit">💾 Saqlash</button></div></form>`);
  const editor = document.querySelector('#answer-editor')!; const reindex = () => editor.querySelectorAll<HTMLElement>('.answer-editor-row').forEach((row, i) => { row.querySelector<HTMLInputElement>('input[type=radio]')!.value = String(i); row.querySelector<HTMLElement>('.answer-letter')!.textContent = String.fromCharCode(65 + i); });
  document.querySelector('#add-answer')?.addEventListener('click', () => { const div = document.createElement('div'); div.innerHTML = answerEditorRow({ text: '', correct: false }, editor.children.length); editor.append(div.firstElementChild!); bindAnswerDeletes(); });
  const bindAnswerDeletes = () => document.querySelectorAll<HTMLElement>('[data-remove-answer]').forEach(b => b.onclick = () => { if (editor.children.length <= 2) return toast('Kamida 2 ta variant bo‘lishi kerak', 'error'); b.closest('.answer-editor-row')?.remove(); reindex(); }); bindAnswerDeletes();
  document.querySelector<HTMLFormElement>('#question-form')!.addEventListener('submit', async e => { e.preventDefault(); const form = new FormData(e.currentTarget as HTMLFormElement); const rows = [...editor.querySelectorAll<HTMLElement>('.answer-editor-row')]; const correctIndex = Number(form.get('correct_index')); const payload = { source_id: Number(form.get('source_id')), question_text: String(form.get('question_text')), answers: rows.map((row, i) => ({ text: row.querySelector<HTMLInputElement>('input[type=text]')!.value, correct: i === correctIndex })) }; try { await api(`/api/admin/questions${question ? `/${question.id}` : ''}`, { method: question ? 'PUT' : 'POST', body: JSON.stringify(payload) }); closeModal(); toast('✅ Savol saqlandi'); refresh ? await refresh() : await showSource(payload.source_id); } catch (error) { document.querySelector('#question-error')!.textContent = error instanceof Error ? error.message : String(error); } });
}
function answerEditorRow(a: Answer, i: number): string { return `<div class="answer-editor-row"><span class="answer-letter">${String.fromCharCode(65 + i)}</span><input type="radio" name="correct_index" value="${i}" ${a.correct ? 'checked' : ''} title="To‘g‘ri javob"><input type="text" value="${esc(a.text)}" placeholder="Javob matni" required><button type="button" class="danger-icon" data-remove-answer>×</button></div>`; }

async function showSearch(): Promise<void> {
  setTitle('Global qidiruv', 'Barcha manbalardan qidirish');
  if (!sourceCache.length) await loadSources();
  document.querySelector('#content')!.innerHTML = `<section class="panel"><div class="panel-title"><div><h2>Barcha savollar bo‘yicha qidiruv</h2><span>Savol va javob matnidan qidiradi</span></div></div><div class="search-grid"><input id="global-q" placeholder="Qidiruv so‘zini kiriting…" autofocus><select id="global-source"><option value="">Barcha manbalar</option>${sourceCache.map(s => `<option value="${s.id}">${esc(s.name)}</option>`).join('')}</select></div><div id="search-results" class="question-list"><div class="empty-block">Qidiruvni boshlang</div></div></section>`;
  const run = async () => { const q = document.querySelector<HTMLInputElement>('#global-q')!.value.trim(); const sid = document.querySelector<HTMLSelectElement>('#global-source')!.value; if (!q) return document.querySelector('#search-results')!.innerHTML = '<div class="empty-block">Qidiruvni boshlang</div>'; document.querySelector('#search-results')!.innerHTML = '<div class="loading small"><div class="spinner"></div></div>'; try { const data = await api<any>(`/api/admin/search?q=${encodeURIComponent(q)}${sid ? `&source_id=${sid}` : ''}`); document.querySelector('#search-results')!.innerHTML = data.items.map((item: Question) => questionCard(item)).join('') || '<div class="empty-block">Natija topilmadi</div>'; bindQuestionActions(run); } catch (e) { showError(e, '#search-results'); } };
  document.querySelector('#global-q')?.addEventListener('input', () => { clearTimeout(debounceTimer); debounceTimer = window.setTimeout(run, 300); }); document.querySelector('#global-source')?.addEventListener('change', run);
}

async function showReports(status = 'open', page = 1): Promise<void> {
  setTitle('Xatoliklar', 'Foydalanuvchilar yuborgan savol muammolari');
  loading();
  try {
    const data = await api<any>(`/api/admin/reports?status=${encodeURIComponent(status)}&page=${page}`);
    document.querySelector('#content')!.innerHTML = `<section class="panel"><div class="panel-title"><div><h2>Xatoliklar</h2><span>${data.total} ta xabar</span></div><div class="toolbar"><button class="secondary compact" data-report-status="open">Ochiq</button><button class="secondary compact" data-report-status="fixed">Yopilgan</button><button class="secondary compact" data-report-status="all">Hammasi</button></div></div><div class="report-list">${data.items.map((report: ErrorReport) => reportCard(report)).join('') || '<div class="empty-block">Xatolik xabari yo‘q</div>'}</div><div class="pagination"><button class="ghost compact" id="prev-report-page" ${page <= 1 ? 'disabled' : ''}>← Oldingi</button><span>${page} / ${data.pages}</span><button class="ghost compact" id="next-report-page" ${page >= data.pages ? 'disabled' : ''}>Keyingi →</button></div></section>`;
    document.querySelectorAll<HTMLElement>('[data-report-status]').forEach(button => button.addEventListener('click', () => showReports(button.dataset.reportStatus || 'open', 1)));
    document.querySelector('#prev-report-page')?.addEventListener('click', () => showReports(status, page - 1));
    document.querySelector('#next-report-page')?.addEventListener('click', () => showReports(status, page + 1));
    bindReportActions(() => showReports(status, page));
  } catch (error) {
    showError(error);
  }
}

function reportCard(report: ErrorReport): string {
  const answers = report.answers?.length ? report.answers.map(answer => `<div class="answer-row ${answer.correct ? 'correct' : ''}">${answer.correct ? '✓' : '•'} ${esc(answer.text)}</div>`).join('') : '<div class="answer-row">Javob variantlari saqlanmagan</div>';
  return `<article class="report-card ${report.status === 'fixed' ? 'fixed' : ''}" data-report-id="${report.id}"><div class="report-head"><div><span class="status ${report.status === 'fixed' ? 'active' : 'inactive'}">${report.status === 'fixed' ? 'Yopilgan' : 'Ochiq'}</span><h3>${esc(report.question_text || 'Savol o‘chirilgan')}</h3><small>${esc(report.source_name || 'Manba noma’lum')} · ${new Date(report.created_at).toLocaleString('uz-UZ')}</small></div><div class="actions">${report.question ? `<button data-edit-report-question="${report.question_id}">✏️</button><button class="danger-icon" data-delete-report-question="${report.question_id}">🗑</button>` : ''}<button class="secondary compact no-issue" data-fix-report="${report.id}" ${report.status === 'fixed' ? 'disabled' : ''}>Muammo yo'q</button><button class="danger-icon" data-delete-report="${report.id}">×</button></div></div><div class="report-meta"><strong>${esc(report.user.full_name || 'Foydalanuvchi')}</strong><span>${report.user.phone ? esc(report.user.phone) : ''}${report.user.username ? ` · @${esc(report.user.username)}` : ''}${report.user.telegram_id ? ` · ID ${report.user.telegram_id}` : ''}</span></div><blockquote>${esc(report.message_text || 'Izoh yozilmagan')}</blockquote><details><summary>Javob variantlari</summary>${answers}</details></article>`;
}

function bindReportActions(refresh: () => void): void {
  document.querySelectorAll<HTMLElement>('[data-edit-report-question]').forEach(button => button.addEventListener('click', async () => {
    try {
      const reportId = Number(button.closest<HTMLElement>('[data-report-id]')?.dataset.reportId || 0);
      const question = await api<Question>(`/api/admin/questions/${button.dataset.editReportQuestion}`);
      questionModal(question, question.source_id, async () => {
        if (reportId) await api(`/api/admin/reports/${reportId}`, { method: 'DELETE' });
        toast('Savol tuzatildi va xatolik xabari olib tashlandi');
        refresh();
      });
    } catch (error) {
      toast(error instanceof Error ? error.message : String(error), 'error');
    }
  }));
  document.querySelectorAll<HTMLElement>('[data-delete-report-question]').forEach(button => button.addEventListener('click', async () => {
    if (!confirm('Savol manba ichidan ham o‘chirilsinmi?')) return;
    try {
      const reportId = Number(button.closest<HTMLElement>('[data-report-id]')?.dataset.reportId || 0);
      await api(`/api/admin/questions/${button.dataset.deleteReportQuestion}`, { method: 'DELETE' });
      if (reportId) await api(`/api/admin/reports/${reportId}`, { method: 'DELETE' });
      toast("Savol va xatolik xabari o'chirildi");
      refresh();
    } catch (error) {
      toast(error instanceof Error ? error.message : String(error), 'error');
    }
  }));
  document.querySelectorAll<HTMLElement>('[data-fix-report]').forEach(button => button.addEventListener('click', async () => {
    try {
      await api(`/api/admin/reports/${button.dataset.fixReport}`, { method: 'DELETE' });
      toast("Xatolik xabari olib tashlandi");
      refresh();
    } catch (error) {
      toast(error instanceof Error ? error.message : String(error), 'error');
    }
  }));
  document.querySelectorAll<HTMLElement>('[data-delete-report]').forEach(button => button.addEventListener('click', async () => {
    if (!confirm('Xatolik xabari ro‘yxatdan o‘chirilsinmi?')) return;
    try {
      await api(`/api/admin/reports/${button.dataset.deleteReport}`, { method: 'DELETE' });
      toast('Xabar o‘chirildi');
      refresh();
    } catch (error) {
      toast(error instanceof Error ? error.message : String(error), 'error');
    }
  }));
}

async function showTests(): Promise<void> {
  setTitle('Testlar', 'Test qoidalari va faolligi'); loading();
  try { const tests = await api<TestItem[]>('/api/admin/tests'); if (!sourceCache.length) await loadSources(); document.querySelector('#content')!.innerHTML = `<section class="panel"><div class="panel-title"><div><h2>Testlar</h2><span>${tests.length} ta</span></div><button class="primary compact" id="new-test">+ Yangi test</button></div><div class="table-wrap"><table><thead><tr><th>Nomi</th><th>Savollar</th><th>Vaqt</th><th>Holati</th><th>Ishlangan</th><th>Amallar</th></tr></thead><tbody>${tests.map(t => `<tr><td><strong>${esc(t.name)}</strong><small class="block">${t.rules.map(r => `${esc(r.source_name)}: ${r.question_count}`).join(' · ')}</small></td><td>${t.total_questions}</td><td>${t.time_limit_minutes ? `${t.time_limit_minutes} daq.` : 'Cheksiz'}</td><td><span class="status ${t.is_active ? 'active' : 'inactive'}">${t.is_active ? 'Faol' : 'Nofaol'}</span></td><td>${t.attempt_count}</td><td class="actions"><button data-edit-test="${t.id}">✏️</button><button class="danger-icon" data-delete-test="${t.id}">🗑</button></td></tr>`).join('') || '<tr><td colspan="6" class="empty-cell">Test mavjud emas</td></tr>'}</tbody></table></div></section>`;
    document.querySelector('#new-test')?.addEventListener('click', () => testModal()); document.querySelectorAll<HTMLElement>('[data-edit-test]').forEach(b => b.addEventListener('click', () => testModal(tests.find(t => t.id === Number(b.dataset.editTest))))); document.querySelectorAll<HTMLElement>('[data-delete-test]').forEach(b => b.addEventListener('click', async () => { if (!confirm('Test o‘chirilsinmi? Tarixiy urinishlar bo‘lsa nofaol qilinadi.')) return; try { const r = await api<any>(`/api/admin/tests/${b.dataset.deleteTest}`, { method: 'DELETE' }); toast(r.deactivated ? 'Test nofaol qilindi' : 'Test o‘chirildi'); showTests(); } catch (e) { toast(e instanceof Error ? e.message : String(e), 'error'); } }));
  } catch (error) { showError(error); }
}
async function testModal(test?: TestItem): Promise<void> {
  if (!sourceCache.length) await loadSources(); const rules = test?.rules.length ? test.rules : [{ source_id: sourceCache[0]?.id, question_count: 1 }];
  openModal(`<form id="test-form" class="wide-modal"><h2>${test ? 'Testni tahrirlash' : 'Yangi test'}</h2><div class="form-grid"><label>Test nomi<input name="name" value="${esc(test?.name || '')}" required></label><label>Vaqt (daqiqa)<input name="time_limit_minutes" type="number" min="0" max="1440" value="${test?.time_limit_minutes || 0}"></label></div><label class="switch-row"><input name="is_active" type="checkbox" ${test?.is_active !== false ? 'checked' : ''}><span>Foydalanuvchilarga ko‘rinsin</span></label><div class="field-label">Manbalar va savollar soni</div><div id="rule-editor">${rules.map((r, i) => testRuleRow(r, i)).join('')}</div><button type="button" class="secondary compact" id="add-rule">+ Manba qo‘shish</button><div class="total-line">Jami: <strong id="test-total">0</strong> ta savol</div><div class="form-error" id="test-error"></div><div class="modal-actions"><button type="button" class="ghost" data-close>Bekor qilish</button><button class="primary" type="submit">💾 Saqlash</button></div></form>`);
  const editor = document.querySelector('#rule-editor')!; const recalc = () => document.querySelector('#test-total')!.textContent = String([...editor.querySelectorAll<HTMLInputElement>('input[type=number]')].reduce((s, e) => s + Number(e.value || 0), 0)); const bind = () => { editor.querySelectorAll('input,select').forEach(el => el.addEventListener('input', recalc)); editor.querySelectorAll<HTMLElement>('[data-remove-rule]').forEach(b => b.onclick = () => { if (editor.children.length <= 1) return toast('Kamida bitta manba bo‘lishi kerak', 'error'); b.closest('.rule-row')?.remove(); recalc(); }); }; bind(); recalc();
  document.querySelector('#add-rule')?.addEventListener('click', () => { const div = document.createElement('div'); div.innerHTML = testRuleRow({ source_id: sourceCache[0]?.id, question_count: 1 }, editor.children.length); editor.append(div.firstElementChild!); bind(); recalc(); });
  document.querySelector<HTMLFormElement>('#test-form')!.addEventListener('submit', async e => { e.preventDefault(); const form = new FormData(e.currentTarget as HTMLFormElement); const payload = { name: String(form.get('name')), time_limit_minutes: Number(form.get('time_limit_minutes')), is_active: form.get('is_active') === 'on', rules: [...editor.querySelectorAll<HTMLElement>('.rule-row')].map(row => ({ source_id: Number(row.querySelector('select')!.value), question_count: Number(row.querySelector('input')!.value) })) }; try { await api(`/api/admin/tests${test ? `/${test.id}` : ''}`, { method: test ? 'PUT' : 'POST', body: JSON.stringify(payload) }); closeModal(); toast('✅ Test saqlandi'); showTests(); } catch (error) { document.querySelector('#test-error')!.textContent = error instanceof Error ? error.message : String(error); } });
}
function testRuleRow(rule: TestRule, i: number): string { return `<div class="rule-row"><select>${sourceCache.map(s => `<option value="${s.id}" ${s.id === rule.source_id ? 'selected' : ''}>${esc(s.name)} (${s.question_count})</option>`).join('')}</select><input type="number" min="1" max="500" value="${rule.question_count}" required><span>ta</span><button type="button" class="danger-icon" data-remove-rule>×</button></div>`; }

async function showImport(): Promise<void> {
  setTitle('Import', '.txt, .docx va .db fayllar'); if (!sourceCache.length) await loadSources(); parsedImport = [];
  const query = new URLSearchParams(location.hash.split('?')[1] || ''); const selected = query.get('source') || '';
  document.querySelector('#content')!.innerHTML = `<section class="panel"><div class="panel-title"><div><h2>Savollarni import qilish</h2><span>Avval preview, keyin tasdiqlash</span></div></div><form id="import-form" class="import-form"><div class="form-grid"><label>Manba<select name="source_id"><option value="">Yangi manba yaratiladi</option>${sourceCache.map(s => `<option value="${s.id}" ${String(s.id) === selected ? 'selected' : ''}>${esc(s.name)}</option>`).join('')}</select></label><label>Yangi manba nomi<input name="new_source_name" placeholder="Masalan: TIF 2026"></label></div><label>.db import rejimi<select name="db_mode"><option value="single">Bitta manbaga qo‘shish</option><option value="full">Manbalari bilan to‘liq import</option></select></label><label class="drop-zone" id="drop-zone"><input name="file" type="file" accept=".txt,.docx,.db,.sqlite,.sqlite3" required><span>📄</span><strong>Faylni tanlang yoki shu yerga tashlang</strong><small>Eng ko‘pi 20 MB</small></label><button class="primary" type="submit">🔎 Faylni tahlil qilish</button></form><div id="import-preview"></div></section>`;
  document.querySelector<HTMLInputElement>('input[type=file]')!.addEventListener('change', e => { const f = (e.target as HTMLInputElement).files?.[0]; if (f) document.querySelector('#drop-zone strong')!.textContent = f.name; });
  document.querySelector<HTMLFormElement>('#import-form')!.addEventListener('submit', async e => { e.preventDefault(); const fd = new FormData(e.currentTarget as HTMLFormElement); const sourceId = fd.get('source_id'); if (!sourceId) fd.delete('source_id'); try { document.querySelector('#import-preview')!.innerHTML = '<div class="loading"><div class="spinner"></div><p>Tahlil qilinmoqda…</p></div>'; const result = await api<any>('/api/admin/import/parse', { method: 'POST', body: fd }); parsedImport = result.parsed; renderImportPreview(result.stats, fd); } catch (error) { showError(error, '#import-preview'); } });
}
function renderImportPreview(stats: any, formData: FormData): void {
  const preview = document.querySelector('#import-preview')!;
  preview.innerHTML = `<div class="import-stats"><span>Jami <strong>${stats.total}</strong></span><span class="good">Yaroqli <strong>${stats.valid}</strong></span><span class="bad">Muammoli <strong>${stats.problematic}</strong></span><span>Takroriy <strong>${stats.duplicates_in_database + stats.duplicates_in_file}</strong></span></div><div class="preview-actions"><label><input id="skip-duplicates" type="checkbox" checked> Takroriylarni tashlab ketish</label><button class="primary compact" id="commit-import">✅ Importni tasdiqlash</button></div><div id="import-progress"></div><div class="preview-list">${parsedImport.map((q, i) => `<article class="preview-item ${q.valid ? '' : 'invalid'}"><div><strong>${i + 1}. ${esc(q.question)}</strong><small>${q.source_name ? `[${esc(q.source_name)}] · ` : ''}${q.answers.length} ta variant · ${q.answers.find(a => a.correct) ? `To‘g‘ri: ${esc(q.answers.find(a => a.correct)!.text)}` : 'To‘g‘ri javob yo‘q'}</small>${q.problems.length ? `<em>${esc(q.problems.join('; '))}</em>` : ''}${q.duplicate_in_database || q.duplicate_in_file ? '<em>Takroriy savol</em>' : ''}</div><span>${q.valid ? '✅' : '⚠️'}</span></article>`).join('')}</div>`;
  document.querySelector('#commit-import')?.addEventListener('click', () => commitImportWithProgress(formData));
}
function renderImportProgress(done: number, total: number, added: number, skipped: number, active = true): void {
  const percent = total ? Math.round(done * 100 / total) : 100;
  const target = document.querySelector('#import-progress');
  if (!target) return;
  target.innerHTML = `<div class="import-progress-card ${active ? 'active' : 'done'}"><div class="progress-orbit"><div class="progress-ring" style="--progress:${percent}"><strong>${percent}%</strong></div></div><div class="progress-copy"><h3>${active ? 'Savollar yuklanmoqda' : 'Import yakunlandi'}</h3><p>${done}/${total} ta savol qayta ishlandi</p><div class="progress-bar"><span style="width:${percent}%"></span></div><small>Qo‘shildi: ${added} ta · O‘tkazildi: ${skipped} ta</small></div></div>`;
}
async function commitImportWithProgress(formData: FormData): Promise<void> {
  if (!parsedImport.length) return toast('Import qilinadigan savol yo‘q', 'error');
  const button = document.querySelector<HTMLButtonElement>('#commit-import');
  const skipInput = document.querySelector<HTMLInputElement>('#skip-duplicates')!;
  const sourceId = formData.get('source_id');
  const dbMode = formData.get('db_mode');
  const newName = String(formData.get('new_source_name') || '');
  const chunkSize = 100;
  let added = 0;
  let skipped = 0;
  let processed = 0;
  if (button) { button.disabled = true; button.textContent = 'Import qilinmoqda...'; }
  skipInput.disabled = true;
  renderImportProgress(0, parsedImport.length, 0, 0);
  try {
    for (let index = 0; index < parsedImport.length; index += chunkSize) {
      const chunk = parsedImport.slice(index, index + chunkSize);
      const payload = {
        source_id: sourceId ? Number(sourceId) : null,
        new_source_name: newName || null,
        create_sources_from_file: dbMode === 'full',
        skip_duplicates: skipInput.checked,
        questions: chunk,
      };
      const result = await api<any>('/api/admin/import/commit', { method: 'POST', body: JSON.stringify(payload) });
      added += Number(result.added || 0);
      skipped += Number(result.skipped || 0);
      processed += chunk.length;
      renderImportProgress(processed, parsedImport.length, added, skipped);
      await new Promise(resolve => window.setTimeout(resolve, 80));
    }
    renderImportProgress(parsedImport.length, parsedImport.length, added, skipped, false);
    toast(`✅ ${added} ta qo‘shildi, ${skipped} ta o‘tkazib yuborildi`);
    window.setTimeout(() => showImport(), 900);
  } catch (error) {
    toast(error instanceof Error ? error.message : String(error), 'error');
    if (button) { button.disabled = false; button.textContent = '✅ Importni tasdiqlash'; }
    skipInput.disabled = false;
  }
}
function openModal(content: string): void { modalRoot.innerHTML = `<div class="modal-backdrop"><div class="modal">${content}</div></div>`; modalRoot.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', closeModal)); modalRoot.querySelector('.modal-backdrop')?.addEventListener('click', e => { if (e.target === e.currentTarget) closeModal(); }); }
function closeModal(): void { modalRoot.innerHTML = ''; }
function showError(error: unknown, selector = '#content'): void { const message = error instanceof Error ? error.message : String(error); document.querySelector(selector)!.innerHTML = `<div class="error-box"><strong>⚠️ Xatolik</strong><p>${esc(message)}</p><button class="ghost compact" onclick="location.reload()">Qayta yuklash</button></div>`; }

if (token) showShell(); else showLogin();



