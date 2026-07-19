import './style.css';

declare global {
  interface Window {
    Telegram?: {
      WebApp: {
        initData: string;
        initDataUnsafe?: { user?: { id: number; first_name?: string; last_name?: string; username?: string } };
        colorScheme: 'light' | 'dark';
        themeParams: Record<string, string>;
        ready(): void;
        expand(): void;
        close(): void;
        BackButton: { show(): void; hide(): void; onClick(cb: () => void): void; offClick(cb: () => void): void };
        HapticFeedback?: { notificationOccurred(type: 'success' | 'error' | 'warning'): void };
        openTelegramLink?(url: string): void;
      };
    };
  }
}

type TestItem = { id: number; name: string; time_limit_minutes: number; total_questions: number };
type Answer = { id: number; text: string; correct?: boolean };
type AttemptQuestion = {
  id: number;
  question_id: number;
  order_index: number;
  question_text: string;
  answers: Answer[];
  selected_answer_id: number | null;
  is_correct: boolean | null;
};
type Attempt = {
  id: number;
  test_id: number;
  test_name: string;
  started_at: string;
  finished_at: string | null;
  total_questions: number;
  correct_count: number;
  time_limit_minutes: number;
  remaining_seconds: number | null;
  questions: AttemptQuestion[];
};
type Result = {
  attempt_id: number; test_name: string; total: number; answered: number; correct: number;
  incorrect: number; unanswered: number; percentage: number; spent_seconds: number;
};

const app = document.querySelector<HTMLDivElement>('#app')!;
const modalRoot = document.querySelector<HTMLDivElement>('#modal-root')!;
const toastElement = document.querySelector<HTMLDivElement>('#toast')!;
const tg = window.Telegram?.WebApp;
let token = sessionStorage.getItem('user_token') || '';
let currentAttempt: Attempt | null = null;
let currentIndex = 0;
let timerId: number | null = null;
let backHandler: (() => void) | null = null;

function escapeHtml(value: unknown): string {
  return String(value ?? '').replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]!));
}

function toast(message: string): void {
  toastElement.textContent = message;
  toastElement.classList.add('show');
  window.setTimeout(() => toastElement.classList.remove('show'), 3000);
}

function setBack(handler?: () => void): void {
  if (!tg) return;
  if (backHandler) tg.BackButton.offClick(backHandler);
  backHandler = handler || null;
  if (handler) {
    tg.BackButton.show();
    tg.BackButton.onClick(handler);
  } else {
    tg.BackButton.hide();
  }
}

