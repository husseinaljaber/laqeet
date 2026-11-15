/* لقيت: The complete frontend logic. Direct API integration under /api */
(function () {
  'use strict';
  const API = (window.LAQEET_API || '') + '/api';
  const PAGE = 30;
  const PROFILE_KEY = 'laqeet.profile';
  const SAVED_KEY = 'laqeet.saved';
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const LEVELS = [
    ['none', 'بدون شهادة'],
    ['basic', 'تعليم أساسي (إعدادي)'],
    ['secondary', 'بكالوريا (ثانوي)'],
    ['institute', 'معهد تقاني أو متوسط'],
    ['bachelor', 'إجازة جامعية (بكالوريوس)'],
    ['master', 'ماجستير / دراسات عليا'],
    ['phd', 'دكتوراه'],
  ];
  const EDU_LABEL = Object.fromEntries(LEVELS);
  const EDU_RANK = Object.fromEntries(LEVELS.map(([c], i) => [c, i]));

  const SECTOR_UI = {
    gov: { label: '🏛️ قطاع حكومي', badge: 'رسمي ✓ حكومي' },
    ngo: { label: '🤝 منظمات وهيئات', badge: 'منظمة إنسانية' },
    private: { label: '🏢 قطاع خاص', badge: 'قطاع خاص' }
  };

  const APPLY_UI = {
    whatsapp: '💬 واتساب',
    form: '📝 فورم تقديم',
    site: '🌐 موقع التوظيف',
    email: '📧 إيميل',
    call: '📞 اتصال هاتفي'
  };

  let META = null, STATS = null;
  let profile = loadProfile();
  let savedMap = {};
  try { savedMap = JSON.parse(localStorage.getItem(SAVED_KEY) || '{}') || {}; } catch (e) {}

  const state = {
    view: 'landing',
    country: 'sy',
    sector: '',
    edu_group: '',
    edu: '',
    lower: true,
    source: '',
    apply: '',
    field: '',
    sub: '',
    city: '',
    q: '',
    sort: 'relevance',
    terms: [],
    fresh: false,
    saved: false,
    offset: 0,
    total: 0
  };

  // ---------------------------------------------------------------- helpers
  function $(id) { return document.getElementById(id); }
  function el(tag, attrs, ...kids) {
    const e = document.createElement(tag);
    for (const k in attrs || {}) {
      if (k === 'class') e.className = attrs[k];
      else if (k === 'style') e.setAttribute('style', attrs[k]);
      else if (k.startsWith('on')) e.addEventListener(k.slice(2), attrs[k]);
      else if (attrs[k] != null && attrs[k] !== false) e.setAttribute(k, attrs[k]);
    }
    kids.flat(Infinity).forEach((c) => { if (c != null && c !== false) e.append(c); });
    return e;
  }

  function loadProfile() {
    try { return JSON.parse(localStorage.getItem(PROFILE_KEY)) || {}; }
    catch (e) { return {}; }
  }
  function saveProfile() {
    try { localStorage.setItem(PROFILE_KEY, JSON.stringify(profile)); } catch (e) {}
  }
  function saveSaved() {
    try { localStorage.setItem(SAVED_KEY, JSON.stringify(savedMap)); } catch (e) {}
  }

  async function get(path) {
    const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
    const tid = ctrl ? setTimeout(() => ctrl.abort(), 16000) : null;
    try {
      const r = await fetch(API + path, ctrl ? { signal: ctrl.signal } : undefined);
      if (!r.ok) throw new Error(r.status);
      return await r.json();
    } finally {
      if (tid) clearTimeout(tid);
    }
  }

  async function getWithRetry(path, tries = 4, delayMs = 2000) {
    let lastErr;
    for (let i = 0; i < tries; i++) {
      try {
        return await get(path);
      } catch (e) {
        lastErr = e;
        if (i + 1 < tries) {
          await new Promise((r) => setTimeout(r, delayMs * (i + 1)));
        }
      }
    }
    throw lastErr;
  }

  function toast(msg) {
    const t = $('toast');
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { t.hidden = true; }, 2600);
  }

  const parseDay = (s) => {
    const [y, m, d] = String(s).slice(0, 10).split('-').map(Number);
    return new Date(y, m - 1, d);
  };
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const daysLeft = (s) => Math.round((parseDay(s) - today) / 864e5);

  function ago(iso) {
    if (!iso) return '';
    const h = Math.round((Date.now() - new Date(iso)) / 36e5);
    if (h < 1) return 'الآن';
    if (h < 24) return h === 1 ? 'قبل ساعة' : h === 2 ? 'قبل ساعتين' : `قبل ${h} ساعات`;
    const d = Math.round(h / 24);
    return d === 1 ? 'أمس' : d === 2 ? 'قبل يومين' : d <= 10 ? `قبل ${d} أيام` : `قبل ${d} يوماً`;
  }

  const expBand = (e) => e == null ? 'unk' : e <= 2 ? 'low' : e < 5 ? 'mid' : 'high';
  const expText = (e) => e == null ? 'الخبرة غير مشروطة' : e === 0 ? 'بدون خبرة' : e === 1 ? 'سنة خبرة واحدة' : e === 2 ? 'سنتان خبرة' : e <= 10 ? e + ' سنوات خبرة' : e + ' سنة خبرة';
  const fieldLabel = (f) => (META && (META.fields.find((x) => x.code === f) || {}).label) || f;
  const subLabel = (f) => (META && (META.subfields.find((x) => x.code === f) || {}).label) || f;
  const isMobile = (p) => /^09\d{8}$/.test(p) || /^\+?905\d{9}$/.test(p) || /^05\d{9}$/.test(p);
  const intlPhone = (p) => {
    const clean = String(p || '').replace(/[\s-]/g, '');
    if (clean.startsWith('+90')) return clean.slice(1);
    if (clean.startsWith('905')) return clean;
    if (clean.startsWith('05') && clean.length === 11) return '9' + clean;
    return '963' + clean.replace(/^0/, '');
  };

  // ---------------------------------------------------------------- «بتنطبق عليك؟» (Match)
  function fit(j) {
    if (!profile || (!profile.edu && profile.exp == null && !profile.age && !profile.gender)) {
      return null;
    }
    const why = [];
    let level = 'ok';
    const worse = (l) => { if (l === 'no' || level === 'ok') level = l; };

    if (profile.edu && j.education) {
      if (EDU_RANK[j.education] > EDU_RANK[profile.edu]) {
        worse('no');
        why.push('تطلب ' + (EDU_LABEL[j.education] || j.education));
      }
    }
    if (profile.age && (j.age_min || j.age_max)) {
      if ((j.age_min && profile.age < j.age_min) || (j.age_max && profile.age > j.age_max)) {
        worse('no');
        why.push(`العمر ${j.age_min || ''}–${j.age_max || ''}`);
      }
    }
    if (profile.exp != null && profile.exp !== '' && j.experience != null && j.experience > profile.exp) {
      worse(j.experience - profile.exp >= 2 ? 'no' : 'maybe');
      why.push('تطلب ' + expText(j.experience));
    }
    if (profile.gender && j.gender && j.gender !== profile.gender) {
      worse('no');
      why.push(j.gender === 'female' ? 'مخصصة للإناث' : 'مخصصة للذكور');
    }

    const text = level === 'ok' ? 'مؤهلاتك تنطبق تماماً ✓' : level === 'maybe' ? 'تنطبق جزئياً: ' + why.join('، ') : 'لا تنطبق: ' + why.join('، ');
    return { level, text };
  }

  // ---------------------------------------------------------------- cards & links
  function waLink(j) {
    const p = (j.phones || []).find(isMobile);
    if (p) {
      const src = (j.sources && j.sources[0] && j.sources[0].label) || 'لقيت';
      const msg = `مرحباً، أستفسر عن شاغر «${j.title}» المنشور عبر منصة ${src}. هل الشاغر متاح حالياً؟`;
      return `https://wa.me/${intlPhone(p)}?text=${encodeURIComponent(msg)}`;
    }
    if (j.apply_url && /^https?:\/\/wa\.me\//i.test(j.apply_url)) return j.apply_url;
    return null;
  }

  function foldHighlightChar(ch) {
    if ('أإآ'.includes(ch)) return 'ا';
    if (ch === 'ة') return 'ه';
    if (ch === 'ى') return 'ي';
    if (ch === 'ؤ') return 'و';
    if (ch === 'ئ') return 'ي';
    return ch.toLowerCase();
  }

  function highlightNodes(text, terms) {
    const src = String(text || '');
    if (!src || !terms || !terms.length || !state.q) return [src];
    let folded = '';
    const mapIdx = [];
    for (let i = 0; i < src.length; i++) {
      const ch = src[i];
      if (/[\u064B-\u065F\u0670\u0640]/.test(ch)) continue;
      folded += foldHighlightChar(ch);
      mapIdx.push(i);
    }
    const ranges = [];
    for (const rawTerm of terms) {
      const term = rawTerm.trim().toLowerCase();
      if (term.length < 2) continue;
      let idx = folded.indexOf(term);
      while (idx !== -1) {
        const before = idx > 0 ? folded[idx - 1] : '';
        const after = idx + term.length < folded.length ? folded[idx + term.length] : '';
        const isShortOrAscii = /[a-z0-9]/.test(term) || term.length <= 3;
        const okBefore = !before || !/[a-z0-9\u0621-\u064A]/i.test(before) || (!isShortOrAscii && folded.slice(Math.max(0, idx - 2), idx) === 'ال');
        const okAfter = !after || !/[a-z0-9\u0621-\u064A]/i.test(after) || !isShortOrAscii;
        if (okBefore && okAfter) {
          ranges.push([mapIdx[idx], mapIdx[idx + term.length - 1] + 1]);
        }
        idx = folded.indexOf(term, idx + 1);
      }
    }
    if (!ranges.length) return [src];
    ranges.sort((a, b) => a[0] - b[0]);
    const merged = [];
    for (const r of ranges) {
      if (!merged.length || r[0] > merged[merged.length - 1][1]) merged.push([...r]);
      else merged[merged.length - 1][1] = Math.max(merged[merged.length - 1][1], r[1]);
    }
    const nodes = [];
    let pos = 0;
    for (const [s, e] of merged) {
      if (s > pos) nodes.push(src.slice(pos, s));
      nodes.push(el('mark', { class: 'hl' }, src.slice(s, e)));
      pos = e;
    }
    if (pos < src.length) nodes.push(src.slice(pos));
    return nodes;
  }

  function splitCsv(val) {
    return (val || '').split(',').map((s) => s.trim()).filter(Boolean);
  }
  function joinCsv(arr) {
    return [...new Set(arr.filter(Boolean))].join(',');
  }
  function toggleCsvItem(val, item) {
    const arr = splitCsv(val);
    const idx = arr.indexOf(item);
    if (idx >= 0) arr.splice(idx, 1);
    else arr.push(item);
    return joinCsv(arr);
  }
  function removeCsvItem(val, item) {
    return joinCsv(splitCsv(val).filter((x) => x !== item));
  }

  function formatCardCities(j) {
    const cs = Array.isArray(j.cities) ? [...j.cities] : [];
    if (!cs.length) return 'سوريا (عام)';
    const selCities = splitCsv(state.city);
    if (selCities.length) {
      const matched = cs.filter((c) => selCities.includes(c));
      if (matched.length) {
        const rest = cs.filter((c) => !selCities.includes(c));
        return rest.length ? `${matched.join('، ')} (+${rest.length} محافظات)` : matched.join('، ');
      }
      if (cs.includes('عدة محافظات')) {
        return `${selCities.slice(0, 2).join('، ')} (شامل المحافظات)`;
      }
    }
    if (cs.length <= 3) return cs.join('، ');
    return `${cs.slice(0, 2).join('، ')} (+${cs.length - 2} محافظات)`;
  }

  function badges(j) {
    const b = [
      el('span', { class: 'b city' }, '📍 ' + formatCardCities(j)),
      el('span', { class: 'b exp' }, expText(j.experience))
    ];
    if (j.sector && SECTOR_UI[j.sector]) {
      b.push(el('span', { class: 'b sec' }, SECTOR_UI[j.sector].label));
    }
    if (j.education) {
      b.push(el('span', { class: 'b' }, '🎓 ' + (EDU_LABEL[j.education] || j.education) + (j.education_from === 'grade' && j.grade ? ` (فئة ${j.grade})` : '')));
    }
    if (j.field && j.field !== 'other') {
      b.push(el('span', { class: 'b' }, '💼 ' + (j.subfield ? subLabel(j.subfield) : fieldLabel(j.field))));
    }
    if (j.specialty) {
      b.push(el('span', { class: 'b' }, '🔹 ' + j.specialty.slice(0, 45)));
    }
    if (j.salary) {
      b.push(el('span', { class: 'b' }, '💰 الأجر: ' + j.salary.slice(0, 35)));
    }
    if (j.shift) {
      b.push(el('span', { class: 'b' }, '🕒 ' + j.shift.slice(0, 40)));
    }
    if (j.age_min || j.age_max) {
      b.push(el('span', { class: 'b' }, `👤 العمر ${j.age_min || '؟'}–${j.age_max || '؟'}`));
    }
    if (j.gender) {
      b.push(el('span', { class: 'b' }, j.gender === 'female' ? '👩 إناث فقط' : '👨 ذكور فقط'));
    }
    if (j.deadline) {
      b.push(el('span', { class: 'b' }, '📅 حتى ' + j.deadline));
    }
    (j.tags || []).forEach((t) => b.push(el('span', { class: 'b' }, t)));
    return b;
  }

  function statusOf(j) {
    const fresh = j.posted_at && Date.now() - new Date(j.posted_at) < 864e5;
    if (j.deadline) {
      const d = daysLeft(j.deadline);
      if (d < 0) return el('span', { class: 'status' }, 'انتهى موعد التقديم');
      if (d === 0) return el('span', { class: 'status soon' }, '⚠️ آخر موعد للتقديم اليوم!');
      return el('span', { class: 'status ' + (d <= 3 ? 'soon' : fresh ? 'new' : 'open') },
        (fresh ? 'جديد · ' : '') + `متبقي ${d} ${d <= 10 ? 'أيام' : 'يوماً'}`);
    }
    return el('span', { class: 'status ' + (fresh ? 'new' : 'open') }, (fresh ? 'جديد اليوم · ' : '') + 'نُشر ' + ago(j.posted_at));
  }

  function toggleSave(j) {
    if (savedMap[j.id]) {
      delete savedMap[j.id];
      toast('تمت إزالة الشاغر من المحفوظات');
    } else {
      savedMap[j.id] = j;
      toast('تم حفظ الشاغر في المحفوظات 🔖');
    }
    saveSaved();
    updateSavedCount();
    if (state.saved) refresh();
  }

  function updateSavedCount() {
    const n = Object.keys(savedMap).length;
    if ($('savedCount')) $('savedCount').textContent = n;
  }

  function copyText(text, msg = 'تم النسخ إلى الحافظة ✅') {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(() => toast(msg)).catch(() => fallbackCopy(text, msg));
    } else {
      fallbackCopy(text, msg);
    }
  }

  function fallbackCopy(text, msg) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    try {
      document.execCommand('copy');
      toast(msg);
    } catch (_) {
      toast('تعذر النسخ التلقائي، يرجى تحديد النص ونسخه يدوياً');
    }
    document.body.removeChild(ta);
  }

  const PLACEHOLDER_EMP = new Set(['غير معلن', 'اعلانات', 'إعلانات', 'شواغر سوريا', '']);

  function cleanEmployer(j) {
    const raw = (j.employer || '').trim();
    if (!PLACEHOLDER_EMP.has(raw)) return raw;
    return j.official ? 'جهة حكومية رسمية' : '';
  }

  function primaryJobLink(j) {
    if (j.apply_url && !/^https?:\/\/wa\.me\//i.test(j.apply_url)) return j.apply_url;
    return `${location.origin}/j/${j.id}`;
  }

  function buildShareText(j) {
    const emp = cleanEmployer(j);
    const cities = (j.cities && j.cities.length) ? j.cities.join('، ') : 'سوريا (عام)';
    const roleLabel = j.field && j.field !== 'other' ? (j.subfield ? subLabel(j.subfield) : fieldLabel(j.field)) : '';
    const edu = j.education ? (EDU_LABEL[j.education] || j.education) : 'غير مشترط / غير مذكور';
    const lines = [
      `📢 فرصة عمل: ${j.title}`,
      emp ? `🏢 الجهة: ${emp}` : null,
      `📍 المدينة: ${cities}`,
      roleLabel ? `💼 المجال: ${roleLabel}` : null,
      `🎓 المؤهل العلمي: ${edu}`,
      j.specialty ? `🎯 التخصص: ${j.specialty}` : null,
      `⏱️ الخبرة: ${expText(j.experience)}`,
      j.salary ? `💰 الراتب / الأجر: ${j.salary}` : null,
      j.shift ? `🕒 الدوام: ${j.shift}` : null,
      j.deadline ? `📅 آخر موعد للتقديم: ${j.deadline}` : null
    ].filter(Boolean);

    if (j.summary) {
      lines.push('', `📝 وصف وتفاصيل العمل:\n${j.summary}`);
    }
    if (j.tasks && j.tasks.length) {
      lines.push('', `📌 المهام والمسؤوليات:\n${j.tasks.slice(0, 5).map((t) => '• ' + t).join('\n')}`);
    }
    if (j.skills && j.skills.length) {
      lines.push('', `✅ الشروط والمهارات:\n${j.skills.slice(0, 5).map((s) => '• ' + s).join('\n')}`);
    }

    // Put ONLY the actual application method requested by the employer (link vs email CV vs phone/WhatsApp)
    const hasDirectLink = Boolean(j.apply_url && !/^https?:\/\/wa\.me\//i.test(j.apply_url));
    const hasEmail = Boolean(j.email);
    const phone = (j.phones || [])[0];
    const applyLines = [];

    if (hasEmail) {
      applyLines.push(`✉️ للتقديم (إرسال السيرة الذاتية CV عبر البريد الإلكتروني): ${j.email}`);
    }
    if (hasDirectLink) {
      const isForm = (j.apply_types || []).includes('form');
      applyLines.push(`🔗 ${isForm ? 'رابط استمارة التقديم' : 'رابط التقديم المباشر'}: ${j.apply_url}`);
    }
    if (phone) {
      applyLines.push(`📞 للتواصل والتقديم (هاتف / واتساب): ${phone}`);
    }
    if (applyLines.length) {
      lines.push('', ...applyLines);
    }
    return lines.join('\n');
  }

  function shareJob(j) {
    const pageUrl = `${location.origin}/j/${j.id}`;
    const hasDirectLink = Boolean(j.apply_url && !/^https?:\/\/wa\.me\//i.test(j.apply_url));
    const linkToCopy = hasDirectLink ? j.apply_url : pageUrl;
    const fullText = buildShareText(j);
    const dlg = $('shareDialog');
    const body = $('shareDialogBody');
    if (!dlg || !body) {
      copyText(fullText, 'تم نسخ الإعلان كاملاً مع الوصف 📋');
      return;
    }

    const previewEl = el('textarea', {
      class: 'share-preview',
      dir: 'rtl',
      'aria-label': 'نص الإعلان الكامل مع الوصف وطريقة التقديم الأساسية'
    }, fullText);

    const urlInput = el('input', {
      type: 'text',
      class: 'share-url-input',
      readonly: 'readonly',
      value: linkToCopy,
      onclick: (e) => e.currentTarget.select()
    });

    body.replaceChildren(...[
      el('div', { class: 'drawer-header' },
        el('h2', { id: 'shareDialogTitle' }, '📤 مشاركة ونسخ الإعلان'),
        el('button', { class: 'close', 'aria-label': 'إغلاق', onclick: () => dlg.close() }, '×')
      ),
      el('p', { class: 'drawer-desc' }, 'نص الإعلان جاهز للنسخ والنشر مع الوصف وطريقة التقديم الأساسية المحددة في الإعلان (إيميل CV أو رابط تقديم أو رقم تواصل):'),
      previewEl,
      el('div', { class: 'share-url-row' },
        urlInput,
        el('button', {
          type: 'button',
          class: 'btn ghost',
          onclick: () => copyText(linkToCopy, hasDirectLink ? 'تم نسخ رابط التقديم المباشر 🔗' : 'تم نسخ رابط الإعلان 🔗')
        }, hasDirectLink ? '🔗 نسخ رابط التقديم' : '🔗 نسخ رابط الإعلان')
      ),
      el('div', { class: 'share-actions' },
        el('button', {
          type: 'button',
          class: 'btn primary',
          onclick: () => copyText(previewEl.value, 'تم نسخ الإعلان كاملاً مع الوصف ✅')
        }, '📋 نسخ الإعلان مع الوصف'),
        j.email ? el('button', {
          type: 'button',
          class: 'btn ghost',
          onclick: () => copyText(j.email, 'تم نسخ البريد الإلكتروني للتقديم ✉️')
        }, '✉️ نسخ الإيميل فقط') : null,
        el('button', {
          type: 'button',
          class: 'btn ghost',
          onclick: () => window.open('https://wa.me/?text=' + encodeURIComponent(previewEl.value), '_blank', 'noopener')
        }, '💬 إرسال عبر واتساب'),
        el('button', {
          type: 'button',
          class: 'btn ghost',
          onclick: () => window.open('https://t.me/share/url?url=' + encodeURIComponent(linkToCopy) + '&text=' + encodeURIComponent(previewEl.value), '_blank', 'noopener')
        }, '✈️ إرسال عبر تيليجرام'),
        navigator.share ? el('button', {
          type: 'button',
          class: 'btn ghost',
          onclick: () => navigator.share({ title: j.title, text: previewEl.value }).catch(() => {})
        }, '📲 مشاركة عبر الجهاز') : null
      )
    ].filter(Boolean));

    dlg.showModal();
  }

  function speakJob(j) {
    if (!('speechSynthesis' in window)) {
      toast('المتصفح لا يدعم القراءة الصوتية');
      return;
    }
    speechSynthesis.cancel();
    const parts = [
      j.title,
      j.employer ? 'لدى ' + j.employer : '',
      (j.cities || []).join(' و '),
      j.education ? 'المؤهل المطلوب ' + (EDU_LABEL[j.education] || j.education) : '',
      expText(j.experience),
      j.salary ? 'الراتب ' + j.salary : '',
      j.deadline ? 'آخر موعد للتقديم ' + j.deadline : ''
    ];
    const u = new SpeechSynthesisUtterance(parts.filter(Boolean).join('. '));
    u.lang = 'ar';
    speechSynthesis.speak(u);
  }

  function downloadIcs(j) {
    if (!j.deadline) return;
    const d = parseDay(j.deadline);
    d.setDate(d.getDate() - 1);
    const ymd = d.toISOString().slice(0, 10).replace(/-/g, '');
    const ics = [
      'BEGIN:VCALENDAR', 'VERSION:2.0', 'BEGIN:VEVENT',
      `DTSTART:${ymd}T090000`, `DTEND:${ymd}T100000`,
      `SUMMARY:تذكير انتهاء التقديم: ${j.title}`,
      `DESCRIPTION:${location.origin}/j/${j.id}`,
      'END:VEVENT', 'END:VCALENDAR'
    ].join('\r\n');
    const a = el('a', { href: URL.createObjectURL(new Blob([ics], { type: 'text/calendar' })), download: `laqeet-${j.id}.ics` });
    a.click();
  }

  function links(j, big) {
    const out = el('div', { class: 'links' });
    const wa = waLink(j);
    if (wa) {
      out.append(el('a', { class: 'wa', href: wa, target: '_blank', rel: 'noopener' }, 'راسل واتساب'));
    }
    const phone = (j.phones || [])[0];
    if (phone && !j.whatsapp) {
      out.append(el('a', { class: 'call', href: 'tel:' + phone }, 'اتصل ' + phone));
    }
    if (j.apply_url && !/^https?:\/\/wa\.me\//i.test(j.apply_url) && !(j.deadline && daysLeft(j.deadline) < 0)) {
      const isForm = (j.apply_types || []).includes('form');
      out.append(el('a', { class: 'apply', href: j.apply_url, target: '_blank', rel: 'noopener' }, isForm ? 'تعبئة استمارة التقديم' : 'التقديم عبر الموقع'));
    }
    if (j.details_url && j.details_url !== j.apply_url) {
      out.append(el('a', { href: j.details_url, target: '_blank', rel: 'noopener' }, 'تفاصيل الإعلان'));
    }
    if (j.email) {
      out.append(el('a', { href: 'mailto:' + j.email }, 'إرسال CV بالإيميل'));
    }
    (j.sources || []).slice(0, big ? 9 : 2).forEach((s) => {
      out.append(el('a', { href: s.post_url, target: '_blank', rel: 'noopener' }, 'المصدر: ' + s.label));
    });

    const isSav = Boolean(savedMap[j.id]);
    const saveBtn = el('button', {
      class: 'report',
      onclick: (e) => {
        toggleSave(j);
        e.currentTarget.textContent = savedMap[j.id] ? '🔖 محفوظة' : 'احفظ';
      }
    }, isSav ? '🔖 محفوظة' : 'احفظ');

    const hasDirectLink = Boolean(j.apply_url && !/^https?:\/\/wa\.me\//i.test(j.apply_url));
    out.append(
      el('button', { class: 'report share-btn', onclick: () => shareJob(j) }, '📤 مشاركة ونسخ الإعلان'),
      j.email && !hasDirectLink
        ? el('button', { class: 'report', onclick: () => copyText(j.email, 'تم نسخ إيميل التقديم ✉️') }, '✉️ نسخ الإيميل')
        : el('button', { class: 'report', onclick: () => copyText(primaryJobLink(j), 'تم نسخ الرابط 🔗') }, '🔗 نسخ الرابط'),
      saveBtn,
      el('button', { class: 'report', onclick: () => report(j) }, 'بلّغ')
    );
    return out;
  }

  function card(j, i) {
    const band = expBand(j.experience);
    const m = fit(j);
    const style = `--edge:var(--exp-${band});--ebg:var(--exp-${band}-bg);--efg:var(--exp-${band});--i:${Math.min(i, 12)}`;
    const emp = cleanEmployer(j);
    const roleLabel = j.field && j.field !== 'other' ? (j.subfield ? subLabel(j.subfield) : fieldLabel(j.field)) : '';
    const where = ['📍 ' + formatCardCities(j), emp ? '🏢 ' + emp : '', roleLabel ? '💼 ' + roleLabel : ''].filter(Boolean).join(' · ');
    const summaryText = j.summary || (j.tasks && j.tasks.length ? j.tasks.slice(0, 2).join(' · ') : '') || (j.skills && j.skills.length ? j.skills.slice(0, 2).join(' · ') : '');
    const desc = summaryText ? el('p', { class: 'card-desc' }, ...highlightNodes(summaryText, state.terms)) : null;

    return el('article', { class: 'card in', style },
      el('div', { class: 'top' },
        statusOf(j),
        j.official ? el('span', { class: 'official' }, 'رسمي ✓ حكومي') : el('span', { class: 'src' }, (j.sources[0] || {}).label || '')
      ),
      el('h3', null, el('button', { onclick: () => openDetail(j) }, ...highlightNodes(j.title, state.terms))),
      el('div', { class: 'meta' }, ...highlightNodes(where, state.terms)),
      desc,
      m ? el('div', { class: 'fit ' + m.level }, el('i'), m.text) : null,
      el('div', { class: 'badges' }, badges(j)),
      links(j)
    );
  }

  function openDetail(j) {
    const m = fit(j);
    const kv = [
      ['الجهة المشغلة', j.employer],
      ['المحافظات والمدن', (j.cities || []).join('، ') || 'سوريا (عام)'],
      ['مجال العمل', j.field && j.field !== 'other' ? fieldLabel(j.field) : null],
      ['المؤهل العلمي', j.education ? (EDU_LABEL[j.education] || j.education) : 'غير مشترط / غير مذكور'],
      ['التخصص الدقيق', j.specialty],
      ['الخبرة المطلوبة', expText(j.experience)],
      ['العمر', j.age_min || j.age_max ? `${j.age_min || '؟'}–${j.age_max || '؟'} سنة` : null],
      ['نظام الدوام', j.shift],
      ['الأجر والراتب', j.salary],
      ['آخر موعد للتقديم', j.deadline]
    ].filter(([, v]) => v);

    const listBlock = (title, items) => (items && items.length) ? [el('h4', null, title), el('ul', null, items.map((x) => el('li', null, x)))] : [];
    const summaryBlock = j.summary ? [el('h4', null, 'نبذة ووصف الشاغر'), el('p', { style: 'margin:4px 0 10px;line-height:1.6' }, j.summary)] : [];
    const hasDirectLink = Boolean(j.apply_url && !/^https?:\/\/wa\.me\//i.test(j.apply_url));
    const shareSection = el('div', { class: 'share-box' },
      el('div', { class: 'share-box-title' }, '📤 مشاركة الإعلان أو نسخه مع الوصف وطريقة التقديم:'),
      el('div', { class: 'share-actions' },
        el('button', { type: 'button', class: 'btn primary', onclick: () => copyText(buildShareText(j), 'تم نسخ الإعلان كاملاً مع الوصف ✅') }, '📋 نسخ الإعلان مع الوصف'),
        j.email && !hasDirectLink
          ? el('button', { type: 'button', class: 'btn ghost', onclick: () => copyText(j.email, 'تم نسخ إيميل التقديم ✉️') }, '✉️ نسخ الإيميل للتقديم')
          : el('button', { type: 'button', class: 'btn ghost', onclick: () => copyText(primaryJobLink(j), 'تم نسخ الرابط 🔗') }, '🔗 نسخ الرابط فقط'),
        el('button', { type: 'button', class: 'btn ghost', onclick: () => shareJob(j) }, '📝 معاينة النص أو إرساله واتساب/تيليجرام')
      )
    );

    $('sheetBody').replaceChildren(...[
      el('button', { class: 'close', 'aria-label': 'إغلاق', onclick: () => $('sheet').close() }, '×'),
      el('h2', { id: 'sheetTitle' }, j.title),
      j.official ? el('span', { class: 'official', style: 'align-self:flex-start' }, 'رسمي ✓ مسابقة أو إعلان حكومي') : null,
      m ? el('div', { class: 'fit ' + m.level }, el('i'), m.text) : null,
      el('dl', { class: 'kv' }, kv.map(([k, v]) => [el('dt', null, k), el('dd', null, v)])),
      summaryBlock,
      listBlock('المهام والمسؤوليات الرئيسية', j.tasks),
      listBlock('الشروط والمهارات المطلوبة', j.skills),
      shareSection,
      el('h4', null, 'جهات نشر وتوثيق الشاغر:'),
      el('ul', null, (j.sources || []).map((s) => el('li', null, el('a', { href: s.post_url, target: '_blank', rel: 'noopener' }, s.label)))),
      links(j, true),
      el('div', { class: 'row', style: 'margin-top:10px' },
        el('a', { class: 'btn primary', href: 'cv.html?job=' + j.id }, 'إنشاء سيرة ذاتية لهذا الشاغر'),
        el('button', { class: 'btn ghost', onclick: () => speakJob(j) }, '🔊 قراءة صوتية للإعلان'),
        j.deadline ? el('button', { class: 'btn ghost', onclick: () => downloadIcs(j) }, '📅 تذكير بالموعد') : null
      )
    ].flat(Infinity).filter(Boolean));

    $('sheet').showModal();
  }

  async function report(j) {
    if (!confirm('هل ترغب بالإبلاغ عن هذا الإعلان لوجود شبهة نصب أو خطأ بالبيانات؟')) return;
    try {
      await fetch(`${API}/jobs/${j.id}/report`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: 'user_report' })
      });
      toast('شكراً لك، تم استلام البلاغ وسيتم مراجعته.');
    } catch (e) {
      toast('تعذر إرسال البلاغ، يرجى المحاولة لاحقاً');
    }
  }

  // ---------------------------------------------------------------- query params
  function buildParams() {
    const p = new URLSearchParams();
    p.set('country', state.country || 'sy');
    if (state.sector) p.set('sector', state.sector);
    if (state.edu_group) p.set('edu_group', state.edu_group);
    if (state.edu) p.set('edu', state.edu);
    if (state.lower) p.set('lower', 'true');
    if (state.source) p.set('source', state.source);
    if (state.apply) p.set('apply', state.apply);
    if (state.city) p.set('city', state.city);
    if (state.field) p.set('field', state.field);
    if (state.sub) p.set('sub', state.sub);
    if (state.q) p.set('q', state.q);
    if (state.sort && state.sort !== 'relevance') p.set('sort', state.sort);
    if (state.fresh) p.set('new', 'true');
    p.set('limit', PAGE);
    p.set('offset', state.offset);
    return p;
  }

  const SYRIAN_GOVERNORATES = [
    'دمشق', 'ريف دمشق', 'حلب', 'حمص', 'حماة', 'اللاذقية', 'طرطوس',
    'إدلب', 'درعا', 'السويداء', 'القنيطرة', 'دير الزور', 'الحسكة', 'الرقة'
  ];
  const TURKISH_PROVINCES = [
    'إسطنبول', 'غازي عنتاب', 'مرسين', 'أنقرة', 'بورصة', 'هاتاي (أنطاكيا)',
    'إزمير', 'قونيا', 'شانلي أورفا', 'أضنة', 'قيصري', 'كليس',
    'طرابزون', 'أنطاليا', 'قوجه إيلي', 'ساكاريا', 'سامسون'
  ];
  const mselRegistry = {};

  function createMultiSelect(selectId, cfg) {
    const selEl = $(selectId);
    if (!selEl) return null;
    selEl.hidden = true;
    selEl.style.display = 'none';

    let wrap = selEl.parentElement.querySelector(`.msel[data-for="${selectId}"]`);
    if (wrap) wrap.remove();

    const btnText = el('span', { class: 'msel-btn-text' }, `${cfg.icon} ${cfg.placeholder}`);
    const badge = el('span', { class: 'msel-badge', hidden: true }, '0');
    const caret = el('span', { class: 'msel-caret' }, '▾');
    const btn = el('button', {
      type: 'button',
      class: 'msel-btn',
      'aria-haspopup': 'listbox',
      'aria-expanded': 'false'
    }, btnText, badge, caret);

    const selectAllLink = el('button', {
      type: 'button',
      class: 'msel-link',
      onclick: (e) => {
        e.stopPropagation();
        cfg.setValue(joinCsv(cfg.options.map(([v]) => v)));
      }
    }, 'تحديد الكل');

    const clearLink = el('button', {
      type: 'button',
      class: 'msel-link',
      onclick: (e) => {
        e.stopPropagation();
        cfg.setValue('');
      }
    }, 'إلغاء التحديد');

    const statusText = el('span', null, 'الكل متاح');
    const applyBtn = el('button', {
      type: 'button',
      class: 'msel-apply',
      onclick: (e) => {
        e.stopPropagation();
        closeMenu();
      }
    }, 'تم ✓');

    const listEl = el('div', {
      class: 'msel-list' + (cfg.twoColumns ? ' grid-2' : ''),
      role: 'listbox',
      'aria-multiselectable': 'true'
    });

    const menu = el('div', { class: 'msel-menu', hidden: true },
      el('div', { class: 'msel-head' },
        el('span', null, cfg.headerHint || 'اختر واحداً أو أكثر:'),
        el('div', { class: 'msel-quick-btns' }, selectAllLink, el('span', null, '·'), clearLink)
      ),
      listEl,
      el('div', { class: 'msel-foot' }, statusText, applyBtn)
    );

    wrap = el('div', { class: 'msel', 'data-for': selectId }, btn, menu);
    selEl.insertAdjacentElement('afterend', wrap);

    const labelMap = Object.fromEntries(cfg.options.map(([v, l]) => [v, l]));

    function closeMenu() {
      wrap.classList.remove('open');
      menu.hidden = true;
      btn.setAttribute('aria-expanded', 'false');
    }

    function openMenu() {
      Object.values(mselRegistry).forEach((w) => { if (w && w.closeMenu !== closeMenu) w.closeMenu(); });
      wrap.classList.add('open');
      menu.hidden = false;
      btn.setAttribute('aria-expanded', 'true');
      renderList();
    }

    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      if (menu.hidden) openMenu();
      else closeMenu();
    });

    function renderList() {
      const selected = splitCsv(cfg.getValue());
      const allChecked = selected.length === 0;
      const rows = [
        el('button', {
          type: 'button',
          class: 'msel-opt span-all',
          role: 'option',
          'aria-checked': String(allChecked),
          onclick: (e) => {
            e.stopPropagation();
            cfg.setValue('');
          }
        },
          el('span', { class: 'msel-chk' }, allChecked ? '✓' : ''),
          el('span', { class: 'msel-lbl' }, cfg.placeholder)
        )
      ];

      for (const [val, label] of cfg.options) {
        const isChecked = selected.includes(val);
        rows.push(
          el('button', {
            type: 'button',
            class: 'msel-opt',
            role: 'option',
            'aria-checked': String(isChecked),
            onclick: (e) => {
              e.stopPropagation();
              const nextCsv = toggleCsvItem(cfg.getValue(), val);
              cfg.setValue(nextCsv);
            }
          },
            el('span', { class: 'msel-chk' }, isChecked ? '✓' : ''),
            el('span', { class: 'msel-lbl' }, label)
          )
        );
      }
      listEl.replaceChildren(...rows);
    }

    function sync() {
      const selected = splitCsv(cfg.getValue());
      const labels = selected.map((v) => labelMap[v] || v);
      if (!selected.length) {
        btnText.textContent = `${cfg.icon} ${cfg.placeholder}`;
        badge.hidden = true;
        btn.classList.remove('has-val');
        statusText.textContent = 'الكل متاح';
      } else if (selected.length === 1) {
        btnText.textContent = `${cfg.icon} ${labels[0]}`;
        badge.hidden = true;
        btn.classList.add('has-val');
        statusText.textContent = 'تم اختيار عنصر واحد';
      } else {
        const preview = labels.slice(0, 2).join('، ') + (labels.length > 2 ? ` (+${labels.length - 2})` : '');
        btnText.textContent = `${cfg.icon} ${preview}`;
        badge.textContent = String(selected.length);
        badge.hidden = false;
        btn.classList.add('has-val');
        statusText.textContent = `تم اختيار ${selected.length} عناصر`;
      }
      renderList();
    }

    sync();
    const api = { sync, closeMenu, openMenu, wrap };
    mselRegistry[selectId] = api;
    return api;
  }

  function syncFilterUI() {
    if ($('q')) $('q').value = state.q;
    if ($('clearQ')) $('clearQ').hidden = !state.q;
    if ($('city')) $('city').value = splitCsv(state.city)[0] || '';
    if ($('eduFilter')) $('eduFilter').value = splitCsv(state.edu)[0] || '';
    if ($('fieldFilter')) $('fieldFilter').value = splitCsv(state.field)[0] || '';
    Object.values(mselRegistry).forEach((w) => { if (w) w.sync(); });
    if ($('lower')) $('lower').checked = state.lower;
    if ($('sortOrder')) $('sortOrder').value = state.sort;
    document.querySelectorAll('#sectorTabs .sector-card').forEach((c) => c.setAttribute('aria-selected', String(c.dataset.sector === state.sector)));
    document.querySelectorAll('#eduGroupChips .chip').forEach((c) => c.setAttribute('aria-pressed', String(c.dataset.eduGroup === state.edu_group)));
    const selApplies = splitCsv(state.apply);
    document.querySelectorAll('#applyRow .chip').forEach((c) => {
      const code = c.dataset.apply;
      const pressed = code ? selApplies.includes(code) : selApplies.length === 0;
      c.setAttribute('aria-pressed', String(pressed));
    });
    if ($('newToday')) $('newToday').setAttribute('aria-pressed', String(state.fresh));
    if ($('savedOnly')) $('savedOnly').setAttribute('aria-pressed', String(state.saved));
  }

  function renderActivePills() {
    const box = $('activeFilterPills');
    if (!box) return;
    const pills = [];
    const addPill = (label, clearFn) => {
      pills.push(el('span', { class: 'active-pill' }, label, el('button', { type: 'button', 'aria-label': 'إزالة ' + label, onclick: clearFn }, '×')));
    };
    if (state.q) addPill(`بحث: «${state.q}»`, () => { state.q = ''; syncFilterUI(); refresh(); });
    if (state.sector && SECTOR_UI[state.sector]) addPill(SECTOR_UI[state.sector].label, () => { state.sector = ''; syncFilterUI(); refresh(); });
    for (const c of splitCsv(state.city)) {
      addPill(`📍 ${c}`, () => { state.city = removeCsvItem(state.city, c); syncFilterUI(); refresh(); });
    }
    for (const e of splitCsv(state.edu)) {
      addPill(`🎓 ${EDU_LABEL[e] || e}`, () => { state.edu = removeCsvItem(state.edu, e); syncFilterUI(); refresh(); });
    }
    if (state.edu_group) addPill(state.edu_group === 'degree' ? '🎓 بشهادة' : '🛠️ بدون شهادة', () => { state.edu_group = ''; syncFilterUI(); refresh(); });
    for (const f of splitCsv(state.field)) {
      addPill(`💼 ${fieldLabel(f)}`, () => {
        state.field = removeCsvItem(state.field, f);
        if (!splitCsv(state.field).includes('computer')) state.sub = '';
        syncFilterUI();
        refresh();
      });
    }
    for (const s of splitCsv(state.sub)) {
      addPill(subLabel(s), () => { state.sub = removeCsvItem(state.sub, s); refresh(); });
    }
    for (const a of splitCsv(state.apply)) {
      addPill(APPLY_UI[a] || a, () => { state.apply = removeCsvItem(state.apply, a); syncFilterUI(); refresh(); });
    }
    if (state.fresh) addPill('✦ جديد اليوم', () => { state.fresh = false; syncFilterUI(); refresh(); });
    if (state.saved) addPill('🔖 المحفوظة فقط', () => { state.saved = false; syncFilterUI(); refresh(); });
    box.replaceChildren(...pills);
  }

  // ---------------------------------------------------------------- refresh jobs
  let requestSeq = 0;
  async function refresh(append) {
    if (state.saved) {
      state.terms = [];
      renderActivePills();
      if ($('typoBanner')) $('typoBanner').hidden = true;
      const list = Object.values(savedMap);
      $('grid').replaceChildren(
        ...(list.length ? list.map((j, i) => card(j, i)) : [
          el('div', { class: 'empty' },
            el('b', null, 'لا توجد شواغر محفوظة بعد'),
            el('span', null, 'اضغط على زر «احفظ» الموجود على أي بطاقة شاغر للوصول السريع إليها هنا.')
          )
        ])
      );
      $('count').textContent = `${list.length} شاغر في المحفوظات`;
      $('more').hidden = true;
      return;
    }

    if (!append) state.offset = 0;
    const currentSeq = ++requestSeq;

    let data;
    try {
      data = await getWithRetry('/jobs?' + buildParams(), 3, 1800);
    } catch (e) {
      $('grid').replaceChildren(
        el('div', { class: 'empty' },
          el('b', null, 'تعذر تحميل الشواغر حالياً'),
          el('span', null, 'قد يكون الخادم في مرحلة الإقلاع المؤقت. اضغط إعادة المحاولة بعد ثوانٍ.'),
          el('button', {
            type: 'button',
            class: 'btn primary',
            style: 'margin-top:12px',
            onclick: () => refresh(append)
          }, 'إعادة تحميل الشواغر')
        )
      );
      return;
    }

    if (currentSeq !== requestSeq) return;
    state.total = data.total;
    state.terms = data.terms || [];
    renderActivePills();

    const banner = $('typoBanner');
    if (banner) {
      if (data.suggestion && !append) {
        banner.hidden = false;
        banner.replaceChildren(
          el('span', null, 'هل تقصد: '),
          el('button', {
            type: 'button',
            class: 'typo-btn',
            onclick: () => {
              state.q = data.suggestion;
              syncFilterUI();
              refresh();
            }
          }, data.suggestion),
          el('span', null, ' ؟')
        );
      } else {
        banner.hidden = true;
        banner.replaceChildren();
      }
    }

    const cards = data.jobs.map((j, i) => card(j, i));
    if (append) {
      $('grid').append(...cards);
    } else {
      $('grid').replaceChildren(...cards);
    }

    // Empty state handling with smart reset
    if (!data.total) {
      $('grid').replaceChildren(
        el('div', { class: 'empty' },
          el('b', null, state.q ? `لم يتم العثور على شواغر لكلمة «${state.q}» ضمن الفلاتر الحالية` : 'لا توجد فرص عمل مطابقة لهذه الفلاتر حالياً'),
          el('span', null, 'جرّب توسيع البحث أو إزالة بعض شروط الفلترة.'),
          el('button', { class: 'btn primary', onclick: resetFilters }, 'عرض كل الشواغر المتاحة بدون فلاتر')
        )
      );
    }

    const shown = Math.min(state.offset + PAGE, data.total);
    const selCities = splitCsv(state.city);
    const countryDefaultLabel = state.country === 'tr' ? 'في كل تركيا 🇹🇷' : 'في كل سوريا 🇸🇾';
    const whereLabel = selCities.length ? `في ${selCities.join(' و ')}` : countryDefaultLabel;
    $('count').textContent = `${data.total} فرصة عمل مفتوحة ${whereLabel}` + (data.total > shown ? ` · معروض ${shown}` : '');
    if ($('boardOpenLabel')) {
      $('boardOpenLabel').textContent = state.country === 'tr' ? 'فرصة مفتوحة في تركيا 🇹🇷' : 'إعلان مفتوح في سوريا 🇸🇾';
    }
    const boardOpenNode = document.querySelector('[data-count="board_open"]');
    if (boardOpenNode && !state.city && !state.field && !state.edu && !state.edu_group && !state.q && !state.sector && !state.apply) {
      boardOpenNode.textContent = data.total;
    }
    $('more').hidden = data.total <= shown;

    if (!append) {
      renderBars(data.facets.field);
      renderSubrow(data.facets.sub);
      updateSectorBadges(data.facets.sector);
      updateApplyBadges(data.facets.apply);
    }
  }

  function updateSectorBadges(sectorFacets) {
    const counts = Object.fromEntries((sectorFacets || []).map((s) => [s.code, s.count]));
    const total = (sectorFacets || []).reduce((a, b) => a + b.count, 0);
    const selCities = splitCsv(state.city);
    const selFields = splitCsv(state.field);
    const countryScope = state.country === 'tr' ? 'كل تركيا' : 'كل سوريا';
    const scopeParts = [selCities.length ? selCities.slice(0, 2).join('، ') + (selCities.length > 2 ? ` (+${selCities.length - 2})` : '') : countryScope];
    if (selFields.length) {
      scopeParts.push(selFields.slice(0, 2).map(fieldLabel).join('، ') + (selFields.length > 2 ? ` (+${selFields.length - 2})` : ''));
    }
    const scope = ' · ' + scopeParts.join(' · ');
    const subScope = (selCities.length || selFields.length) ? scope : '';

    if ($('badgeAll')) $('badgeAll').textContent = `${total} شاغر متاح${scope}`;
    if ($('badgeGov')) $('badgeGov').textContent = `${counts.gov || 0} وظيفة رسمية ومسابقات${subScope}`;
    if ($('badgeNgo')) $('badgeNgo').textContent = `${counts.ngo || 0} شاغر أممي وإنساني${subScope}`;
    if ($('badgePrivate')) $('badgePrivate').textContent = `${counts.private || 0} شاغر شركات ومحلات${subScope}`;
  }

  function updateApplyBadges(applyFacets) {
    const counts = Object.fromEntries((applyFacets || []).map((a) => [a.code, a.count]));
    document.querySelectorAll('#applyRow .chip').forEach((btn) => {
      const a = btn.dataset.apply;
      if (a && APPLY_UI[a]) {
        btn.textContent = `${APPLY_UI[a]} (${counts[a] || 0})`;
      }
    });
  }

  function renderBars(fieldFacets) {
    const top = (fieldFacets || []).filter((f) => f.code !== 'other').slice(0, 8);
    const max = Math.max(1, ...top.map((f) => f.count));
    const selFields = splitCsv(state.field);
    $('bars').replaceChildren(...top.map((f) => {
      const fill = el('span', { class: 'f' });
      const b = el('button', {
        class: 'bar',
        'aria-pressed': String(selFields.includes(f.code)),
        'aria-label': `${f.label}: ${f.count}`
      },
        el('span', null, f.label),
        el('span', { class: 't' }, fill),
        el('span', { class: 'n' }, String(f.count))
      );
      b.addEventListener('click', () => {
        state.field = toggleCsvItem(state.field, f.code);
        if (!splitCsv(state.field).includes('computer')) state.sub = '';
        syncFilterUI();
        refresh();
      });
      requestAnimationFrame(() => requestAnimationFrame(() => {
        fill.style.width = (f.count / max * 100) + '%';
      }));
      return b;
    }));
    if (!top.length) {
      $('bars').replaceChildren(el('span', { class: 'sub' }, 'جاري تحديث إحصائيات التخصصات الأكثر طلباً...'));
    }
  }

  function renderSubrow(subFacets) {
    const row = $('subrow');
    const selFields = splitCsv(state.field);
    const selSubs = splitCsv(state.sub);
    row.hidden = !selFields.includes('computer') || !subFacets || !subFacets.length;
    const chip = (code, label) => el('button', {
      class: 'chip',
      'aria-pressed': String(code ? selSubs.includes(code) : selSubs.length === 0),
      onclick: () => {
        state.sub = code ? toggleCsvItem(state.sub, code) : '';
        refresh();
      }
    }, label);
    row.replaceChildren(
      chip('', 'كل تخصصات الحاسوب'),
      ...(subFacets || []).map((f) => chip(f.code, `${f.label} (${f.count})`))
    );
  }

  function resetFilters() {
    state.sector = '';
    state.edu_group = '';
    state.edu = '';
    state.apply = '';
    state.field = '';
    state.sub = '';
    state.city = '';
    state.source = '';
    state.q = '';
    state.sort = 'relevance';
    state.terms = [];
    state.fresh = false;
    state.saved = false;
    state.lower = true;
    if (location.search) {
      try { history.replaceState(null, '', location.pathname + location.hash); } catch (e) {}
    }

    syncFilterUI();
    refresh();
  }

  // ---------------------------------------------------------------- event listeners
  function setupEvents() {
    // Sector selection cards
    document.querySelectorAll('#sectorTabs .sector-card').forEach((btn) => {
      btn.addEventListener('click', () => {
        state.sector = btn.dataset.sector;
        document.querySelectorAll('#sectorTabs .sector-card').forEach((b) => {
          b.setAttribute('aria-selected', String(b === btn));
        });
        refresh();
      });
    });

    // Education Group Chips (الكل / بشهادة / بدون شهادة)
    document.querySelectorAll('#eduGroupChips .chip').forEach((btn) => {
      btn.addEventListener('click', () => {
        state.edu_group = btn.dataset.eduGroup;
        if (state.edu_group) {
          state.edu = '';
          if ($('eduFilter')) $('eduFilter').value = '';
        }
        document.querySelectorAll('#eduGroupChips .chip').forEach((b) => {
          b.setAttribute('aria-pressed', String(b === btn));
        });
        refresh();
      });
    });

    // Application method chips (multi-select supported)
    document.querySelectorAll('#applyRow .chip').forEach((btn) => {
      btn.addEventListener('click', () => {
        const code = btn.dataset.apply;
        state.apply = code ? toggleCsvItem(state.apply, code) : '';
        syncFilterUI();
        refresh();
      });
    });

    // Search input + live autocomplete
    let typingTimer, suggestTimer, activeIdx = -1;
    const suggestBox = $('suggestBox');
    const closeSuggest = () => {
      if (suggestBox) { suggestBox.hidden = true; suggestBox.replaceChildren(); }
      activeIdx = -1;
    };
    const pickSuggest = (text) => {
      $('q').value = text;
      $('clearQ').hidden = !text;
      state.q = text;
      closeSuggest();
      refresh();
    };
    async function loadSuggestions(val) {
      if (!suggestBox || val.length < 2) { closeSuggest(); return; }
      try {
        const res = await get('/suggest?q=' + encodeURIComponent(val));
        const items = res.suggestions || [];
        if (!items.length || $('q').value.trim() !== val) { closeSuggest(); return; }
        activeIdx = -1;
        suggestBox.replaceChildren(...items.map((it) => {
          const kindLabel = it.kind === 'employer' ? 'جهة عمل' : it.kind === 'topic' ? 'موضوع شائع' : `${it.count} شاغر`;
          return el('button', {
            type: 'button',
            class: 'suggest-item',
            role: 'option',
            onclick: () => pickSuggest(it.text)
          }, el('span', null, it.text), el('span', { class: 'suggest-meta' }, kindLabel));
        }));
        suggestBox.hidden = false;
      } catch (e) { closeSuggest(); }
    }

    $('q').addEventListener('input', (e) => {
      const val = e.target.value.trim();
      $('clearQ').hidden = !val;
      clearTimeout(typingTimer);
      clearTimeout(suggestTimer);
      suggestTimer = setTimeout(() => loadSuggestions(val), 140);
      typingTimer = setTimeout(() => {
        state.q = val;
        refresh();
      }, 220);
    });
    $('q').addEventListener('keydown', (e) => {
      if (!suggestBox || suggestBox.hidden) return;
      const opts = [...suggestBox.querySelectorAll('.suggest-item')];
      if (!opts.length) return;
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        activeIdx = (activeIdx + 1) % opts.length;
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        activeIdx = (activeIdx - 1 + opts.length) % opts.length;
      } else if (e.key === 'Enter' && activeIdx >= 0) {
        e.preventDefault();
        pickSuggest(opts[activeIdx].firstChild.textContent);
        return;
      } else if (e.key === 'Escape') {
        closeSuggest();
        return;
      } else return;
      opts.forEach((o, i) => o.setAttribute('aria-selected', String(i === activeIdx)));
    });
    document.addEventListener('click', (e) => {
      if (suggestBox && !e.target.closest('.search-wrap')) closeSuggest();
      if (!e.target.closest('.msel')) {
        Object.values(mselRegistry).forEach((w) => { if (w) w.closeMenu(); });
      }
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        Object.values(mselRegistry).forEach((w) => { if (w) w.closeMenu(); });
      }
    });
    $('clearQ').addEventListener('click', () => {
      $('q').value = '';
      $('clearQ').hidden = true;
      closeSuggest();
      state.q = '';
      refresh();
    });

    if ($('sortOrder')) {
      $('sortOrder').addEventListener('change', (e) => {
        state.sort = e.target.value;
        refresh();
      });
    }

    // Select dropdowns & Green Search Button
    const runSearchAction = (scrollToResults = false) => {
      if ($('q')) state.q = $('q').value.trim();
      state.edu_group = '';
      closeSuggest();
      Object.values(mselRegistry).forEach((w) => { if (w) w.closeMenu(); });
      refresh().then(() => {
        if (scrollToResults && $('count')) {
          $('count').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
      });
    };

    if ($('searchBtn')) {
      $('searchBtn').addEventListener('click', () => runSearchAction(true));
    }
    $('q').addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && (!suggestBox || suggestBox.hidden || activeIdx < 0)) {
        e.preventDefault();
        runSearchAction(true);
      }
    });

    $('city').addEventListener('change', (e) => {
      state.city = e.target.value;
      refresh();
    });
    $('eduFilter').addEventListener('change', (e) => {
      state.edu = e.target.value;
      state.edu_group = '';
      refresh();
    });
    $('fieldFilter').addEventListener('change', (e) => {
      state.field = e.target.value;
      state.sub = '';
      refresh();
    });

    // Toggles
    $('lower').addEventListener('change', (e) => {
      state.lower = e.target.checked;
      refresh();
    });
    $('newToday').addEventListener('click', (e) => {
      state.fresh = !state.fresh;
      e.currentTarget.setAttribute('aria-pressed', String(state.fresh));
      refresh();
    });
    $('savedOnly').addEventListener('click', (e) => {
      state.saved = !state.saved;
      e.currentTarget.setAttribute('aria-pressed', String(state.saved));
      refresh();
    });
    $('resetFiltersBtn').addEventListener('click', resetFilters);
    $('more').addEventListener('click', () => {
      state.offset += PAGE;
      refresh(true);
    });

    // Sheet & Share Dialog close
    $('sheet').addEventListener('click', (e) => {
      if (e.target === $('sheet')) $('sheet').close();
    });
    if ($('shareDialog')) {
      $('shareDialog').addEventListener('click', (e) => {
        if (e.target === $('shareDialog')) $('shareDialog').close();
      });
    }

    // Theme toggle
    $('theme').addEventListener('click', () => {
      const dark = document.documentElement.dataset.theme ? document.documentElement.dataset.theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
      document.documentElement.dataset.theme = dark ? 'light' : 'dark';
      try { localStorage.setItem('laqeet.theme', document.documentElement.dataset.theme); } catch (e) {}
    });
    try {
      const savedTheme = localStorage.getItem('laqeet.theme');
      if (savedTheme) document.documentElement.dataset.theme = savedTheme;
    } catch (e) {}

    // Profile Drawer (Qualifications matching)
    $('openProfileDrawer').addEventListener('click', () => {
      $('userEdu').value = profile.edu || '';
      $('userExp').value = profile.exp ?? '';
      $('userAge').value = profile.age || '';
      $('userGender').value = profile.gender || '';
      $('profileDrawer').showModal();
    });
    $('closeProfileDrawer').addEventListener('click', () => $('profileDrawer').close());
    $('profileDrawer').addEventListener('click', (e) => {
      if (e.target === $('profileDrawer')) $('profileDrawer').close();
    });
    $('saveProfileBtn').addEventListener('click', () => {
      const num = (v) => v === '' ? null : Math.max(0, parseInt(v, 10) || 0);
      profile = {
        edu: $('userEdu').value,
        exp: num($('userExp').value),
        age: num($('userAge').value),
        gender: $('userGender').value
      };
      saveProfile();
      $('profileDrawer').close();
      toast('تم حفظ ملفك الشخصي بنجاح وتفعيل المطابقة ✓');
      refresh();
    });
    $('clearProfileBtn').addEventListener('click', () => {
      profile = {};
      saveProfile();
      $('userEdu').value = '';
      $('userExp').value = '';
      $('userAge').value = '';
      $('userGender').value = '';
      $('profileDrawer').close();
      toast('تم مسح البيانات الشخصية');
      refresh();
    });

    // Back to Landing Page buttons inside Board View
    ['backToLandingBtn', 'navHomeLink', 'footerHomeLink'].forEach((id) => {
      if ($(id)) {
        $(id).addEventListener('click', () => showLanding(true));
      }
    });

    // Board Country Switcher Tabs (🇸🇾 وظائف سوريا | 🇹🇷 وظائف تركيا)
    document.querySelectorAll('[data-board-country]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const c = btn.dataset.boardCountry || 'sy';
        if (state.country === c && state.view === 'board') return;
        enterBoard({ country: c }, true);
      });
    });

    // Landing Page Quick Finder Wizard & Country-Scoped Sector Stats Sync
    window._wizCountry = 'sy';
    const syncLandingCountry = (countryCode) => {
      const c = countryCode === 'tr' ? 'tr' : 'sy';
      const isTr = c === 'tr';
      window._wizCountry = c;

      document.querySelectorAll('[data-wiz-country]').forEach((t) => {
        const active = t.dataset.wizCountry === c;
        t.classList.toggle('active', active);
        t.setAttribute('aria-selected', String(active));
      });
      document.querySelectorAll('[data-gw-country]').forEach((t) => {
        const active = t.dataset.gwCountry === c;
        t.classList.toggle('active', active);
        t.setAttribute('aria-selected', String(active));
      });

      const cs = getCountryStats(c);
      const cityCounts = cs.city_counts || {};
      const list = isTr
        ? TURKISH_PROVINCES
        : ((META && META.cities && META.cities.length) ? META.cities : SYRIAN_GOVERNORATES);
      const selCity = $('lpWizCity');
      if (selCity) {
        const prev = selCity.value;
        selCity.replaceChildren(
          new Option(isTr ? `كل الولايات والمدن التركية (${cs.open || 0})` : `كل المدن والمحافظات السورية (${cs.open || 0})`, ''),
          ...list.map((city) => new Option(cityCounts[city] ? `${city} (${cityCounts[city]})` : city, city))
        );
        selCity.value = list.includes(prev) ? prev : '';
      }

      const selSec = $('lpWizSector');
      if (selSec) {
        const prevSec = selSec.value;
        selSec.replaceChildren(
          new Option(`كل القطاعات والمؤهلات (${cs.open || 0})`, ''),
          new Option(`🤝 منظمات دولية وإنسانية NGOs (${cs.ngo_count || 0})`, 'ngo'),
          new Option(`🏢 شركات وقطاع خاص (${cs.private_count || 0})`, 'private'),
          new Option(`🏛️ قطاع حكومي ومسابقات (${cs.gov_count || 0})`, 'gov'),
          new Option(`🛠️ فرص سريعة وبدون شهادة (${cs.no_degree_count || 0})`, 'nodegree')
        );
        selSec.value = prevSec;
      }

      const bar = $('lpWizSectorsBar');
      if (bar) {
        const countryTag = isTr ? 'تركيا 🇹🇷' : 'سوريا 🇸🇾';
        const items = [
          { lbl: '🏢 قطاع خاص', cnt: cs.private_count || 0, sector: 'private' },
          { lbl: '🤝 منظمات دولية', cnt: cs.ngo_count || 0, sector: 'ngo' },
          { lbl: '🏛️ حكومي', cnt: cs.gov_count || 0, sector: 'gov' },
          { lbl: '🛠️ بدون شهادة', cnt: cs.no_degree_count || 0, eduGroup: 'nodegree' }
        ];
        bar.replaceChildren(
          el('span', { class: 'lp-wiz-sectors-title' }, `أرقام قطاعات ${countryTag}:`),
          ...items.map((it) => {
            const attrs = {
              type: 'button',
              class: 'lp-wiz-sec-chip' + (isTr ? ' tr' : ''),
              'data-enter-country': c
            };
            if (it.sector) attrs['data-enter-sector'] = it.sector;
            if (it.eduGroup) attrs['data-enter-edu-group'] = it.eduGroup;
            return el('button', attrs, el('span', null, it.lbl), el('b', null, String(it.cnt)));
          })
        );
      }

      if ($('lpCounterSy')) $('lpCounterSy').classList.toggle('is-focus', !isTr);
      if ($('lpCounterTr')) $('lpCounterTr').classList.toggle('is-focus', isTr);
      if ($('lpCounterNgo')) {
        $('lpCounterNgo').setAttribute('data-enter-country', c);
        const b = $('lpCounterNgo').querySelector('[data-lp-country-stat="ngo_count"]');
        if (b) b.textContent = String(cs.ngo_count || 0);
      }
      if ($('lpNgoLabel')) $('lpNgoLabel').textContent = isTr ? 'شواغر منظمات في تركيا 🇹🇷' : 'شواغر منظمات في سوريا 🇸🇾';
      if ($('lpCounterPrivate')) {
        $('lpCounterPrivate').setAttribute('data-enter-country', c);
        const b = $('lpCounterPrivate').querySelector('[data-lp-country-stat="private_count"]');
        if (b) b.textContent = String(cs.private_count || 0);
      }
      if ($('lpPrivateLabel')) $('lpPrivateLabel').textContent = isTr ? 'شركات وقطاع خاص في تركيا 🇹🇷' : 'شركات وقطاع خاص في سوريا 🇸🇾';

      const gwCountrySuffix = isTr ? 'في تركيا 🇹🇷' : 'في سوريا 🇸🇾';
      const gwLinkText = {
        ngo_count: `تصفح شواغر المنظمات ${gwCountrySuffix} ←`,
        gov_count: `تصفح الوظائف الحكومية ${gwCountrySuffix} ←`,
        private_count: `تصفح وظائف الشركات ${gwCountrySuffix} ←`,
        no_degree_count: `تصفح الفرص السريعة ${gwCountrySuffix} ←`
      };
      document.querySelectorAll('[data-gw-card]').forEach((card) => {
        const key = card.dataset.gwCard;
        card.setAttribute('data-enter-country', c);
        const cntEl = card.querySelector('.gw-count');
        if (cntEl) cntEl.textContent = String(cs[key] != null ? cs[key] : 0);
        const lnkEl = card.querySelector('.gw-link');
        if (lnkEl && gwLinkText[key]) lnkEl.textContent = gwLinkText[key];
      });
    };
    window._syncLandingCountry = syncLandingCountry;

    document.querySelectorAll('[data-wiz-country]').forEach((tab) => {
      tab.addEventListener('click', () => syncLandingCountry(tab.dataset.wizCountry || 'sy'));
    });
    document.querySelectorAll('[data-gw-country]').forEach((tab) => {
      tab.addEventListener('click', () => syncLandingCountry(tab.dataset.gwCountry || 'sy'));
    });

    const submitWizard = () => {
      const city = $('lpWizCity') ? $('lpWizCity').value : '';
      const secVal = $('lpWizSector') ? $('lpWizSector').value : '';
      const qVal = $('lpWizQ') ? $('lpWizQ').value.trim() : '';
      const opts = { country: window._wizCountry || 'sy', city, q: qVal };
      if (secVal === 'nodegree') {
        opts.edu_group = 'nodegree';
      } else if (secVal) {
        opts.sector = secVal;
      }
      enterBoard(opts, true);
    };

    if ($('lpWizSubmit')) {
      $('lpWizSubmit').addEventListener('click', submitWizard);
    }
    if ($('lpWizQ')) {
      $('lpWizQ').addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          submitWizard();
        }
      });
    }

    // Delegated click handler for all Landing Page portal/gateway/city/ticker buttons
    document.addEventListener('click', (e) => {
      const trigger = e.target.closest('[data-enter-country],[data-enter-sector],[data-enter-city],[data-enter-edu],[data-enter-edu-group],[data-enter-field]');
      if (!trigger) return;
      e.preventDefault();
      enterBoard({
        country: trigger.dataset.enterCountry || 'sy',
        sector: trigger.dataset.enterSector || '',
        city: trigger.dataset.enterCity || '',
        edu: trigger.dataset.enterEdu || '',
        edu_group: trigger.dataset.enterEduGroup || '',
        field: trigger.dataset.enterField || ''
      }, true);
    });

    // Browser Back/Forward navigation between Landing Page and Jobs Board
    window.addEventListener('popstate', () => {
      const sp = new URLSearchParams(location.search);
      if (sp.get('view') === 'jobs' || sp.get('country') || sp.get('city') || sp.get('q') || location.hash.startsWith('#job-')) {
        enterBoard({
          country: sp.get('country') || 'sy',
          city: sp.get('city') || '',
          sector: sp.get('sector') || '',
          edu: sp.get('edu') || '',
          edu_group: sp.get('edu_group') || '',
          field: sp.get('field') || '',
          q: sp.get('q') || ''
        }, false);
      } else {
        showLanding(false);
      }
    });
  }

  function startRotator(selector, intervalMs, onStep) {
    const spans = [...document.querySelectorAll(selector)];
    if (spans.length < 2) return;
    let i = 0;
    if (typeof onStep === 'function') onStep(spans[0], 0);
    setInterval(() => {
      const prev = spans[i];
      prev.classList.remove('on');
      if (!reduce) {
        prev.classList.add('out');
        setTimeout(() => prev.classList.remove('out'), 450);
      }
      i = (i + 1) % spans.length;
      spans[i].classList.add('on');
      if (typeof onStep === 'function') onStep(spans[i], i);
    }, intervalMs);
  }

  function setupHeadlineRotation() {
    const countryBox = $('lpCountryRot');
    startRotator('#rot span', 2300);
    startRotator('#lpRoleRot span', 2000);
    startRotator('#lpCountryRot span', 2200, (activeSpan) => {
      if (countryBox) {
        countryBox.classList.toggle('is-tr', activeSpan.dataset.rotCountry === 'tr');
      }
    });
  }

  function countUp(node, to) {
    if (reduce) { node.textContent = to; return; }
    const t0 = performance.now(), dur = 1200;
    const step = (t) => {
      const p = Math.min(1, (t - t0) / dur);
      node.textContent = Math.round(to * (1 - Math.pow(1 - p, 3)));
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  function rebuildCityMultiSelect(countryCode) {
    const isTr = countryCode === 'tr';
    const govList = isTr
      ? TURKISH_PROVINCES
      : ((META && META.cities && META.cities.length) ? META.cities : SYRIAN_GOVERNORATES);
    const citySel = $('city');
    if (citySel) {
      citySel.replaceChildren(
        new Option(isTr ? 'كل الولايات والمدن التركية' : 'كل المدن والمحافظات السورية', ''),
        ...govList.map((c) => new Option(c, c))
      );
      citySel.value = '';
    }
    createMultiSelect('city', {
      icon: '📍',
      placeholder: isTr ? 'كل الولايات والمدن التركية' : 'كل المدن والمحافظات السورية',
      headerHint: isTr ? 'اختر ولاية تركية أو أكثر:' : 'اختر محافظة أو أكثر (14 محافظة):',
      twoColumns: true,
      options: govList.map((c) => [c, c]),
      getValue: () => state.city,
      setValue: (csv) => {
        state.city = csv;
        syncFilterUI();
        refresh();
      }
    });

    // Update Board Header & Country Switcher UI
    document.querySelectorAll('[data-board-country]').forEach((b) => {
      const active = b.dataset.boardCountry === (isTr ? 'tr' : 'sy');
      b.classList.toggle('active', active);
      b.setAttribute('aria-selected', String(active));
    });
    if ($('boardCountryTitle')) {
      $('boardCountryTitle').textContent = isTr ? 'في تركيا 🇹🇷' : 'في سوريا 🇸🇾';
    }
    if ($('boardHeroSub')) {
      $('boardHeroSub').textContent = isTr
        ? 'فرص العمل الموثقة في تركيا للسوريين والعرب والكفاءات: المنظمات الدولية، الشركات التركية والعربية، المصانع والورش، والعمل عن بُعد مع وسيلة التقديم المباشرة.'
        : 'فرص العمل الموثقة في سوريا بمكان واحد. شواغر الوزارات، المنظمات الدولية والمحلية، والشركات والمصانع. فلترة دقيقة حسب مؤهلك ومدينتك وطريقة التقديم، بدون إعلانات مكررة أو وهمية.';
    }
    if ($('marketDemandTitle')) {
      $('marketDemandTitle').textContent = isTr
        ? 'أكثر المجالات طلباً حالياً في تركيا 🇹🇷 (اضغط للفلترة السريعة):'
        : 'أكثر المجالات طلباً حالياً في سوريا 🇸🇾 (اضغط للفلترة السريعة):';
    }
  }

  function getCountryStats(countryCode) {
    const c = countryCode === 'tr' ? 'tr' : 'sy';
    if (STATS && STATS.by_country && STATS.by_country[c]) return STATS.by_country[c];
    if (!STATS) return {};
    if (c === 'tr') {
      return {
        open: STATS.open_tr || 0,
        city_counts: STATS.city_counts_tr || {}
      };
    }
    return STATS;
  }

  function updateBoardHeroStats(countryCode) {
    const c = countryCode === 'tr' ? 'tr' : 'sy';
    const cs = getCountryStats(c);
    const box = $('boardHeroCounters');
    if (box) {
      const openNode = box.querySelector('[data-count="board_open"]');
      if (openNode) openNode.textContent = String(cs.open != null ? cs.open : 0);
      box.querySelectorAll('[data-board-stat]').forEach((node) => {
        const k = node.dataset.boardStat;
        node.textContent = String(cs[k] != null ? cs[k] : 0);
      });
    }
    if ($('boardOpenLabel')) {
      $('boardOpenLabel').textContent = c === 'tr' ? 'فرصة مفتوحة في تركيا 🇹🇷' : 'إعلان مفتوح في سوريا 🇸🇾';
    }
  }

  function populateLandingPortals() {
    const syList = (META && META.cities && META.cities.length) ? META.cities : SYRIAN_GOVERNORATES;
    const syStats = getCountryStats('sy');
    const trStats = getCountryStats('tr');
    const syCounts = syStats.city_counts || {};
    const trCounts = trStats.city_counts || {};

    const syGrid = $('lpSyCitiesGrid');
    if (syGrid) {
      syGrid.replaceChildren(...syList.map((city) =>
        el('button', {
          type: 'button',
          class: 'lp-city-btn',
          'data-enter-country': 'sy',
          'data-enter-city': city
        },
          el('span', null, city),
          syCounts[city] ? el('small', null, String(syCounts[city])) : null
        )
      ));
    }

    const trGrid = $('lpTrCitiesGrid');
    if (trGrid) {
      trGrid.replaceChildren(...TURKISH_PROVINCES.map((city) =>
        el('button', {
          type: 'button',
          class: 'lp-city-btn tr',
          'data-enter-country': 'tr',
          'data-enter-city': city
        },
          el('span', null, city),
          trCounts[city] ? el('small', null, String(trCounts[city])) : null
        )
      ));
    }

    document.querySelectorAll('[data-portal-stat]').forEach((node) => {
      const [cCode, statKey] = (node.dataset.portalStat || '').split(':');
      const s = getCountryStats(cCode);
      if (s[statKey] != null) node.textContent = `(${s[statKey]})`;
    });

    if (window._syncLandingCountry) {
      window._syncLandingCountry(window._wizCountry || 'sy');
    }
  }

  function showLanding(pushHistory = true) {
    state.view = 'landing';
    if ($('landingView')) $('landingView').hidden = false;
    if ($('boardView')) $('boardView').hidden = true;
    if (pushHistory) {
      try { history.pushState({ view: 'landing' }, '', location.pathname); } catch (e) {}
    }
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  function enterBoard(opts = {}, pushHistory = true) {
    const nextCountry = opts.country === 'tr' ? 'tr' : 'sy';
    state.view = 'board';
    state.country = nextCountry;
    state.sector = opts.sector || '';
    state.edu_group = opts.edu_group || '';
    state.edu = opts.edu || '';
    state.apply = opts.apply || '';
    state.field = opts.field || '';
    state.sub = '';
    state.city = opts.city || '';
    state.source = '';
    state.q = opts.q || '';
    state.sort = 'relevance';
    state.terms = [];
    state.fresh = false;
    state.saved = false;
    state.offset = 0;

    if ($('landingView')) $('landingView').hidden = true;
    if ($('boardView')) $('boardView').hidden = false;

    rebuildCityMultiSelect(state.country);
    updateBoardHeroStats(state.country);
    syncFilterUI();

    if (pushHistory) {
      const sp = new URLSearchParams();
      sp.set('view', 'jobs');
      sp.set('country', state.country);
      if (state.city) sp.set('city', state.city);
      if (state.sector) sp.set('sector', state.sector);
      if (state.edu) sp.set('edu', state.edu);
      if (state.edu_group) sp.set('edu_group', state.edu_group);
      if (state.field) sp.set('field', state.field);
      if (state.q) sp.set('q', state.q);
      try { history.pushState({ view: 'jobs', country: state.country }, '', `${location.pathname}?${sp.toString()}`); } catch (e) {}
    }

    window.scrollTo({ top: 0, behavior: 'smooth' });
    refresh();
  }

  // ---------------------------------------------------------------- initialization
  async function init() {
    setupHeadlineRotation();
    setupEvents();
    updateSavedCount();

    const oldErr = $('initErrorBanner');
    if (oldErr) oldErr.remove();

    try {
      [META, STATS] = await Promise.all([
        getWithRetry('/meta', 4, 2000),
        getWithRetry('/stats', 4, 2000)
      ]);
    } catch (e) {
      const errBanner = el('div', {
        id: 'initErrorBanner',
        class: 'empty',
        style: 'margin:18px auto;max-width:680px;z-index:99;position:relative'
      },
        el('b', null, 'تعذر الاتصال بالسيرفر مؤقتاً'),
        el('span', null, 'قد يستغرق إيقاظ الخادم السحابي بضع ثوانٍ. اضغط زر إعادة المحاولة.'),
        el('button', {
          type: 'button',
          class: 'btn primary',
          style: 'margin-top:12px',
          onclick: () => { errBanner.remove(); init(); }
        }, 'إعادة المحاولة الآن')
      );
      document.body.prepend(errBanner);
      return;
    }

    // Populate metadata & periodic live sync (every 20 min collector)
    const updateLiveLabel = () => {
      if (STATS && STATS.updated_at) {
        const mins = (META && META.update_minutes) || 20;
        document.querySelectorAll('.updated').forEach((n) => {
          n.textContent = `محدّث ${ago(STATS.updated_at)} · يسحب الشواغر تلقائياً كل ${mins} دقيقة`;
        });
      }
    };
    updateLiveLabel();

    const getStatCounts = () => ({
      open: STATS.open_sy || STATS.open || 0,
      open_tr: STATS.open_tr || 0,
      board_open: state.country === 'tr' ? (STATS.open_tr || 0) : (STATS.open_sy || STATS.open || 0)
    });

    setInterval(async () => {
      try {
        const freshStats = await get('/stats');
        if (freshStats && freshStats.updated_at && freshStats.updated_at !== (STATS && STATS.updated_at)) {
          STATS = freshStats;
          updateLiveLabel();
          const newCounts = getStatCounts();
          document.querySelectorAll('[data-count]').forEach((node) => {
            const k = node.dataset.count;
            if (newCounts[k] != null) node.textContent = newCounts[k];
          });
          populateLandingPortals();
          updateBoardHeroStats(state.country);
          if (state.view === 'board' && state.offset === 0 && !state.saved) refresh();
        } else {
          updateLiveLabel();
        }
      } catch (_) {}
    }, 180000);

    // Counters in Landing & Board Hero
    const statCounts = getStatCounts();
    document.querySelectorAll('[data-count]').forEach((node) => {
      const k = node.dataset.count;
      countUp(node, statCounts[k] != null ? statCounts[k] : 0);
    });

    populateLandingPortals();

    // Populate dropdowns & initialize interactive Multi-Select widgets
    const eduSel = $('eduFilter');
    if (eduSel) {
      eduSel.replaceChildren(
        new Option('كل الشهادات والمؤهلات', ''),
        ...LEVELS.map(([c, l]) => new Option(l, c))
      );
      eduSel.value = '';
    }

    const fldSel = $('fieldFilter');
    if (fldSel) {
      fldSel.replaceChildren(
        new Option('كل مجالات العمل', ''),
        ...META.fields.filter((f) => f.code !== 'other').map((f) => new Option(f.label, f.code))
      );
      fldSel.value = '';
    }

    rebuildCityMultiSelect('sy');

    createMultiSelect('eduFilter', {
      icon: '🎓',
      placeholder: 'كل الشهادات والمؤهلات',
      headerHint: 'اختر مؤهلاً علمياً أو أكثر:',
      twoColumns: false,
      options: LEVELS.map(([c, l]) => [c, l]),
      getValue: () => state.edu,
      setValue: (csv) => {
        state.edu = csv;
        state.edu_group = '';
        syncFilterUI();
        refresh();
      }
    });

    createMultiSelect('fieldFilter', {
      icon: '💼',
      placeholder: 'كل التخصصات والمجالات',
      headerHint: 'اختر تخصصاً أو مجال عمل أو أكثر:',
      twoColumns: false,
      options: (META.fields || []).filter((f) => f.code !== 'other').map((f) => [f.code, f.label]),
      getValue: () => state.field,
      setValue: (csv) => {
        state.field = csv;
        if (!splitCsv(state.field).includes('computer')) state.sub = '';
        syncFilterUI();
        refresh();
      }
    });

    // Determine initial view: Standalone Landing Page by default, or Jobs Board if deep-linked
    const sp = new URLSearchParams(location.search);
    const hashMatch = location.hash.match(/^#job-(\d+)$/);
    if (sp.get('view') === 'jobs' || sp.get('country') || sp.get('city') || sp.get('sector') || sp.get('edu_group') || sp.get('q') || hashMatch) {
      enterBoard({
        country: sp.get('country') || 'sy',
        city: sp.get('city') || '',
        sector: sp.get('sector') || '',
        edu: sp.get('edu') || '',
        edu_group: sp.get('edu_group') || '',
        field: sp.get('field') || '',
        q: sp.get('q') || ''
      }, false);
      if (hashMatch) {
        get('/jobs/' + hashMatch[1]).then(openDetail).catch(() => {});
      }
    } else {
      showLanding(false);
    }
  }

  init();

  if ('serviceWorker' in navigator && location.protocol !== 'file:') {
    navigator.serviceWorker.register('sw.js', { updateViaCache: 'none' }).then((reg) => reg.update()).catch(() => {});
  }
})();