async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers || {});
  if (!(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  if (token) headers.set('Authorization', `Bearer ${token}`);
  const response = await fetch(path, { ...options, headers });
  const data = await response.json().catch(() => ({ detail: 'Server javobi notoвЂgвЂri' }));
  if (!response.ok) {
    const detail = typeof data.detail === 'string' ? data.detail : (data.detail?.message || JSON.stringify(data.detail));
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return data as T;
}

function applyTelegramTheme(): void {
  tg?.ready();
  tg?.expand();
  document.documentElement.dataset.theme = tg?.colorScheme || 'light';
  const params = tg?.themeParams || {};
  const map: Record<string, string> = {
    bg_color: '--tg-bg', text_color: '--tg-text', hint_color: '--tg-muted',
    button_color: '--tg-button', button_text_color: '--tg-button-text', secondary_bg_color: '--tg-card',
  };
  Object.entries(map).forEach(([key, css]) => { if (params[key]) document.documentElement.style.setProperty(css, params[key]); });
}

async function waitForTelegramInitData(): Promise<string> {
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const initData = window.Telegram?.WebApp?.initData || '';
    if (initData) return initData;
    await new Promise(resolve => window.setTimeout(resolve, 100));
  }
  return '';
}

async function authenticate(): Promise<void> {
  if (token) {
    try { await api('/api/me'); return; } catch { token = ''; sessionStorage.removeItem('user_token'); }
  }
  const initData = await waitForTelegramInitData();
  if (!initData) {
    const params = new URLSearchParams(location.search);
    const webappToken = params.get('tg_login') || '';
    if (webappToken) {
      const result = await api<{ access_token: string }>('/api/auth/telegram', { method: 'POST', body: JSON.stringify({ webapp_token: webappToken }) });
      token = result.access_token; sessionStorage.setItem('user_token', token);
      history.replaceState(null, '', `${location.pathname}${location.hash}`);
      return;
    }
    if (params.get('dev') === '1') {
      const result = await api<{ access_token: string }>('/api/auth/dev', { method: 'POST', body: JSON.stringify({}) });
      token = result.access_token; sessionStorage.setItem('user_token', token); return;
    }
    throw new Error("Telegram initData kelmadi. Mini App'ni botdagi 'Testlarni boshlash' tugmasi yoki BotFather Menu Button orqali oching; oddiy link sifatida ochilsa foydalanuvchi aniqlanmaydi.");
  }
  const result = await api<{ access_token: string }>('/api/auth/telegram', {
    method: 'POST', body: JSON.stringify({ init_data: initData }),
  });
  token = result.access_token;
  sessionStorage.setItem('user_token', token);
}

function loading(text = 'YuklanmoqdaвЂ¦'): void {
  app.innerHTML = `<div class="splash"><div class="spinner"></div><p>${escapeHtml(text)}</p></div>`;
}

function showFatal(error: unknown): void {
  const message = error instanceof Error ? error.message : String(error);
  app.innerHTML = `<main class="page center-page"><div class="error-card"><div class="big-icon">вљ пёЏ</div><h1>Kirish amalga oshmadi</h1><p>${escapeHtml(message)}</p><button class="primary" id="retry">Qayta urinish</button></div></main>`;
  document.querySelector('#retry')?.addEventListener('click', () => location.reload());
}

function modal(title: string, body: string, confirmText = 'Tasdiqlash'): Promise<boolean> {
  return new Promise((resolve) => {
    modalRoot.innerHTML = `<div class="modal-backdrop"><div class="modal"><h3>${escapeHtml(title)}</h3><div class="modal-body">${body}</div><div class="modal-actions"><button class="ghost" data-action="cancel">Bekor qilish</button><button class="primary" data-action="confirm">${escapeHtml(confirmText)}</button></div></div></div>`;
    const close = (value: boolean) => { modalRoot.innerHTML = ''; resolve(value); };
    modalRoot.querySelector('[data-action="cancel"]')?.addEventListener('click', () => close(false));
    modalRoot.querySelector('[data-action="confirm"]')?.addEventListener('click', () => close(true));
    modalRoot.querySelector('.modal-backdrop')?.addEventListener('click', (event) => { if (event.target === event.currentTarget) close(false); });
  });
}

async function showHome(): Promise<void> {
  clearTimer(); setBack(); loading();
  try {
    const [me, tests, active] = await Promise.all([
      api<{ full_name: string; stats: { count: number; average: number; best_percentage: number } }>('/api/me'),
      api<TestItem[]>('/api/tests'),
      api<Attempt | null>('/api/attempts/active'),
    ]);
    app.innerHTML = `<main class="page home-page">
      <header class="welcome"><div><p class="eyebrow">Bugungi testlar</p><h1>Salom, ${escapeHtml(me.full_name)}! рџ‘‹</h1><p>Bilimingizni sinab koвЂring va natijalaringizni yaxshilang.</p></div><div class="avatar">${escapeHtml(me.full_name.charAt(0).toUpperCase())}</div></header>
      <section class="mini-stats"><div><strong>${me.stats.count}</strong><span>Ishlangan</span></div><div><strong>${me.stats.average}%</strong><span>OвЂrtacha</span></div><div><strong>${me.stats.best_percentage}%</strong><span>Eng yaxshi</span></div></section>
      ${active ? `<section class="resume-card"><div><span class="badge warning">Tugallanmagan</span><h2>${escapeHtml(active.test_name)}</h2><p>${active.questions.filter(q => q.selected_answer_id !== null).length}/${active.total_questions} ta savolga javob berilgan</p></div><button class="primary" id="resume">Davom ettirish</button></section>` : ''}
      <section><div class="section-title"><h2>Faol testlar</h2><span>${tests.length} ta</span></div><div class="test-grid">${tests.length ? tests.map(test => `
        <article class="test-card" data-test-id="${test.id}"><div class="test-icon">рџ“љ</div><div class="test-info"><h3>${escapeHtml(test.name)}</h3><p>рџ”ў ${test.total_questions} ta savol ${test.time_limit_minutes ? ` В· вЏ± ${test.time_limit_minutes} daqiqa` : ' В· вЏ± Cheksiz'}</p></div><button class="circle-button" aria-label="Boshlash">вЂє</button></article>`).join('') : '<div class="empty">Hozircha faol test mavjud emas.</div>'}</div></section>
    </main>`;
    document.querySelector('#resume')?.addEventListener('click', () => openAttempt(active!.id));
    document.querySelectorAll<HTMLElement>('[data-test-id]').forEach(card => card.addEventListener('click', () => startTest(Number(card.dataset.testId), tests.find(t => t.id === Number(card.dataset.testId))!)));
  } catch (error) { showFatal(error); }
}

async function startTest(testId: number, test: TestItem): Promise<void> {
  const accepted = await modal('Testni boshlaysizmi?', `<p><strong>${escapeHtml(test.name)}</strong></p><p>${test.total_questions} ta savol${test.time_limit_minutes ? `, ${test.time_limit_minutes} daqiqa` : ''}.</p>`, 'Boshlash');
  if (!accepted) return;
  loading('Test tayyorlanmoqdaвЂ¦');
  try {
    currentAttempt = await api<Attempt>('/api/attempts', { method: 'POST', body: JSON.stringify({ test_id: testId }) });
    currentIndex = 0; renderAttempt();
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    toast(message); await showHome();
  }
}

async function openAttempt(attemptId: number): Promise<void> {
  loading('Test yuklanmoqdaвЂ¦');
  try { currentAttempt = await api<Attempt>(`/api/attempts/${attemptId}`); currentIndex = 0; renderAttempt(); }
  catch (error) { toast(error instanceof Error ? error.message : String(error)); await showHome(); }
}

function clearTimer(): void { if (timerId !== null) { window.clearInterval(timerId); timerId = null; } }
function formatTime(seconds: number): string { const m = Math.floor(seconds / 60); const s = seconds % 60; return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`; }

function startTimer(initial: number | null): void {
  clearTimer(); if (initial === null || !currentAttempt) return;
  let remaining = initial;
  const update = () => {
    const element = document.querySelector<HTMLElement>('#timer');
    if (!element) return;
    element.textContent = formatTime(Math.max(0, remaining));
    element.classList.toggle('danger', remaining <= 300);
    if (remaining <= 0) { clearTimer(); void finishTest(true); }
    remaining -= 1;
  };
  update(); timerId = window.setInterval(update, 1000);
}

function renderAttempt(): void {
  if (!currentAttempt) return;
  setBack(() => void confirmLeave());
  const question = currentAttempt.questions[currentIndex];
  const answered = currentAttempt.questions.filter(q => q.selected_answer_id !== null).length;
  app.innerHTML = `<main class="test-page">
    <header class="test-header"><div><span class="eyebrow">Test</span><h1>${escapeHtml(currentAttempt.test_name)}</h1></div>${currentAttempt.remaining_seconds !== null ? `<div class="timer" id="timer">${formatTime(currentAttempt.remaining_seconds)}</div>` : '<div class="timer">в€ћ</div>'}</header>
    <nav class="question-nav" id="question-nav">${currentAttempt.questions.map((item, index) => `<button class="q-dot ${index === currentIndex ? 'current' : ''} ${item.is_correct === true ? 'correct' : item.is_correct === false ? 'wrong' : ''}" data-index="${index}">${index + 1}</button>`).join('')}</nav>
    <section class="question-wrap"><div class="progress-line"><span>${question.order_index}-savol / ${currentAttempt.total_questions}</span><span>${answered} ta javob</span></div><article class="question-card"><h2>${escapeHtml(question.question_text)}</h2><div class="answers">${question.answers.map((answer, index) => {
      let cls = '';
      if (question.selected_answer_id !== null) {
        if (answer.correct) cls = 'correct';
        else if (answer.id === question.selected_answer_id) cls = 'wrong';
      }
      return `<button class="answer ${cls}" data-answer-id="${answer.id}" ${question.selected_answer_id !== null ? 'disabled' : ''}><span>${String.fromCharCode(65 + index)}</span><b>${escapeHtml(answer.text)}</b></button>`;
    }).join('')}</div><button class="report-question" id="report-question" type="button">Savolda muammo bor</button></article><div class="pager"><button class="ghost" id="prev" ${currentIndex === 0 ? 'disabled' : ''}>в—Ђ Oldingi</button><button class="ghost" id="next" ${currentIndex === currentAttempt.questions.length - 1 ? 'disabled' : ''}>Keyingi в–¶</button></div></section>
    <footer class="test-footer"><button class="finish" id="finish">рџЏЃ Testni yakunlash</button></footer>
  </main>`;
  startTimer(currentAttempt.remaining_seconds);
  document.querySelectorAll<HTMLElement>('[data-index]').forEach(button => button.addEventListener('click', () => { currentIndex = Number(button.dataset.index); renderAttempt(); }));
  document.querySelectorAll<HTMLButtonElement>('[data-answer-id]').forEach(button => button.addEventListener('click', () => answerCurrent(Number(button.dataset.answerId))));
  document.querySelector('#report-question')?.addEventListener('click', () => reportCurrentQuestion(question));
  document.querySelector('#prev')?.addEventListener('click', () => { currentIndex--; renderAttempt(); });
  document.querySelector('#next')?.addEventListener('click', () => { currentIndex++; renderAttempt(); });
  document.querySelector('#finish')?.addEventListener('click', () => finishTest(false));
  window.setTimeout(() => document.querySelector('.q-dot.current')?.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' }), 50);
}

function reportCurrentQuestion(question: AttemptQuestion): void {
  if (!currentAttempt) return;
  modalRoot.innerHTML = `<div class="modal-backdrop"><form class="modal report-modal" id="question-report-form"><h3>Savoldagi muammo</h3><div class="modal-body"><p>Xatolik bo'yicha izohingizni yozing.</p><textarea name="message" rows="5" minlength="3" maxlength="2000" placeholder="Masalan: to'g'ri javob noto'g'ri belgilangan..." required></textarea><div class="form-error" id="report-error"></div></div><div class="modal-actions"><button class="ghost" type="button" data-action="cancel">Bekor qilish</button><button class="primary" type="submit">Yuborish</button></div></form></div>`;
  const close = () => { modalRoot.innerHTML = ''; };
  modalRoot.querySelector('[data-action="cancel"]')?.addEventListener('click', close);
  modalRoot.querySelector('.modal-backdrop')?.addEventListener('click', event => { if (event.target === event.currentTarget) close(); });
  modalRoot.querySelector<HTMLFormElement>('#question-report-form')!.addEventListener('submit', async event => {
    event.preventDefault();
    const form = new FormData(event.currentTarget as HTMLFormElement);
    const message = String(form.get('message') || '').trim();
    const button = (event.currentTarget as HTMLFormElement).querySelector<HTMLButtonElement>('button[type=submit]')!;
    if (message.length < 3) {
      document.querySelector('#report-error')!.textContent = "Izoh kamida 3 ta belgidan iborat bo'lishi kerak";
      return;
    }
    button.disabled = true;
    try {
      await api(`/api/questions/${question.question_id}/reports`, {
        method: 'POST',
        body: JSON.stringify({ message, attempt_id: currentAttempt?.id }),
      });
      close();
      toast('Xabaringiz adminga yuborildi');
      tg?.HapticFeedback?.notificationOccurred('success');
    } catch (error) {
      document.querySelector('#report-error')!.textContent = error instanceof Error ? error.message : String(error);
      button.disabled = false;
      tg?.HapticFeedback?.notificationOccurred('error');
    }
  });
}

async function answerCurrent(answerId: number): Promise<void> {
  if (!currentAttempt) return;
  const question = currentAttempt.questions[currentIndex];
  if (question.selected_answer_id !== null) return;
  document.querySelectorAll<HTMLButtonElement>('.answer').forEach(btn => btn.disabled = true);
  try {
    const result = await api<{ is_correct: boolean; correct_answer_id: number; selected_answer_id: number }>(`/api/attempts/${currentAttempt.id}/answer`, {
      method: 'POST', body: JSON.stringify({ question_id: question.question_id, answer_id: answerId }),
    });
    question.selected_answer_id = result.selected_answer_id;
    question.is_correct = result.is_correct;
    question.answers = question.answers.map(answer => ({ ...answer, correct: answer.id === result.correct_answer_id }));
    tg?.HapticFeedback?.notificationOccurred(result.is_correct ? 'success' : 'error');
    renderAttempt();
    window.setTimeout(() => {
      if (!currentAttempt) return;
      const next = currentAttempt.questions.findIndex((item, index) => index > currentIndex && item.selected_answer_id === null);
      const wrap = currentAttempt.questions.findIndex(item => item.selected_answer_id === null);
      const destination = next >= 0 ? next : wrap;
      if (destination >= 0) { currentIndex = destination; renderAttempt(); }
    }, 800);
  } catch (error) { toast(error instanceof Error ? error.message : String(error)); renderAttempt(); }
}

async function confirmLeave(): Promise<void> {
  const accepted = await modal('Testdan chiqasizmi?', '<p>Javoblaringiz saqlanadi va keyin davom ettirishingiz mumkin.</p>', 'Chiqish');
  if (accepted) await showHome();
}

async function finishTest(auto: boolean): Promise<void> {
  if (!currentAttempt) return;
  const answered = currentAttempt.questions.filter(q => q.selected_answer_id !== null).length;
  if (!auto) {
    const accepted = await modal('Testni yakunlaysizmi?', `<p>вњ… Javob berilgan: <strong>${answered}</strong> ta</p><p>в¬њ Javob berilmagan: <strong>${currentAttempt.total_questions - answered}</strong> ta</p>`, 'Ha, yakunlash');
    if (!accepted) return;
  }
  clearTimer(); loading(auto ? 'Vaqt tugadi. Natija hisoblanmoqdaвЂ¦' : 'Natija hisoblanmoqdaвЂ¦');
  try { const result = await api<Result>(`/api/attempts/${currentAttempt.id}/finish`, { method: 'POST' }); showResult(result); }
  catch (error) { toast(error instanceof Error ? error.message : String(error)); renderAttempt(); }
}

function evaluation(percentage: number): string {
  if (percentage >= 90) return 'Ajoyib natija! рџЏ†';
  if (percentage >= 70) return 'Yaxshi natija! рџ‘Џ';
  if (percentage >= 50) return 'Yomon emas! рџ’Є';
  return 'KoвЂproq mashq qiling рџ“љ';
}

function showResult(result: Result): void {
  setBack(() => void showHome());
  const minutes = Math.floor(result.spent_seconds / 60); const seconds = result.spent_seconds % 60;
  app.innerHTML = `<main class="page result-page"><section class="result-card"><div class="result-emoji">${result.percentage >= 70 ? 'рџЋ‰' : 'рџ’Є'}</div><h1>${escapeHtml(result.test_name)}</h1><div class="score-ring" style="--score:${result.percentage}"><div><strong>${result.percentage}%</strong><span>natija</span></div></div><h2>${evaluation(result.percentage)}</h2><div class="result-stats"><div><span>вњ…</span><strong>${result.correct}</strong><small>ToвЂgвЂri</small></div><div><span>вќЊ</span><strong>${result.incorrect}</strong><small>NotoвЂgвЂri</small></div><div><span>в¬њ</span><strong>${result.unanswered}</strong><small>Javobsiz</small></div><div><span>вЏ±</span><strong>${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}</strong><small>Vaqt</small></div></div><div class="result-actions"><button class="secondary" id="review">рџ“‹ Javoblarni koвЂrish</button><button class="primary" id="retry-test">рџ”„ Qayta ishlash</button><button class="ghost" id="home">рџЏ  Bosh sahifa</button></div></section></main>`;
  document.querySelector('#review')?.addEventListener('click', () => showReview(result.attempt_id));
  document.querySelector('#retry-test')?.addEventListener('click', async () => {
    if (!currentAttempt) return; const tests = await api<TestItem[]>('/api/tests'); const test = tests.find(item => item.id === currentAttempt!.test_id); if (test) await startTest(test.id, test);
  });
  document.querySelector('#home')?.addEventListener('click', () => showHome());
}

async function showReview(attemptId: number): Promise<void> {
  loading('Javoblar yuklanmoqdaвЂ¦');
  try {
    const review = await api<{ questions: Array<{ order_index: number; question_text: string; answers: Answer[]; selected_answer_id: number | null; correct_answer_id: number; is_correct: boolean | null }> }>(`/api/attempts/${attemptId}/review`);
    setBack(() => currentAttempt && openAttempt(currentAttempt.id));
    app.innerHTML = `<main class="page review-page"><header class="section-title"><h1>Javoblar tahlili</h1><span>${review.questions.length} ta</span></header><div class="review-list">${review.questions.map(item => `<article class="review-card ${item.is_correct ? 'ok' : 'bad'}"><div class="review-number">${item.order_index}</div><h3>${escapeHtml(item.question_text)}</h3>${item.answers.map(answer => `<div class="review-answer ${answer.correct ? 'correct' : answer.id === item.selected_answer_id ? 'wrong' : ''}">${answer.correct ? 'вњ“' : answer.id === item.selected_answer_id ? 'вњ•' : 'вЂў'} ${escapeHtml(answer.text)}</div>`).join('')}</article>`).join('')}</div><button class="primary wide" id="review-home">Bosh sahifaga qaytish</button></main>`;
    document.querySelector('#review-home')?.addEventListener('click', () => showHome());
  } catch (error) { toast(error instanceof Error ? error.message : String(error)); await showHome(); }
}

async function bootstrap(): Promise<void> {
  applyTelegramTheme();
  try { await authenticate(); await showHome(); }
  catch (error) { showFatal(error); }
}

void bootstrap();
