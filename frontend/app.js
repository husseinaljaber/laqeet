/* لقيت: The complete frontend logic. Direct API integration under /api */
(function () {
  'use strict';
  const API = (window.LAQEET_API || '') + '/api';
  const PAGE = 30;
  const PROFILE_KEY = 'laqeet.profile';
  const SAVED_KEY = 'laqeet.saved';
  const TRACKER_KEY = 'laqeet.tracker';
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const TRACKER_STAGES = [
    { code: 'saved', label: 'محفوظة', ico: 'bookmark-filled', cls: 'st-saved' },
    { code: 'applied', label: 'قدّمت عليها', ico: 'send', cls: 'st-applied' },
    { code: 'interview', label: 'مقابلة', ico: 'calendar', cls: 'st-interview' },
    { code: 'offered', label: 'عرض عمل', ico: 'award', cls: 'st-offered' }
  ];
  const STAGE_MAP = Object.fromEntries(TRACKER_STAGES.map((s) => [s.code, s]));

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
    gov: { label: 'قطاع حكومي', ico: 'landmark', badge: 'رسمي · حكومي' },
    ngo: { label: 'منظمات وهيئات', ico: 'handshake', badge: 'منظمة إنسانية' },
    private: { label: 'قطاع خاص', ico: 'building', badge: 'قطاع خاص' }
  };

  const APPLY_UI = {
    whatsapp: { label: 'واتساب', ico: 'whatsapp' },
    form: { label: 'فورم تقديم', ico: 'file-text' },
    site: { label: 'موقع التوظيف', ico: 'globe' },
    email: { label: 'إيميل', ico: 'mail' },
    call: { label: 'اتصال هاتفي', ico: 'phone' }
  };

  function ico(name, cls) {
    if (window.LaqeetUI && window.LaqeetUI.icon) return window.LaqeetUI.icon(name, cls);
    return '';
  }

  let META = null, STATS = null;
  let profile = loadProfile();
  let savedMap = {};
  let trackerMap = {};
  try { savedMap = JSON.parse(localStorage.getItem(SAVED_KEY) || '{}') || {}; } catch (e) {}
  try { trackerMap = JSON.parse(localStorage.getItem(TRACKER_KEY) || '{}') || {}; } catch (e) {}
  // One-time seamless bridge from legacy savedMap -> trackerMap
  for (const [id, job] of Object.entries(savedMap)) {
    if (job && !trackerMap[id]) {
      trackerMap[id] = { job, stage: 'saved', note: '', updatedAt: new Date().toISOString() };
    }
  }
  for (const [id, entry] of Object.entries(trackerMap)) {
    if (entry && entry.job && !savedMap[id]) savedMap[id] = entry.job;
  }

  const state = {
    view: 'landing',
    country: 'sy',
    sector: '',
    edu_group: '',
    edu: '',
    lower: false,
    source: '',
    apply: '',
    exp: '',
    field: '',
    sub: '',
    city: '',
    q: '',
    sort: 'relevance',
    terms: [],
    fresh: false,
    instant: false,
    saved: false,
    trackerFilter: '',
    offset: 0,
    total: 0
  };
  let manualFieldsOpen = null;
  let pendingFitSort = false;
  let fitCache = null;

  // ---------------------------------------------------------------- helpers
  function $(id) { return document.getElementById(id); }
  const track = (e, k, src) => { if (window.LaqeetUI && window.LaqeetUI.track) window.LaqeetUI.track(e, k, src); };
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
    try { localStorage.setItem(TRACKER_KEY, JSON.stringify(trackerMap)); } catch (e) {}
  }

  function formatShortDay(iso) {
    const s = String(iso || '').slice(0, 10);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) return '';
    const [, m, d] = s.split('-');
    return `${parseInt(d, 10)}/${parseInt(m, 10)}`;
  }

  function isExpiringWithin48h(j) {
    if (!j || !j.deadline) return false;
    const d = daysLeft(j.deadline);
    return d >= 0 && d <= 2;
  }

  function getTrack(id) {
    return trackerMap[String(id)] || null;
  }

  function setTrack(j, stage, note) {
    const k = String(j.id);
    const prev = trackerMap[k] || {};
    const nextStage = STAGE_MAP[stage] ? stage : (prev.stage || 'saved');
    const nextNote = note !== undefined ? String(note).slice(0, 240) : (prev.note || '');
    const nowIso = new Date().toISOString();
    const appliedAt = nextStage === 'saved' ? null : (prev.appliedAt || nowIso);
    trackerMap[k] = { job: j, stage: nextStage, note: nextNote, appliedAt, updatedAt: nowIso };
    savedMap[k] = j;
    saveSaved();
    updateSavedCount();
  }

  function removeTrack(id) {
    const k = String(id);
    delete trackerMap[k];
    delete savedMap[k];
    saveSaved();
    updateSavedCount();
  }

  function isEnglishJob(j) {
    const t = String((j && j.title) || '');
    const arChars = (t.match(/[\u0621-\u064A]/g) || []).length;
    const enChars = (t.match(/[A-Za-z]/g) || []).length;
    if (enChars >= 5 && arChars <= 2) return true;
    const sec = (j && (j.sector || (j.official ? 'gov' : 'private'))) || 'private';
    return sec === 'ngo' && /[A-Za-z]{4,}/.test(t);
  }

  function trustPill(j) {
    const tier = j.trust_tier || (j.verified_employer ? 'verified_employer' : (j.sector === 'gov' ? 'gov' : (j.sector === 'ngo' ? 'ngo' : 'standard')));
    if (tier === 'gov') {
      return el('span', { class: 'trust-pill gov' }, ico('shield-check', 'ico-sm'), 'جهة حكومية رسمية موثّقة');
    }
    if (tier === 'ngo') {
      return el('span', { class: 'trust-pill ngo' }, ico('shield-check', 'ico-sm'), 'منظمة دولية / إنسانية موثّقة');
    }
    if (tier === 'verified_employer') {
      return el('span', { class: 'trust-pill verified' }, ico('badge-check', 'ico-sm'), 'صاحب عمل موثّق على لقيت');
    }
    return null;
  }

  function buildApplyTemplates(j) {
    let cv = {};
    try { cv = JSON.parse(localStorage.getItem('laqeet.cv') || '{}') || {}; } catch (_) {}
    const rawName = (cv.name || '').trim();
    const hasName = Boolean(rawName);
    const myName = rawName || '[اسمك الكامل]';
    const myPhone = (cv.phone || '').trim();
    const myExp = profile.exp != null ? `${profile.exp} سنوات خبرة` : '';
    const myEdu = profile.edu && EDU_LABEL[profile.edu] ? EDU_LABEL[profile.edu] : '';
    const ent = entityMeta(j);
    const hasNamedOrg = Boolean(ent.name && !/^جهة|^شركة\s*\/\s*صاحب|^منظمة\s+دولية/.test(ent.name));
    const orgAr = hasNamedOrg ? `لدى ${ent.name}` : 'لديكم';
    const cityTxt = (j.cities || [])[0] || (j.country === 'tr' ? 'تركيا' : 'سوريا');
    const quals = [myEdu, myExp].filter(Boolean).join(' و');
    const qualLineAr = quals ? ` أمتلك ${quals} ذات صلة بمتطلبات الدور.` : '';
    const introNameAr = hasName ? `اسمي ${rawName}، و` : '';

    const waAr = [
      `السلام عليكم ورحمة الله وبركاته،`,
      `${introNameAr}أتواصل معكم للتقديم على شاغر «${j.title}» ${orgAr} (${cityTxt}) المنشور عبر منصة لقيت.${qualLineAr}`,
      `أرفق لكم السيرة الذاتية للتفضل بالاطلاع، ويسعدني تواصلكم لتحديد موعد مقابلة. شكراً لكم.`,
      myPhone ? `رقم التواصل: ${myPhone}` : null
    ].filter(Boolean).join('\n');

    const emailSubjectAr = `التقدم لشاغر: ${j.title} — ${cityTxt}${hasName ? ' (' + rawName + ')' : ''}`;
    const emailBodyAr = [
      `السادة في قسم الموارد البشرية ${orgAr} المحترمين،`,
      `تحية طيبة وبعد،`,
      ``,
      `أود التقدم بطلبي لشغل شاغر «${j.title}» في ${cityTxt}.${qualLineAr}`,
      `أرفق لكم سيرتي الذاتية المحدثة للاطلاع على مؤهلاتي وخبراتي العملية، ولدي جاهزية كاملة للالتزام بمهام الوظيفة والانضمام إلى فريقكم، وأتطلع لفرصة إجراء مقابلة عمل.`,
      ``,
      `مع خالص التقدير والاحترام،`,
      hasName ? rawName : myName,
      myPhone || null
    ].filter((x) => x !== null).join('\n');

    const orgEn = ent.name && /[A-Za-z]/.test(ent.name) ? ent.name : 'your organization';
    const emailSubjectEn = `Application for ${j.title} Position — ${cityTxt}${hasName ? ' (' + rawName + ')' : ''}`;
    const emailBodyEn = [
      `Dear Hiring Team at ${orgEn},`,
      ``,
      `I am writing to express my strong interest in the "${j.title}" position (${cityTxt}).`,
      `Please find my updated CV attached for your review. I am confident that my qualifications and practical experience align well with the role requirements, and I would welcome the opportunity to discuss how I can contribute to your team in an interview.`,
      ``,
      `Thank you for your time and consideration.`,
      ``,
      `Best regards,`,
      hasName ? rawName : '[Your Full Name]',
      myPhone || null
    ].filter((x) => x !== null).join('\n');

    return { waAr, emailSubjectAr, emailBodyAr, emailSubjectEn, emailBodyEn };
  }

  function mailtoHref(j, forceLang) {
    if (!j || !j.email) return '';
    const tpls = buildApplyTemplates(j);
    const useEn = forceLang ? forceLang === 'en' : isEnglishJob(j);
    const subj = useEn ? tpls.emailSubjectEn : tpls.emailSubjectAr;
    const body = useEn ? tpls.emailBodyEn : tpls.emailBodyAr;
    return `mailto:${encodeURIComponent(j.email)}?subject=${encodeURIComponent(subj)}&body=${encodeURIComponent(body)}`;
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
    if (!t) return;
    t.replaceChildren(ico('check-circle'), el('span', null, msg));
    t.hidden = false;
    t.style.animation = 'none';
    void t.offsetWidth;
    t.style.animation = '';
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
    if (h < 24) return h === 1 ? 'قبل ساعة' : h === 2 ? 'قبل ساعتين' : h <= 10 ? `قبل ${h} ساعات` : `قبل ${h} ساعة`;
    const d = Math.round(h / 24);
    return d === 1 ? 'أمس' : d === 2 ? 'قبل يومين' : d <= 10 ? `قبل ${d} أيام` : `قبل ${d} يوماً`;
  }

  const expBand = (e) => e == null ? 'unk' : e <= 2 ? 'low' : e < 5 ? 'mid' : 'high';
  const EXP_UI = {
    unk: { label: 'خبرة غير مشروطة' },
    low: { label: 'خبرة حتى سنتين' },
    mid: { label: 'خبرة 3–4 سنين' },
    high: { label: 'خبرة 5+ سنين' }
  };
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

  function hasProfile() {
    return Boolean(profile && (profile.edu || profile.exp != null || profile.age || profile.gender));
  }

  function fitScore(j) {
    const m = fit(j);
    if (!m) return 0;
    let score = m.level === 'ok' ? 1000 : m.level === 'maybe' ? 500 : 0;
    if (profile.edu && j.education) {
      if (j.education === profile.edu) score += 80;
      else if (EDU_RANK[j.education] <= EDU_RANK[profile.edu]) score += 35;
    } else if (profile.edu && !j.education) {
      score += 10;
    }
    if (profile.exp != null && profile.exp !== '') {
      if (j.experience != null && j.experience <= profile.exp) score += 25;
      else if (j.experience == null) score += 10;
    }
    if (j.posted_at) score += (Date.parse(j.posted_at) || 0) / 1e13;
    return score;
  }

  // ---------------------------------------------------------------- cards & links
  function waLink(j) {
    const msg = buildApplyTemplates(j).waAr;
    const p = (j.phones || []).find(isMobile);
    if (p) {
      return `https://wa.me/${intlPhone(p)}?text=${encodeURIComponent(msg)}`;
    }
    if (j.apply_url && /^https?:\/\/(?:wa\.me\/|api\.whatsapp\.com\/)/i.test(j.apply_url)) {
      const m = j.apply_url.match(/wa\.me\/(\d{7,15})/i);
      if (m) return `https://wa.me/${m[1]}?text=${encodeURIComponent(msg)}`;
      try {
        const u = new URL(j.apply_url);
        const ph = (u.searchParams.get('phone') || '').replace(/\D+/g, '');
        if (ph.length >= 7) return `https://wa.me/${ph}?text=${encodeURIComponent(msg)}`;
        u.searchParams.set('text', msg);
        return u.toString();
      } catch {
        return j.apply_url.split('?')[0] + `?text=${encodeURIComponent(msg)}`;
      }
    }
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
    const isTr = j.country === 'tr';
    const multiLbl = isTr ? 'عدة ولايات في تركيا' : 'عدة محافظات';
    const cs = Array.isArray(j.cities) ? [...j.cities] : [];
    if (!cs.length) return isTr ? 'تركيا (عام)' : 'سوريا (عام)';
    const selCities = splitCsv(state.city);
    if (selCities.length) {
      const matched = cs.filter((c) => selCities.includes(c));
      if (matched.length) {
        const rest = cs.filter((c) => !selCities.includes(c));
        return rest.length ? `${matched.join('، ')} (+${rest.length})` : matched.join('، ');
      }
      if (cs.includes(multiLbl)) {
        return `${selCities.slice(0, 2).join('، ')} (شامل)`;
      }
    }
    if (cs.length <= 3) return cs.join('، ');
    return `${cs.slice(0, 2).join('، ')} (+${cs.length - 2})`;
  }

  function formatDate(iso) {
    const s = String(iso || '').trim();
    return /^\d{4}-\d{2}-\d{2}/.test(s) ? s.slice(0, 10) : '';
  }

  function entityMeta(j) {
    const sec = j.sector || (j.official ? 'gov' : 'private');
    const emp = cleanEmployer(j);
    if (sec === 'gov') {
      return { icon: '🏛️', ico: 'landmark', role: 'الوزارة / الجهة', name: emp || 'جهة حكومية رسمية' };
    }
    if (sec === 'ngo') {
      return { icon: '🤝', ico: 'handshake', role: 'المنظمة', name: emp || 'منظمة دولية / إنسانية' };
    }
    return { icon: '🏢', ico: 'building', role: 'الشركة / الجهة', name: emp || 'شركة / صاحب عمل (قطاع خاص)' };
  }

  function visibleSources(j) {
    const sec = j.sector || (j.official ? 'gov' : 'private');
    if (sec !== 'gov' && sec !== 'ngo') return [];
    return (j.sources || []).filter((s) => Boolean(s && s.official_public));
  }

  function badges(j) {
    const b = [
      el('span', { class: 'b exp' }, expText(j.experience))
    ];
    if (j.education) {
      b.push(el('span', { class: 'b' }, ico('graduation-cap', 'ico-sm'), (EDU_LABEL[j.education] || j.education) + (j.education_from === 'grade' && j.grade ? ` (فئة ${j.grade})` : '')));
    }
    if (j.field && j.field !== 'other') {
      b.push(el('span', { class: 'b' }, ico('briefcase', 'ico-sm'), j.subfield ? subLabel(j.subfield) : fieldLabel(j.field)));
    }
    if (j.specialty) {
      b.push(el('span', { class: 'b' }, ico('tag', 'ico-sm'), j.specialty.slice(0, 45)));
    }
    if (j.salary) {
      b.push(el('span', { class: 'b' }, ico('coins', 'ico-sm'), 'الأجر: ' + j.salary.slice(0, 35)));
    }
    if (j.shift) {
      b.push(el('span', { class: 'b' }, ico('clock', 'ico-sm'), j.shift.slice(0, 40)));
    }
    if (j.age_min || j.age_max) {
      b.push(el('span', { class: 'b' }, ico('user', 'ico-sm'), `العمر ${j.age_min || '؟'}–${j.age_max || '؟'}`));
    }
    if (j.gender) {
      b.push(el('span', { class: 'b' }, ico('users', 'ico-sm'), j.gender === 'female' ? 'إناث فقط' : 'ذكور فقط'));
    }
    (j.tags || []).forEach((t) => b.push(el('span', { class: 'b' }, t)));
    return b;
  }

  function statusOf(j) {
    const fresh = j.posted_at && Date.now() - new Date(j.posted_at) < 864e5;
    if (j.deadline) {
      const d = daysLeft(j.deadline);
      if (d < 0) return el('span', { class: 'status' }, 'انتهى التقديم');
      if (d === 0) return el('span', { class: 'status soon' }, ico('alert', 'ico-sm'), 'آخر موعد اليوم!');
      return el('span', { class: 'status ' + (d <= 3 ? 'soon' : fresh ? 'new' : 'open') },
        (fresh ? 'جديد · ' : '') + `متبقي ${d} ${d <= 10 ? 'أيام' : 'يوماً'}`);
    }
    return el('span', { class: 'status ' + (fresh ? 'new' : 'open') }, (fresh ? 'جديد · ' : 'نُشر ') + ago(j.posted_at));
  }

  function toggleSave(j) {
    const trk = getTrack(j.id);
    if (trk && trk.stage === 'saved') {
      removeTrack(j.id);
      toast('تمت إزالة الشاغر من المحفوظات');
    } else if (trk && trk.stage !== 'saved') {
      setTrack(j, 'saved');
      toast('تم نقل الشاغر إلى «المحفوظة للتقديم»');
    } else {
      setTrack(j, 'saved');
      toast('تم حفظ الشاغر في «المحفوظة للتقديم» ⭐');
    }
    if (state.saved) refresh();
  }

  function toggleApplied(j) {
    const trk = getTrack(j.id);
    if (trk && trk.stage !== 'saved') {
      setTrack(j, 'saved');
      toast('تمت إعادة الحالة إلى «محفوظة للتقديم»');
    } else {
      setTrack(j, 'applied');
      const dayLbl = formatShortDay(new Date().toISOString());
      toast(`تم تعليم الشاغر كـ «قدّمت عليها ✅» (${dayLbl})`);
    }
    if (state.saved) refresh();
  }

  function getExpiringSavedEntries() {
    return Object.values(trackerMap).filter(
      (e) => e && e.job && e.stage === 'saved' && isExpiringWithin48h(e.job)
    );
  }

  function renderExpiringSavedBanner() {
    const banner = $('expiringSavedBanner');
    if (!banner) return;
    const expiring = getExpiringSavedEntries();
    if (!expiring.length) {
      banner.hidden = true;
      banner.replaceChildren();
      return;
    }
    const n = expiring.length;
    const countTxt = n === 1
      ? 'لديك وظيفة محفوظة واحدة ينتهي التقديم عليها خلال 48 ساعة ولم تقدّم عليها بعد!'
      : n === 2
        ? 'لديك وظيفتان محفوظتان ينتهي التقديم عليهما خلال 48 ساعة ولم تقدّم عليهما بعد!'
        : `لديك ${n} وظائف محفوظة ينتهي التقديم عليها خلال 48 ساعة ولم تقدّم عليها بعد!`;
    banner.hidden = false;
    banner.replaceChildren(
      el('div', { class: 'expiring-saved-msg' },
        ico('alert', 'ico-sm'),
        el('span', null, countTxt)
      ),
      el('button', {
        type: 'button',
        class: 'expiring-saved-btn',
        onclick: () => {
          state.saved = true;
          state.trackerFilter = 'expiring';
          syncFilterUI();
          refresh();
        }
      }, ico('hourglass', 'ico-sm'), el('span', null, 'عرضها للتقديم الآن'))
    );
  }

  function updateSavedCount() {
    const entries = Object.values(trackerMap).filter((e) => e && e.job);
    const savedCnt = entries.filter((e) => e.stage === 'saved').length;
    const appliedCnt = entries.filter((e) => e.stage !== 'saved').length;
    if ($('savedCount')) $('savedCount').textContent = savedCnt;
    if ($('appliedCount')) $('appliedCount').textContent = appliedCnt;
    renderExpiringSavedBanner();
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

  const PLACEHOLDER_EMP = new Set([
    'غير معلن', 'اعلانات', 'إعلانات', 'شواغر سوريا', '',
    'منظمات دولية ووكالات أممية (reliefweb)',
    'منصة ورك لينك (worklink syria)',
    'منصة ساحة المنظمات (ngo square)',
    'منصة فرص عمل في سوريا',
    'موقع فرص عمل في تركيا',
    'موقع وظائف سوريا (syria jobs)',
    'منصة سلام جوبز',
    'منصة وظيفتورك'
  ]);
  const PORTAL_EMP_RE = /^(?:جهة\s*توظيف|منظمة\s*أممية\s*\/\s*دولية|فرص\s*عمل|شواغر|وظائف|توظيف)(?:\s+\S+){0,2}\s+عبر\s+.+$/i;
  const WRAPPER_EMP_RE = /(?:job\s*in\s*syria|worklink|ngos?\s*syria|ngosquare|reliefweb|unjobs|unjobnet|syria-?jobs\.com|shamhr|forsa-?sy|salam\s*jobs|jobsintr|adwhit|ab-ilan|sqrjobs|nsjobs|jobsyria|wazifturk|وظيفتورك)/i;

  const UNICODE_JUNK_RE = /[\p{Extended_Pictographic}\uFE0F\u200D\u200B-\u200F\u202A-\u202E\u20E3\uFFFD]|[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/gu;
  const CARD_EDU_LABEL = {
    none: 'بدون شهادة',
    basic: 'تعليم أساسي',
    secondary: 'ثانوي',
    institute: 'معهد تقاني',
    bachelor: 'بكالوريوس',
    master: 'ماجستير',
    phd: 'دكتوراه'
  };
  const CARD_FIELD_LABEL = {
    computer: 'تقنية وبرمجة',
    engineering: 'هندسة وعمارة',
    health: 'طب وصحة',
    business: 'محاسبة ومالية',
    sales: 'مبيعات وتسويق',
    education: 'تعليم وتدريب',
    media: 'إعلام وتصميم',
    law: 'حقوق وقانون',
    ngo: 'عمل إنساني',
    hospitality: 'مطاعم وفنادق',
    retail: 'محلات ومتاجر',
    industry: 'معامل وصناعة',
    driving: 'نقل وتوصيل',
    security: 'حراسة وأمن',
    cleaning: 'نظافة وخدمات',
    care: 'رعاية الأطفال',
    translation: 'ترجمة ولغات'
  };

  function stripUnicodeJunk(s) {
    return String(s || '')
      .replace(/[0-9#*]\uFE0F?\u20E3/g, ' ')
      .replace(UNICODE_JUNK_RE, ' ')
      .replace(/\s{2,}/g, ' ')
      .trim();
  }

  function cleanEmployer(j) {
    const raw = stripUnicodeJunk(j.employer || '').replace(/\s+(?:عبر|عن)$/u, '').trim();
    if (!raw || PLACEHOLDER_EMP.has(raw.toLowerCase()) || PORTAL_EMP_RE.test(raw) || WRAPPER_EMP_RE.test(raw)) {
      return j.official ? 'جهة حكومية رسمية' : '';
    }
    return raw;
  }

  function cleanSummary(s) {
    if (!s) return '';
    return stripUnicodeJunk(s)
      .replace(/(?:\s*[·•\-|]\s*|\s+)المصدر\s*[:：]\s*[^·\n]+/gi, '')
      .replace(/\s*لدى\s+(?:جهة\s*توظيف|منظمة\s*أممية(?:\s*\/\s*دولية)?)\s+عبر\s+(?:job\s*in\s*syria|worklink|ngos?\s*syria|ngosquare|reliefweb|[^\s·,،.]+)[^·\n]*/gi, '')
      .replace(/\s*عبر\s+(?:منصة|موقع|قناة|بوابة)\s+(?:فريلانس\s*سوريا|ورك\s*لينك|ساحة\s*المنظمات|سلام\s*جوبز|وظائف\s*سوريا|فرص\s*عمل(?:\s+في\s+(?:تركيا|سوريا))?(?:\s+الرسمية)?|ngo(?:s)?\s*syria|job\s*in\s*syria|worklink|salam\s*jobs|jobsintr|reliefweb|unjobnet|syria-?jobs)[^·\n]*/gi, '')
      .replace(/^(?:\s*[·•\-]\s*)+|(?:\s*[·•\-]\s*)+$/g, '')
      .replace(/\s{2,}/g, ' ')
      .trim();
  }

  function primaryJobLink(j) {
    if (j.apply_url && !/^https?:\/\/(?:wa\.me\/|api\.whatsapp\.com\/)/i.test(j.apply_url)) return j.apply_url;
    return `${location.origin}/j/${j.id}`;
  }

  function buildShareText(j) {
    const ent = entityMeta(j);
    const defaultCountry = j.country === 'tr' ? 'تركيا (عام)' : 'سوريا (عام)';
    const cities = (j.cities && j.cities.length) ? j.cities.join('، ') : defaultCountry;
    const roleLabel = j.field && j.field !== 'other' ? (j.subfield ? subLabel(j.subfield) : fieldLabel(j.field)) : '';
    const edu = j.education ? (EDU_LABEL[j.education] || j.education) : 'غير مشترط / غير مذكور';
    const pubDay = formatDate(j.posted_at);
    const endDay = formatDate(j.deadline || j.expires_on);
    const lines = [
      `📢 فرصة عمل: ${j.title}`,
      `${ent.icon} ${ent.role}: ${ent.name}`,
      `📍 المدينة: ${cities}`,
      roleLabel ? `💼 المجال: ${roleLabel}` : null,
      `🎓 المؤهل العلمي: ${edu}`,
      j.specialty ? `🎯 التخصص: ${j.specialty}` : null,
      `⏱️ الخبرة: ${expText(j.experience)}`,
      j.salary ? `💰 الراتب / الأجر: ${j.salary}` : null,
      j.shift ? `🕒 الدوام: ${j.shift}` : null,
      pubDay ? `🗓️ تاريخ النشر: ${pubDay}` : null,
      endDay ? `📅 ${j.deadline ? 'تاريخ الانتهاء' : 'متاح حتى'}: ${endDay}` : null
    ].filter(Boolean);

    const shareSummary = cleanSummary(j.summary);
    if (shareSummary) {
      lines.push('', `📝 وصف وتفاصيل العمل:\n${shareSummary}`);
    }
    if (j.tasks && j.tasks.length) {
      lines.push('', `📌 المهام والمسؤوليات:\n${j.tasks.slice(0, 5).map((t) => '• ' + t).join('\n')}`);
    }
    if (j.skills && j.skills.length) {
      lines.push('', `✅ الشروط والمهارات:\n${j.skills.slice(0, 5).map((s) => '• ' + s).join('\n')}`);
    }

    // Put ONLY the actual application method requested by the employer (link vs email CV vs phone/WhatsApp)
    const hasDirectLink = Boolean(j.apply_url && !/^https?:\/\/(?:wa\.me\/|api\.whatsapp\.com\/)/i.test(j.apply_url));
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
    lines.push('', `🌐 عبر منصة لقيت: ${location.origin}/j/${j.id}`);
    return lines.join('\n');
  }

  function shareJob(j) {
    track('share');
    const pageUrl = `${location.origin}/j/${j.id}`;
    const hasDirectLink = Boolean(j.apply_url && !/^https?:\/\/(?:wa\.me\/|api\.whatsapp\.com\/)/i.test(j.apply_url));
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
        el('h2', { id: 'shareDialogTitle' }, ico('share'), 'مشاركة ونسخ الإعلان'),
        el('button', { class: 'close', 'aria-label': 'إغلاق', onclick: () => dlg.close() }, ico('x'))
      ),
      el('p', { class: 'drawer-desc' }, 'نص الإعلان جاهز للنسخ والنشر مع الوصف وطريقة التقديم الأساسية المحددة في الإعلان (إيميل CV أو رابط تقديم أو رقم تواصل):'),
      previewEl,
      el('div', { class: 'share-url-row' },
        urlInput,
        el('button', {
          type: 'button',
          class: 'btn ghost',
          onclick: () => copyText(linkToCopy, hasDirectLink ? 'تم نسخ رابط التقديم المباشر' : 'تم نسخ رابط الإعلان')
        }, ico('link'), hasDirectLink ? 'نسخ رابط التقديم' : 'نسخ رابط الإعلان')
      ),
      el('div', { class: 'share-actions' },
        el('button', {
          type: 'button',
          class: 'btn primary',
          onclick: () => copyText(previewEl.value, 'تم نسخ الإعلان كاملاً مع الوصف')
        }, ico('copy'), 'نسخ الإعلان مع الوصف'),
        j.email ? el('button', {
          type: 'button',
          class: 'btn ghost',
          onclick: () => copyText(j.email, 'تم نسخ البريد الإلكتروني للتقديم')
        }, ico('mail'), 'نسخ الإيميل فقط') : null,
        el('button', {
          type: 'button',
          class: 'btn ghost',
          onclick: () => window.open('https://wa.me/?text=' + encodeURIComponent(previewEl.value), '_blank', 'noopener')
        }, ico('whatsapp'), 'إرسال عبر واتساب'),
        el('button', {
          type: 'button',
          class: 'btn ghost',
          onclick: () => window.open('https://t.me/share/url?url=' + encodeURIComponent(linkToCopy) + '&text=' + encodeURIComponent(previewEl.value), '_blank', 'noopener')
        }, ico('send'), 'إرسال عبر تيليجرام'),
        navigator.share ? el('button', {
          type: 'button',
          class: 'btn ghost',
          onclick: () => navigator.share({ title: j.title, text: previewEl.value }).catch(() => {})
        }, ico('share'), 'مشاركة عبر الجهاز') : null
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

  function portalRequiresAccount(j) {
    if (!j || !j.apply_url) return false;
    if (j.url_requires_account) return true;
    return /(?:moad\.gov\.sy|ssf\.gov\.sy|powerholding-intl\.com|myworkdayjobs\.com|myworkdaysite\.com|oraclecloud\.com|successfactors\.|taleo\.net|csod\.com|inspira\.un\.org|careers\.un\.org|careers\.unops\.org|jobs\.unicef\.org|jobs\.undp\.org|jobs\.fao\.org|careers\.who\.int|jobs\.iom\.int|jobs\.oxfam\.org|careers\.icmpd\.org|jobs\.ebrd\.com|careers\.ifrc\.org|icims\.com|webcruiter\.no|hr-manager\.net|jobs\.jobvite\.com|recruiting2\.ultipro\.com)/i.test(j.apply_url);
  }

  function links(j, big) {
    const out = el('div', { class: 'links' });
    const wa = waLink(j);
    if (wa) {
      out.append(el('a', { class: 'wa', href: wa, target: '_blank', rel: 'noopener', 'data-trk': 'whatsapp', 'data-src': j.source }, ico('whatsapp', 'ico-sm'), 'راسل واتساب'));
    }
    const phone = (j.phones || [])[0];
    if (phone && !j.whatsapp) {
      out.append(el('a', { class: 'call', href: 'tel:' + phone, 'data-trk': 'call', 'data-src': j.source }, ico('phone', 'ico-sm'), 'اتصل ' + phone));
    }
    if (j.apply_url && !/^https?:\/\/(?:wa\.me\/|api\.whatsapp\.com\/)/i.test(j.apply_url) && !(j.deadline && daysLeft(j.deadline) < 0)) {
      const isForm = (j.apply_types || []).includes('form');
      const needsAcc = portalRequiresAccount(j);
      const lbl = needsAcc ? 'بوابة رسمية (بحساب)' : (isForm ? 'تعبئة استمارة التقديم' : 'التقديم عبر الموقع');
      const icName = needsAcc ? 'lock' : (isForm ? 'file-text' : 'external-link');
      out.append(el('a', {
        class: needsAcc ? 'apply portal-account' : 'apply',
        href: j.apply_url,
        target: '_blank',
        rel: 'noopener',
        'data-trk': needsAcc ? 'portal' : (isForm ? 'form' : 'site'),
        'data-src': j.source,
        ...(needsAcc ? { title: 'يتطلب إنشاء حساب مجاني أو تسجيل دخول على البوابة الرسمية للجهة المعلنة' } : {})
      }, ico(icName, 'ico-sm'), lbl));
    }
    const isWrapperUrl = (u) => /(?:^|\.)(?:worklink\.sy|jobinsyria\.com|reliefweb\.int|unjobs\.org|unjobnet\.org|ngosquare\.com|jobsyria\.org|nsjobs\.net|ngossyria\.com|sqrjobs\.net|syria-jobs\.com|shamhr\.com|forsa-sy\.com|jobsintr\.net|salamjobs\.com|adwhit\.com|ab-ilan\.com|wazifturk\.com|mahereen\.com|spc\.sy)(?:\/|$)/i.test((() => { try { return new URL(u).hostname; } catch { return ''; } })());
    if (j.details_url && j.details_url !== j.apply_url && !isWrapperUrl(j.details_url)) {
      out.append(el('a', { href: j.details_url, target: '_blank', rel: 'noopener' }, ico('external-link', 'ico-sm'), 'تفاصيل الإعلان'));
    }
    if (j.email) {
      out.append(el('a', { href: mailtoHref(j), 'data-trk': 'email', 'data-src': j.source }, ico('mail', 'ico-sm'), 'إرسال CV بالإيميل (مع رسالة جاهزة)'));
    }
    visibleSources(j).slice(0, big ? 6 : 1).forEach((s) => {
      out.append(el('a', { href: s.post_url, target: '_blank', rel: 'noopener', 'data-trk-src': s.source }, 'المصدر: ' + s.label));
    });

    const curTrk = getTrack(j.id);
    const isSav = Boolean(curTrk && curTrk.stage === 'saved');
    const isApp = Boolean(curTrk && curTrk.stage !== 'saved');
    const appDay = isApp ? formatShortDay(curTrk.appliedAt || curTrk.updatedAt) : '';

    const saveBtn = el('button', {
      type: 'button',
      class: 'report save-toggle' + (isSav ? ' is-saved' : ''),
      onclick: (e) => {
        toggleSave(j);
        const nowTrk = getTrack(j.id);
        const nowSav = Boolean(nowTrk && nowTrk.stage === 'saved');
        const nowApp = Boolean(nowTrk && nowTrk.stage !== 'saved');
        e.currentTarget.classList.toggle('is-saved', nowSav);
        e.currentTarget.replaceChildren(ico(nowSav ? 'bookmark-filled' : 'bookmark', 'ico-sm'), nowSav ? 'محفوظة' : 'احفظ');
        appliedBtn.classList.toggle('is-applied', nowApp);
        appliedBtn.replaceChildren(ico('check-circle', 'ico-sm'), nowApp ? `قدّمت (${formatShortDay(nowTrk.appliedAt || nowTrk.updatedAt)})` : 'قدّمت عليها');
        const cardEl = e.currentTarget.closest('.card');
        if (cardEl && !state.saved) {
          const pillSlot = cardEl.querySelector('.card-track-slot');
          if (pillSlot) pillSlot.replaceChildren(...buildCardTrackBadges(j));
        }
      }
    }, ico(isSav ? 'bookmark-filled' : 'bookmark', 'ico-sm'), isSav ? 'محفوظة' : 'احفظ');

    const appliedBtn = el('button', {
      type: 'button',
      class: 'report applied-toggle' + (isApp ? ' is-applied' : ''),
      onclick: (e) => {
        toggleApplied(j);
        const nowTrk = getTrack(j.id);
        const nowSav = Boolean(nowTrk && nowTrk.stage === 'saved');
        const nowApp = Boolean(nowTrk && nowTrk.stage !== 'saved');
        e.currentTarget.classList.toggle('is-applied', nowApp);
        e.currentTarget.replaceChildren(ico('check-circle', 'ico-sm'), nowApp ? `قدّمت (${formatShortDay(nowTrk.appliedAt || nowTrk.updatedAt)})` : 'قدّمت عليها');
        saveBtn.classList.toggle('is-saved', nowSav);
        saveBtn.replaceChildren(ico(nowSav ? 'bookmark-filled' : 'bookmark', 'ico-sm'), nowSav ? 'محفوظة' : 'احفظ');
        const cardEl = e.currentTarget.closest('.card');
        if (cardEl && !state.saved) {
          const pillSlot = cardEl.querySelector('.card-track-slot');
          if (pillSlot) pillSlot.replaceChildren(...buildCardTrackBadges(j));
        }
      }
    }, ico('check-circle', 'ico-sm'), isApp ? `قدّمت (${appDay})` : 'قدّمت عليها');

    const hasDirectLink = Boolean(j.apply_url && !/^https?:\/\/(?:wa\.me\/|api\.whatsapp\.com\/)/i.test(j.apply_url));
    out.append(
      saveBtn,
      appliedBtn,
      el('button', { type: 'button', class: 'report share-btn', onclick: () => shareJob(j) }, ico('share', 'ico-sm'), 'مشاركة ونسخ'),
      j.email && !hasDirectLink
        ? el('button', { type: 'button', class: 'report', onclick: () => copyText(j.email, 'تم نسخ إيميل التقديم') }, ico('mail', 'ico-sm'), 'نسخ الإيميل')
        : el('button', { type: 'button', class: 'report', onclick: () => copyText(primaryJobLink(j), 'تم نسخ الرابط') }, ico('link', 'ico-sm'), 'نسخ الرابط'),
      el('button', { type: 'button', class: 'report', onclick: () => report(j) }, ico('flag', 'ico-sm'), 'بلّغ')
    );
    return out;
  }

  function renderSkeletons(count = 6) {
    const frag = [];
    for (let i = 0; i < count; i++) {
      frag.push(
        el('article', { class: 'card skeleton-card', 'aria-hidden': 'true' },
          el('div', { class: 'skel-row' },
            el('div', { class: 'skel skel-pill' }),
            el('div', { class: 'skel skel-pill' })
          ),
          el('div', { class: 'skel skel-title' }),
          el('div', { class: 'skel skel-box' }),
          el('div', { class: 'skel skel-desc' }),
          el('div', { class: 'skel skel-foot' })
        )
      );
    }
    $('grid').replaceChildren(...frag);
  }

  function buildCardTrackBadges(j) {
    const out = [];
    if (j.verified_employer) {
      out.push(el('span', { class: 'card-verified-chip' }, ico('badge-check', 'ico-sm'), 'صاحب عمل موثّق على لقيت'));
    }
    const trk = getTrack(j.id);
    if (!trk) return out;
    const stageInfo = STAGE_MAP[trk.stage] || STAGE_MAP.saved;
    if (!state.saved) {
      if (trk.stage !== 'saved') {
        const dayLbl = formatShortDay(trk.appliedAt || trk.updatedAt);
        out.push(
          el('span', { class: 'tracker-stage-pill ' + stageInfo.cls },
            ico(stageInfo.ico, 'ico-sm'),
            dayLbl ? `${stageInfo.label} (${dayLbl})` : stageInfo.label
          )
        );
      } else {
        out.push(
          el('span', { class: 'tracker-stage-pill st-saved' },
            ico('bookmark-filled', 'ico-sm'),
            'محفوظة للتقديم'
          )
        );
      }
    }
    if (trk.stage === 'saved' && isExpiringWithin48h(j)) {
      const d = daysLeft(j.deadline);
      const leftTxt = d === 0 ? 'اليوم' : d === 1 ? 'خلال 24 ساعة' : 'خلال 48 ساعة';
      out.push(
        el('span', { class: 'tracker-expiring-pill' },
          ico('alert', 'ico-sm'),
          `ينتهي التقديم ${leftTxt} ولم تقدّم بعد!`
        )
      );
    }
    return out;
  }

  function cleanCardSummary(j, ent, cityTxt) {
    const taskTxt = (j.tasks && j.tasks.length) ? j.tasks.slice(0, 2).join(' · ') : '';
    const subSkills = (j.skills || []).filter((s) => !/^(?:خريج|شهاد[ةه]|الحصول\s+على\s+شهاد[ةه])/i.test(String(s).trim()));
    const skillTxt = subSkills.length ? subSkills.slice(0, 2).join(' · ') : ((j.skills && j.skills.length) ? j.skills.slice(0, 2).join(' · ') : '');
    const raw = cleanSummary(j.summary || taskTxt || skillTxt);
    let txt = stripUnicodeJunk(raw)
      .replace(/^\s*(?:فرص[ةه]?\s+عمل|إعلان\s+توظيف)[^|·\n]*[|·]\s*(?:[^|·\n]*[|·]\s*)?/i, '')
      .replace(/^(?:\s*[·•\-|،,:]\s*)+/g, '')
      .replace(/\s{2,}/g, ' ')
      .trim();
    if (j.education && !/\(الفئة/.test(txt)) {
      txt = txt
        .replace(/(?:^|·\s*)المؤهل:\s*[^·]+/g, '')
        .replace(/(?:^|·\s*)الخبرة:\s*غير مطلوبة/g, '')
        .replace(/^(?:\s*·\s*)+|(?:\s*·\s*)+$/g, '')
        .trim();
    }
    if (!txt || txt.length < 16) {
      const alt = taskTxt || skillTxt || [
        j.shift ? 'الدوام: ' + j.shift : '',
        j.salary ? 'الراتب: ' + j.salary : '',
        j.email ? 'للتقديم: إرسال السيرة الذاتية إلى ' + j.email : '',
        j.deadline ? 'آخر موعد للتقديم: ' + formatDate(j.deadline) : ''
      ].filter(Boolean).join(' · ');
      txt = cleanSummary(alt) || 'اضغط على «المواصفات» لعرض شروط العمل وطريقة التقديم.';
    }
    if (txt.length > 165) {
      const clauses = txt.split(' · ').map((c) => c.trim()).filter(Boolean);
      const kept = [];
      for (const c of clauses) {
        const next = kept.concat(c).join(' · ');
        if (next.length <= 165 || !kept.length) kept.push(c);
        else break;
      }
      txt = kept.join(' · ');
      if (txt.length > 165) {
        let cut = txt.slice(0, 165);
        const sp = cut.lastIndexOf(' ');
        if (sp > 90) cut = cut.slice(0, sp);
        txt = cut.replace(/[·•\-:،,\s]+$/u, '');
      }
    }
    return txt.replace(/\s+(?:في|من|إلى|عن|مع|على)\s*$/u, '').trim();
  }

  function formatCardSalary(raw) {
    let s = stripUnicodeJunk(raw)
      .replace(/ليرة\s+سورية\s+جديدة/g, 'ل.س جديدة')
      .replace(/ليرة\s+سورية/g, 'ل.س')
      .replace(/ليرة\s+تركية/g, 'ل.ت')
      .replace(/دولار\s+أمريكي/g, '$');
    if (s.length > 26) {
      let cut = s.slice(0, 26);
      const sp = cut.lastIndexOf(' ');
      if (sp > 10) cut = cut.slice(0, sp);
      s = cut.trim();
    }
    return s;
  }

  function cardBadges(j) {
    const b = [
      el('span', { class: 'b exp' }, expText(j.experience))
    ];
    if (j.education) {
      b.push(el('span', { class: 'b' }, ico('graduation-cap', 'ico-sm'), CARD_EDU_LABEL[j.education] || EDU_LABEL[j.education] || j.education));
    }
    if (j.salary) {
      b.push(el('span', { class: 'b b-sal' }, ico('coins', 'ico-sm'), formatCardSalary(j.salary)));
    } else if (j.field && j.field !== 'other') {
      const lbl = j.subfield ? subLabel(j.subfield) : (CARD_FIELD_LABEL[j.field] || fieldLabel(j.field));
      b.push(el('span', { class: 'b' }, ico('briefcase', 'ico-sm'), lbl));
    } else if (j.shift) {
      b.push(el('span', { class: 'b' }, ico('clock', 'ico-sm'), formatCardSalary(j.shift)));
    }
    return b;
  }

  function card(j, i) {
    const band = expBand(j.experience);
    const m = fit(j);
    const style = `--edge:var(--exp-${band});--ebg:var(--exp-${band}-bg);--efg:var(--exp-${band});--i:${Math.min(i, 12)}`;
    const sec = j.sector || (j.official ? 'gov' : 'private');
    const secMeta = SECTOR_UI[sec] || SECTOR_UI.private;
    const ent = entityMeta(j);
    const dlDay = formatDate(j.deadline);
    const cityTxt = formatCardCities(j);
    const summaryText = cleanCardSummary(j, ent, cityTxt);
    const desc = el('p', {
      class: 'card-desc',
      role: 'button',
      tabindex: '0',
      title: 'اضغط لعرض مواصفات الشغل والشروط كاملة',
      onclick: () => openDetail(j),
      onkeydown: (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          openDetail(j);
        }
      }
    }, ...highlightNodes(summaryText, state.terms));

    const trk = getTrack(j.id);
    const isSav = Boolean(trk && trk.stage === 'saved');
    const isApp = Boolean(trk && trk.stage !== 'saved');
    const appDay = isApp ? formatShortDay(trk.appliedAt || trk.updatedAt) : '';
    const stageInfo = trk ? STAGE_MAP[trk.stage] : null;
    const trackSlot = el('div', { class: 'card-track-slot' }, ...buildCardTrackBadges(j));

    let topSaveBtn = null;
    let footerAppliedBtn = null;

    const syncCardTrackUI = () => {
      const nowTrk = getTrack(j.id);
      const nowSav = Boolean(nowTrk && nowTrk.stage === 'saved');
      const nowApp = Boolean(nowTrk && nowTrk.stage !== 'saved');
      const nowDay = nowApp ? formatShortDay(nowTrk.appliedAt || nowTrk.updatedAt) : '';
      if (topSaveBtn) {
        topSaveBtn.classList.toggle('is-saved', nowSav);
        topSaveBtn.title = nowSav ? 'محفوظة للتقديم (اضغط للإزالة)' : 'حفظ الوظيفة للتقديم لاحقاً';
        topSaveBtn.setAttribute('aria-label', topSaveBtn.title);
        topSaveBtn.replaceChildren(ico(nowSav ? 'bookmark-filled' : 'bookmark', 'ico-sm'));
      }
      if (footerAppliedBtn) {
        footerAppliedBtn.classList.toggle('is-applied', nowApp);
        footerAppliedBtn.replaceChildren(
          ico('check-circle', 'ico-sm'),
          el('span', null, nowApp ? (nowDay ? `قدّمت (${nowDay})` : 'قدّمت') : 'قدّمت')
        );
      }
      if (!state.saved) {
        trackSlot.replaceChildren(...buildCardTrackBadges(j));
      }
    };

    topSaveBtn = el('button', {
      type: 'button',
      class: 'card-icon-btn save-toggle' + (isSav ? ' is-saved' : ''),
      title: isSav ? 'محفوظة للتقديم (اضغط للإزالة)' : 'حفظ الوظيفة للتقديم لاحقاً',
      'aria-label': isSav ? 'محفوظة للتقديم' : 'حفظ الوظيفة',
      onclick: () => {
        toggleSave(j);
        syncCardTrackUI();
      }
    }, ico(isSav ? 'bookmark-filled' : 'bookmark', 'ico-sm'));

    const topShareBtn = el('button', {
      type: 'button',
      class: 'card-icon-btn share-btn',
      title: 'مشاركة ونسخ الإعلان',
      'aria-label': 'مشاركة ونسخ الإعلان',
      onclick: () => shareJob(j)
    }, ico('share', 'ico-sm'));

    footerAppliedBtn = el('button', {
      type: 'button',
      class: 'card-applied-btn applied-toggle' + (isApp ? ' is-applied' : ''),
      title: 'تعليم الوظيفة كـ «قدّمت عليها» لحفظ تاريخ التقديم',
      onclick: () => {
        toggleApplied(j);
        syncCardTrackUI();
      }
    }, ico('check-circle', 'ico-sm'), el('span', null, isApp ? (appDay ? `قدّمت (${appDay})` : 'قدّمت') : 'قدّمت'));

    const cleanEmp = cleanEmployer(j);
    const subOrgLabel = cleanEmp || (sec === 'gov' ? 'جهة حكومية رسمية' : sec === 'ngo' ? 'منظمة إنسانية' : 'صاحب عمل / شركة خاصة');
    const subMeta = el('div', { class: 'card-submeta' },
      el('span', { class: 'card-org', title: ent.name }, ico(ent.ico, 'ico-sm'), el('span', { class: 'trunc-txt' }, ...highlightNodes(subOrgLabel, state.terms))),
      el('span', { class: 'card-dot', 'aria-hidden': 'true' }, '·'),
      el('span', { class: 'card-loc', title: cityTxt }, ico('map-pin', 'ico-sm'), el('span', { class: 'trunc-txt' }, ...highlightNodes(cityTxt, state.terms))),
      dlDay ? el('span', { class: 'card-dl-pill', title: `آخر موعد للتقديم: ${dlDay}` }, ico('hourglass', 'ico-sm'), dlDay) : null
    );

    let trackerRow = null;
    if (state.saved && trk) {
      const sel = el('select', {
        'aria-label': 'تغيير حالة التقديم',
        onchange: (e) => {
          setTrack(j, e.target.value);
          toast(`تم تحديث الحالة إلى «${STAGE_MAP[e.target.value].label}»`);
          refresh();
        }
      }, ...TRACKER_STAGES.map((st) => el('option', { value: st.code, selected: trk.stage === st.code }, st.label)));
      const dayLbl = trk.stage !== 'saved' ? formatShortDay(trk.appliedAt || trk.updatedAt) : '';
      trackerRow = el('div', { class: 'tracker-card-bar' },
        el('span', { class: 'tracker-stage-pill ' + (stageInfo ? stageInfo.cls : 'st-saved') },
          ico(stageInfo ? stageInfo.ico : 'bookmark-filled', 'ico-sm'),
          (stageInfo ? stageInfo.label : 'محفوظة') + (dayLbl ? ` (${dayLbl})` : '')
        ),
        sel,
        trk.note ? el('p', { class: 'tracker-card-note' }, `ملاحظتي: ${trk.note}`) : null
      );
    }

    // Build clean single-row footer actions
    const applyActions = [];
    const hasDirectLink = Boolean(j.apply_url && !/^https?:\/\/(?:wa\.me\/|api\.whatsapp\.com\/)/i.test(j.apply_url) && !(j.deadline && daysLeft(j.deadline) < 0));
    const wa = waLink(j);
    const phone = (j.phones || [])[0];
    if (hasDirectLink) {
      const isForm = (j.apply_types || []).includes('form');
      const needsAcc = portalRequiresAccount(j);
      applyActions.push({
        cls: 'is-apply',
        icon: needsAcc ? 'lock' : (isForm ? 'file-text' : 'external-link'),
        label: needsAcc ? 'بوابة رسمية (بحساب)' : (isForm ? 'تعبئة استمارة التقديم' : 'التقديم عبر الموقع'),
        title: needsAcc
          ? 'رابط التقديم الرسمي على منصة الجهة المعلنة (يتطلب إنشاء حساب مجاني أو تسجيل دخول)'
          : (isForm ? 'تعبئة استمارة التقديم المباشر' : 'التقديم المباشر عبر الموقع'),
        href: j.apply_url,
        trk: needsAcc ? 'portal' : (isForm ? 'form' : 'site'),
        external: true
      });
    }
    if (wa) {
      applyActions.push({
        cls: 'is-wa',
        icon: 'whatsapp',
        label: 'راسل واتساب',
        title: 'التقديم عبر واتساب (مع رسالة تقديم جاهزة)',
        href: wa,
        trk: 'whatsapp',
        external: true
      });
    }
    if (j.email) {
      applyActions.push({
        cls: 'is-email',
        icon: 'mail',
        label: 'إرسال CV بالإيميل',
        title: `إرسال CV بالإيميل مع رسالة جاهزة (${j.email})`,
        href: mailtoHref(j),
        trk: 'email',
        external: false
      });
    }
    if (phone && !j.whatsapp) {
      applyActions.push({
        cls: 'is-call',
        icon: 'phone',
        label: 'اتصل ' + phone,
        title: `اتصال هاتفي مباشر (${phone})`,
        href: 'tel:' + phone,
        trk: 'call',
        external: false
      });
    }

    const footerEl = el('div', { class: 'card-footer' });
    if (applyActions.length) {
      const primary = applyActions[0];
      footerEl.append(
        el('a', {
          class: 'card-cta-main ' + primary.cls,
          href: primary.href,
          title: primary.title,
          'data-trk': primary.trk,
          'data-src': j.source,
          ...(primary.external ? { target: '_blank', rel: 'noopener' } : {})
        }, ico(primary.icon, 'ico-sm'), el('span', null, primary.label))
      );
      applyActions.slice(1, 2).forEach((secAct) => {
        footerEl.append(
          el('a', {
            class: 'card-cta-icon ' + secAct.cls,
            href: secAct.href,
            title: secAct.title,
            'data-trk': secAct.trk,
            'data-src': j.source,
            'aria-label': secAct.title,
            ...(secAct.external ? { target: '_blank', rel: 'noopener' } : {})
          }, ico(secAct.icon, 'ico-sm'))
        );
      });
      footerEl.append(
        el('button', {
          type: 'button',
          class: 'card-det-btn',
          title: 'عرض شرح العمل والمواصفات والشروط',
          onclick: () => openDetail(j)
        }, ico('file-text', 'ico-sm'), el('span', null, 'المواصفات'))
      );
    } else {
      footerEl.append(
        el('button', {
          type: 'button',
          class: 'card-cta-main is-apply',
          onclick: () => openDetail(j)
        }, ico('external-link', 'ico-sm'), el('span', null, 'عرض التفاصيل والتقديم'))
      );
    }
    footerEl.append(footerAppliedBtn);

    return el('article', { class: 'card in', style },
      el('div', { class: 'top' },
        el('div', { class: 'card-top-badges' },
          el('span', { class: 'sec-tag sec-' + sec }, ico(secMeta.ico, 'ico-sm'), secMeta.label),
          statusOf(j)
        ),
        el('div', { class: 'card-top-tools' },
          topSaveBtn,
          topShareBtn
        )
      ),
      trackSlot,
      el('h3', { class: 'card-title' }, el('button', { onclick: () => openDetail(j) }, ...highlightNodes(stripUnicodeJunk(j.title), state.terms))),
      subMeta,
      trackerRow,
      desc,
      m ? el('div', { class: 'fit ' + m.level }, el('i'), m.text) : null,
      el('div', { class: 'badges card-specs' }, cardBadges(j)),
      footerEl
    );
  }

  function openDetail(j) {
    const m = fit(j);
    const sec = j.sector || (j.official ? 'gov' : 'private');
    const secMeta = SECTOR_UI[sec] || SECTOR_UI.private;
    const ent = entityMeta(j);
    const defaultCountry = j.country === 'tr' ? 'تركيا (عام)' : 'سوريا (عام)';
    const citiesTxt = (j.cities || []).join('، ') || defaultCountry;
    const pubDay = formatDate(j.posted_at);
    const dlDay = formatDate(j.deadline);
    const expDay = formatDate(j.expires_on);
    const kv = [
      ['القطاع', secMeta.label],
      ['مجال العمل', j.field && j.field !== 'other' ? fieldLabel(j.field) : null],
      ['المؤهل العلمي', j.education ? (EDU_LABEL[j.education] || j.education) : 'غير مشترط / غير مذكور'],
      ['التخصص الدقيق', j.specialty],
      ['الخبرة المطلوبة', expText(j.experience)],
      ['العمر', j.age_min || j.age_max ? `${j.age_min || '؟'}–${j.age_max || '؟'} سنة` : null],
      ['نظام الدوام', j.shift],
      ['الأجر والراتب', j.salary]
    ].filter(([, v]) => v);

    const listBlock = (title, items) => (items && items.length) ? [el('h4', null, title), el('ul', null, items.map((x) => el('li', null, x)))] : [];
    const detSummary = cleanSummary(j.summary);
    const summaryBlock = detSummary ? [el('h4', null, ico('file-text', 'ico-sm'), 'نبذة ووصف الشاغر'), el('p', { style: 'margin:0;line-height:1.72' }, detSummary)] : [];
    const hasDirectLink = Boolean(j.apply_url && !/^https?:\/\/(?:wa\.me\/|api\.whatsapp\.com\/)/i.test(j.apply_url));
    const hasStructuredSpecs = Boolean((j.tasks && j.tasks.length) || (j.skills && j.skills.length));
    const detSpecUrl = j.details_url || (hasDirectLink ? j.apply_url : '');
    const externalSpecsBox = (!hasStructuredSpecs && detSpecUrl) ? el('div', { class: 'sheet-external-specs' },
      el('div', { class: 'sheet-external-specs-copy' },
        el('strong', null, ico('info', 'ico-sm'), 'بطاقة الوصف الوظيفي الكاملة على المصدر الرسمي'),
        el('p', null, 'المهام والشروط التفصيلية لهذا الشاغر منشورة ضمن صفحة الإعلان أو استمارة التقديم الرسمية.')
      ),
      el('a', {
        class: 'btn primary',
        href: detSpecUrl,
        target: '_blank',
        rel: 'noopener'
      }, ico('external-link', 'ico-sm'), 'عرض مواصفات العمل على الموقع الرسمي')
    ) : null;

    // Phase 2: Personal Application Tracker Control Box inside Drawer
    const curTrk = getTrack(j.id);
    const noteInput = el('input', {
      type: 'text',
      class: 'tracker-note-input',
      placeholder: 'أضف ملاحظة شخصية (مثال: موعد المقابلة أو اسم المسؤول)...',
      value: curTrk ? (curTrk.note || '') : '',
      maxlength: '240'
    });
    const stageBtns = TRACKER_STAGES.map((st) => {
      const btn = el('button', {
        type: 'button',
        class: 'tracker-stage-btn',
        'aria-pressed': String(Boolean(curTrk && curTrk.stage === st.code)),
        onclick: () => {
          setTrack(j, st.code, noteInput.value.trim());
          stageBtns.forEach((b, idx) => b.setAttribute('aria-pressed', String(TRACKER_STAGES[idx].code === st.code)));
          toast(`تم الحفظ في «تقديماتي» بحالة: ${st.label}`);
          if (state.saved) refresh();
        }
      }, ico(st.ico, 'ico-sm'), st.label);
      return btn;
    });
    const saveNoteBtn = el('button', {
      type: 'button',
      class: 'btn ghost',
      style: 'padding:7px 13px;font-size:.8rem',
      onclick: () => {
        const existing = getTrack(j.id);
        setTrack(j, existing ? existing.stage : 'saved', noteInput.value.trim());
        stageBtns.forEach((b, idx) => b.setAttribute('aria-pressed', String(TRACKER_STAGES[idx].code === (existing ? existing.stage : 'saved'))));
        toast('تم حفظ الملاحظة الشخصية على جهازك');
        if (state.saved) refresh();
      }
    }, ico('check', 'ico-sm'), 'حفظ');
    const trackerBox = el('div', { class: 'tracker-sheet-box' },
      el('div', { class: 'tracker-sheet-head' },
        el('span', { style: 'display:inline-flex;align-items:center;gap:6px' }, ico('clipboard-check', 'ico-sm'), 'متتبع تقديماتي الشخصي'),
        el('span', { class: 'tracker-privacy' }, ico('lock', 'ico-sm'), 'محفوظ على جهازك فقط')
      ),
      el('div', { class: 'tracker-stage-grid' }, ...stageBtns),
      el('div', { class: 'tracker-note-row' }, noteInput, saveNoteBtn)
    );

    // Phase 2: Quick Apply Message Templates (WhatsApp AR, Email AR, Email EN for NGOs)
    const tpls = buildApplyTemplates(j);
    const defaultTab = isEnglishJob(j) ? 'en' : (j.email && !(j.phones && j.phones.length) ? 'email' : 'wa');
    let activeTplTab = defaultTab;
    const tplSubjectInput = el('input', {
      type: 'text',
      class: 'tpl-subject-input',
      'aria-label': 'عنوان الإيميل (Subject)'
    });
    const tplSubjectRow = el('div', { class: 'tpl-subject-row', hidden: true },
      el('span', { class: 'tpl-subject-lbl' }, 'عنوان الإيميل (Subject):'),
      tplSubjectInput,
      el('button', {
        type: 'button',
        class: 'btn ghost tpl-copy-subj-btn',
        onclick: () => copyText(tplSubjectInput.value, 'تم نسخ عنوان الإيميل (Subject) ✅')
      }, ico('copy', 'ico-sm'), 'نسخ العنوان')
    );
    const tplTextarea = el('textarea', { class: 'tpl-textarea', 'aria-label': 'نص رسالة التقديم' });
    const tplDirectBtn = el('a', { class: 'btn ghost', target: '_blank', rel: 'noopener', hidden: true });
    const tplTabs = [
      { id: 'wa', label: 'واتساب (عربي)' },
      { id: 'email', label: 'إيميل (عربي)' },
      { id: 'en', label: 'Email (English)' }
    ];
    const tplTabBtns = [];
    const updateDirectTplHref = () => {
      if (activeTplTab === 'wa') {
        const p = (j.phones || []).find((x) => x.startsWith('09') || (j.country === 'tr' && x.startsWith('05')));
        if (p) {
          const intl = (j.country === 'tr' && p.startsWith('05')) ? '90' + p.slice(1) : '963' + p.slice(1);
          tplDirectBtn.hidden = false;
          tplDirectBtn.href = `https://wa.me/${intl}?text=${encodeURIComponent(tplTextarea.value)}`;
          tplDirectBtn.replaceChildren(ico('whatsapp', 'ico-sm'), 'إرسال الرسالة عبر واتساب');
        } else {
          tplDirectBtn.hidden = true;
        }
      } else if (j.email) {
        tplDirectBtn.hidden = false;
        tplDirectBtn.href = `mailto:${encodeURIComponent(j.email)}?subject=${encodeURIComponent(tplSubjectInput.value)}&body=${encodeURIComponent(tplTextarea.value)}`;
        tplDirectBtn.replaceChildren(ico('mail', 'ico-sm'), activeTplTab === 'en' ? 'Open Email with Pitch' : 'فتح الإيميل مع الرسالة جاهزة');
      } else {
        tplDirectBtn.hidden = true;
      }
    };
    const syncTplView = (tabId) => {
      activeTplTab = tabId;
      tplTabBtns.forEach((b, i) => b.setAttribute('aria-selected', String(tplTabs[i].id === tabId)));
      if (tabId === 'wa') {
        tplSubjectRow.hidden = true;
        tplTextarea.dir = 'rtl';
        tplTextarea.value = tpls.waAr;
      } else if (tabId === 'email') {
        tplSubjectRow.hidden = false;
        tplSubjectInput.dir = 'rtl';
        tplSubjectInput.value = tpls.emailSubjectAr;
        tplTextarea.dir = 'rtl';
        tplTextarea.value = tpls.emailBodyAr;
      } else {
        tplSubjectRow.hidden = false;
        tplSubjectInput.dir = 'ltr';
        tplSubjectInput.value = tpls.emailSubjectEn;
        tplTextarea.dir = 'ltr';
        tplTextarea.value = tpls.emailBodyEn;
      }
      updateDirectTplHref();
    };
    tplSubjectInput.addEventListener('input', updateDirectTplHref);
    tplTextarea.addEventListener('input', updateDirectTplHref);
    tplTabs.forEach((t) => {
      tplTabBtns.push(el('button', {
        type: 'button',
        class: 'tpl-tab',
        role: 'tab',
        'aria-selected': 'false',
        onclick: () => syncTplView(t.id)
      }, t.label));
    });
    syncTplView(defaultTab);

    const templatesSection = el('div', { class: 'tpl-box' },
      el('div', { class: 'tpl-head' },
        el('span', { class: 'tpl-title' }, ico('message-square', 'ico-sm'), 'مُولّد رسالة التقديم الجاهزة (Email & WhatsApp)'),
        el('div', { class: 'tpl-tabs', role: 'tablist' }, ...tplTabBtns)
      ),
      tplSubjectRow,
      tplTextarea,
      el('div', { class: 'tpl-actions' },
        el('button', {
          type: 'button',
          class: 'btn primary',
          style: 'padding:8px 15px;font-size:.83rem',
          onclick: () => copyText(tplTextarea.value, 'تم نسخ نص رسالة التقديم ✅')
        }, ico('copy', 'ico-sm'), 'نسخ نص الرسالة'),
        tplDirectBtn
      )
    );

    const shareSection = el('div', { class: 'share-box' },
      el('div', { class: 'share-box-title' }, ico('share', 'ico-sm'), 'مشاركة الإعلان أو نسخه مع الوصف وطريقة التقديم:'),
      el('div', { class: 'share-actions' },
        el('button', { type: 'button', class: 'btn primary', onclick: () => copyText(buildShareText(j), 'تم نسخ الإعلان كاملاً مع الوصف') }, ico('copy', 'ico-sm'), 'نسخ الإعلان مع الوصف'),
        j.email && !hasDirectLink
          ? el('button', { type: 'button', class: 'btn ghost', onclick: () => copyText(j.email, 'تم نسخ إيميل التقديم') }, ico('mail', 'ico-sm'), 'نسخ الإيميل للتقديم')
          : el('button', { type: 'button', class: 'btn ghost', onclick: () => copyText(primaryJobLink(j), 'تم نسخ الرابط') }, ico('link', 'ico-sm'), 'نسخ الرابط فقط'),
        el('button', { type: 'button', class: 'btn ghost', onclick: () => shareJob(j) }, ico('send', 'ico-sm'), 'معاينة أو إرسال واتساب / تيليجرام')
      )
    );

    const safetyStrip = el('div', { class: 'safety-strip' },
      el('span', { class: 'safety-strip-main' }, ico('shield-check', 'ico-sm'), 'التوظيف ما بيكلّف شي. إذا حدا طلب منك رسوم، لا تدفع وبلّغ عن الإعلان'),
      el('button', {
        type: 'button',
        onclick: () => {
          try { sessionStorage.setItem('laqeet.scam_prefill', buildShareText(j)); } catch (_) {}
          location.href = 'scam.html';
        }
      }, 'فحص النص في رادار النصب')
    );

    const offSrcs = visibleSources(j);
    const sourcesSection = offSrcs.length ? [
      el('h4', null, ico('shield-check', 'ico-sm'), 'جهات نشر وتوثيق الشاغر:'),
      el('ul', null, offSrcs.map((s) => el('li', null, el('a', { href: s.post_url, target: '_blank', rel: 'noopener' }, s.label))))
    ] : [];

    const metaGrid = el('div', { class: 'sheet-meta-grid' },
      el('div', { class: 'sheet-meta-card' },
        el('small', null, ico(ent.ico, 'ico-sm'), ent.role),
        el('b', null, ent.name)
      ),
      el('div', { class: 'sheet-meta-card' },
        el('small', null, ico('map-pin', 'ico-sm'), 'المدينة / المحافظة'),
        el('b', null, citiesTxt)
      ),
      pubDay ? el('div', { class: 'sheet-meta-card' },
        el('small', null, ico('calendar', 'ico-sm'), 'تاريخ النشر'),
        el('b', null, pubDay)
      ) : null,
      (dlDay || expDay) ? el('div', { class: 'sheet-meta-card' + (dlDay ? ' is-dl' : '') },
        el('small', null, ico('hourglass', 'ico-sm'), dlDay ? 'آخر موعد للتقديم' : 'تاريخ الانتهاء المتوقع'),
        el('b', null, dlDay || expDay)
      ) : null
    );

    const headEl = el('div', { class: 'sheet-head' },
      el('div', { class: 'sheet-head-top' },
        el('div', { style: 'display:flex;align-items:center;gap:8px;flex-wrap:wrap' },
          el('span', { class: 'sec-tag sec-' + sec }, ico(secMeta.ico, 'ico-sm'), secMeta.label),
          statusOf(j)
        ),
        el('button', { class: 'close', 'aria-label': 'إغلاق', onclick: () => $('sheet').close() }, ico('x'))
      ),
      el('h2', { id: 'sheetTitle' }, j.title),
      trustPill(j)
    );

    const hasInstantAlt = Boolean(j.email || j.whatsapp || (j.phones && j.phones.length));
    const portalNote = portalRequiresAccount(j) && j.apply_url && !(j.deadline && daysLeft(j.deadline) < 0)
      ? [el('div', { class: 'sheet-portal-note', role: 'note' },
          ico('lock', 'ico-sm'),
          el('div', null,
            el('strong', null, 'تتطلب البوابة الرسمية تسجيل حساب مجاني'),
            el('p', null, hasInstantAlt
              ? 'الرابط يفتح منصة التوظيف الرسمية للجهة المعلنة وقد يطلب تسجيل دخول، كما تتوفر وسيلة تواصل فورية بالأسفل.'
              : 'التقديم يتم حصراً عبر البوابة الرسمية للجهة المعلنة ويتطلب إنشاء حساب مجاني أو تسجيل دخول لإرسال الطلب.')
          )
        )]
      : [];

    const contentEl = el('div', { class: 'sheet-content' }, ...[
      metaGrid,
      m ? el('div', { class: 'fit ' + m.level }, el('i'), m.text) : null,
      trackerBox,
      kv.length ? el('dl', { class: 'kv' }, kv.map(([k, v]) => [el('dt', null, k), el('dd', null, v)])) : null,
      ...summaryBlock,
      ...listBlock('المهام والمسؤوليات الرئيسية', j.tasks),
      ...listBlock('الشروط والمهارات المطلوبة', j.skills),
      externalSpecsBox,
      templatesSection,
      shareSection,
      ...sourcesSection,
      ...portalNote,
      links(j, true),
      safetyStrip,
      el('div', { class: 'row', style: 'margin-top:4px' },
        el('a', { class: 'btn primary', href: 'cv.html?job=' + j.id }, ico('file-text', 'ico-sm'), 'إنشاء سيرة ذاتية لهذا الشاغر'),
        el('button', { class: 'btn ghost', onclick: () => speakJob(j) }, ico('volume', 'ico-sm'), 'قراءة صوتية'),
        j.deadline ? el('button', { class: 'btn ghost', onclick: () => downloadIcs(j) }, ico('calendar', 'ico-sm'), 'تذكير بالموعد') : null
      )
    ].flat(Infinity).filter(Boolean));

    $('sheetBody').replaceChildren(
      el('div', { class: 'sheet-handle', 'aria-hidden': 'true' }),
      headEl,
      contentEl
    );

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
  const nlCountryOf = (data) => ((data.understood || []).find((u) => u.kind === 'country') || {}).value;

  function buildParams(limitOverride, offsetOverride) {
    const p = new URLSearchParams();
    p.set('country', state.country || 'sy');
    if (state.sector) p.set('sector', state.sector);
    if (state.edu_group) p.set('edu_group', state.edu_group);
    if (state.edu) p.set('edu', state.edu);
    if (state.lower) p.set('lower', 'true');
    if (state.source) p.set('source', state.source);
    if (state.apply) p.set('apply', state.apply);
    if (state.exp) p.set('exp', state.exp);
    if (state.city) p.set('city', state.city);
    if (state.field) p.set('field', state.field);
    if (state.sub) p.set('sub', state.sub);
    if (state.q) { p.set('q', state.q); p.set('smart', '1'); } // «محاسب بدمشق دوام جزئي» → words + filters
    if (state.sort && state.sort !== 'relevance' && state.sort !== 'fit') p.set('sort', state.sort);
    if (state.fresh) p.set('new', 'true');
    if (state.instant) p.set('instant', 'true');
    p.set('limit', limitOverride != null ? limitOverride : PAGE);
    p.set('offset', offsetOverride != null ? offsetOverride : state.offset);
    return p;
  }

  function buildFilterKey() {
    return JSON.stringify([
      state.country, state.sector, state.edu_group, state.edu, state.lower,
      state.source, state.apply, state.exp, state.field, state.sub,
      state.city, state.q, state.fresh, state.instant, profile.edu, profile.exp, profile.age, profile.gender
    ]);
  }

  function updateMarketFieldsCollapse() {
    const sec = $('marketFieldsSection');
    const btn = $('toggleMarketFields');
    if (!sec || !btn) return;
    const isFiltered = Boolean(
      state.q || state.city || state.sector || state.edu || state.edu_group ||
      state.field || state.sub || state.apply || state.exp || state.fresh || state.instant || state.saved
    );
    const phone = window.matchMedia('(max-width: 720px)').matches;
    const open = manualFieldsOpen != null ? manualFieldsOpen : (!isFiltered && !phone);
    sec.classList.toggle('is-collapsed', !open);
    btn.setAttribute('aria-expanded', String(open));
    btn.replaceChildren(
      ico(open ? 'chevron-up' : 'chevron-down', 'ico-sm'),
      el('span', null, open ? 'إخفاء' : 'عرض المجالات')
    );
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

    const btnText = el('span', { class: 'msel-btn-text' });
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

    function setBtnLabel(txt) {
      if (cfg.ico) {
        btnText.replaceChildren(ico(cfg.ico, 'ico-sm'), el('span', null, txt));
      } else {
        btnText.textContent = cfg.icon ? `${cfg.icon} ${txt}` : txt;
      }
    }

    function sync() {
      const selected = splitCsv(cfg.getValue());
      const labels = selected.map((v) => labelMap[v] || v);
      if (!selected.length) {
        setBtnLabel(cfg.placeholder);
        badge.hidden = true;
        btn.classList.remove('has-val');
        statusText.textContent = 'الكل متاح';
      } else if (selected.length === 1) {
        setBtnLabel(labels[0]);
        badge.hidden = true;
        btn.classList.add('has-val');
        statusText.textContent = 'تم اختيار عنصر واحد';
      } else {
        const preview = labels.slice(0, 2).join('، ') + (labels.length > 2 ? ` (+${labels.length - 2})` : '');
        setBtnLabel(preview);
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
    if ($('sortOrder')) $('sortOrder').value = state.sort;
    if (state.country === 'tr' && state.sector === 'gov') state.sector = '';
    document.querySelectorAll('#sectorTabs .sector-card').forEach((c) => {
      c.hidden = c.dataset.sector === 'gov' && state.country === 'tr';
      c.setAttribute('aria-selected', String(c.dataset.sector === state.sector));
    });
    document.querySelectorAll('#eduGroupChips .chip').forEach((c) => c.setAttribute('aria-pressed', String(c.dataset.eduGroup === state.edu_group)));
    const selApplies = splitCsv(state.apply);
    document.querySelectorAll('#applyRow .chip').forEach((c) => {
      const code = c.dataset.apply;
      const pressed = code ? selApplies.includes(code) : selApplies.length === 0;
      c.setAttribute('aria-pressed', String(pressed));
    });
    const selExps = splitCsv(state.exp);
    document.querySelectorAll('#expRow .chip').forEach((c) => {
      const code = c.dataset.exp;
      const pressed = code ? selExps.includes(code) : selExps.length === 0;
      c.setAttribute('aria-pressed', String(pressed));
    });
    if ($('instantOnly')) $('instantOnly').setAttribute('aria-pressed', String(state.instant));
    if ($('newToday')) $('newToday').setAttribute('aria-pressed', String(state.fresh));
    if ($('savedOnly')) $('savedOnly').setAttribute('aria-pressed', String(state.saved && state.trackerFilter !== 'applied'));
    if ($('appliedOnly')) $('appliedOnly').setAttribute('aria-pressed', String(state.saved && state.trackerFilter === 'applied'));
    updateMarketFieldsCollapse();
    updateMoreFiltersLabel();
  }

  function updateMoreFiltersLabel() {
    const n = splitCsv(state.apply).length + splitCsv(state.exp).length + (state.instant ? 1 : 0) + (state.fresh ? 1 : 0) + (state.saved ? 1 : 0);
    if ($('moreFiltersLabel')) $('moreFiltersLabel').textContent = n ? `فلاتر إضافية (${n})` : 'فلاتر إضافية';
  }

  function renderActivePills() {
    const box = $('activeFilterPills');
    if (!box) return;
    const pills = [];
    const addPill = (iconName, label, clearFn) => {
      pills.push(el('span', { class: 'active-pill' },
        iconName ? ico(iconName, 'ico-sm') : null,
        el('span', null, label),
        el('button', { type: 'button', 'aria-label': 'إزالة ' + label, onclick: clearFn }, '×')
      ));
    };
    if (state.q) addPill('search', `بحث: «${state.q}»`, () => { state.q = ''; syncFilterUI(); refresh(); });
    if (state.sector && SECTOR_UI[state.sector]) addPill(SECTOR_UI[state.sector].ico, SECTOR_UI[state.sector].label, () => { state.sector = ''; syncFilterUI(); refresh(); });
    for (const c of splitCsv(state.city)) {
      addPill('map-pin', c, () => { state.city = removeCsvItem(state.city, c); syncFilterUI(); refresh(); });
    }
    for (const e of splitCsv(state.edu)) {
      addPill('graduation-cap', EDU_LABEL[e] || e, () => { state.edu = removeCsvItem(state.edu, e); syncFilterUI(); refresh(); });
    }
    if (state.edu_group) addPill(state.edu_group === 'degree' ? 'graduation-cap' : 'wrench', state.edu_group === 'degree' ? 'بشهادة' : 'بدون شهادة', () => { state.edu_group = ''; syncFilterUI(); refresh(); });
    for (const f of splitCsv(state.field)) {
      addPill('briefcase', fieldLabel(f), () => {
        state.field = removeCsvItem(state.field, f);
        if (!splitCsv(state.field).includes('computer')) state.sub = '';
        syncFilterUI();
        refresh();
      });
    }
    for (const s of splitCsv(state.sub)) {
      addPill('tag', subLabel(s), () => { state.sub = removeCsvItem(state.sub, s); refresh(); });
    }
    for (const a of splitCsv(state.apply)) {
      const aInfo = APPLY_UI[a];
      addPill(aInfo ? aInfo.ico : 'send', aInfo ? aInfo.label : a, () => { state.apply = removeCsvItem(state.apply, a); syncFilterUI(); refresh(); });
    }
    for (const exCode of splitCsv(state.exp)) {
      const exInfo = EXP_UI[exCode];
      addPill('clock', exInfo ? exInfo.label : exCode, () => { state.exp = removeCsvItem(state.exp, exCode); syncFilterUI(); refresh(); });
    }
    if (state.instant) addPill('zap', 'تقديم فوري بدون حساب', () => { state.instant = false; syncFilterUI(); refresh(); });
    if (state.fresh) addPill('sparkles', 'جديد اليوم', () => { state.fresh = false; syncFilterUI(); refresh(); });
    if (state.saved) addPill('clipboard-check', 'متتبع تقديماتي', () => { state.saved = false; state.trackerFilter = ''; syncFilterUI(); refresh(); });
    box.replaceChildren(...pills);
  }

  function renderTrackerBar(entries) {
    const bar = $('trackerBar');
    if (!bar) return;
    if (!state.saved) {
      bar.hidden = true;
      bar.replaceChildren();
      return;
    }
    const expiringCnt = entries.filter((e) => e.stage === 'saved' && isExpiringWithin48h(e.job)).length;
    const counts = { '': entries.length, saved: 0, applied: 0, interview: 0, offered: 0, expiring: expiringCnt };
    entries.forEach((e) => { if (counts[e.stage] != null) counts[e.stage] += 1; });
    const stageList = [
      { code: '', label: 'الكل', ico: 'layers' },
      ...TRACKER_STAGES,
      ...(expiringCnt > 0 ? [{ code: 'expiring', label: 'تنتهي خلال 48 ساعة', ico: 'alert', cls: 'st-expiring' }] : [])
    ];
    const tabs = stageList.map((st) =>
      el('button', {
        type: 'button',
        class: 'tracker-tab' + (st.code === 'expiring' ? ' is-expiring' : ''),
        'aria-pressed': String(state.trackerFilter === st.code),
        onclick: () => {
          state.trackerFilter = st.code;
          syncFilterUI();
          refresh();
        }
      }, ico(st.ico, 'ico-sm'), el('span', null, st.label), el('small', null, `(${counts[st.code] || 0})`))
    );
    bar.hidden = false;
    bar.replaceChildren(
      el('div', { class: 'tracker-tabs' }, ...tabs),
      el('span', { class: 'tracker-privacy' }, ico('lock', 'ico-sm'), 'متتبعك محفوظ محلياً على جهازك فقط')
    );
  }

  // ---------------------------------------------------------------- refresh jobs
  const renderedIds = new Set();
  let requestSeq = 0;
  async function refresh(append) {
    renderExpiringSavedBanner();
    updateMarketFieldsCollapse();
    if (state.saved) {
      state.terms = [];
      renderActivePills();
      if ($('typoBanner')) $('typoBanner').hidden = true;
      const entries = Object.values(trackerMap)
        .filter((e) => e && e.job)
        .sort((a, b) => String(b.updatedAt || '').localeCompare(String(a.updatedAt || '')));
      renderTrackerBar(entries);
      const filtered = state.trackerFilter === 'expiring'
        ? entries.filter((e) => e.stage === 'saved' && isExpiringWithin48h(e.job))
        : state.trackerFilter
          ? entries.filter((e) => e.stage === state.trackerFilter)
          : entries;
      $('grid').replaceChildren(
        ...(filtered.length ? filtered.map((e, i) => card(e.job, i)) : [
          el('div', { class: 'empty' },
            el('b', null, entries.length ? 'لا توجد شواغر ضمن هذه المرحلة حالياً' : 'متتبع تقديماتك فارغ حالياً'),
            el('span', null, entries.length
              ? 'يمكنك الضغط على «قدّمت عليها» أو «احفظ» على أي بطاقة لتحديث حالتك فوراً.'
              : 'اضغط على «احفظ» أو «قدّمت عليها» على أي بطاقة لتتابع كل تقديماتك هنا مجاناً وبدون حساب.')
          )
        ])
      );
      const filterTitle = state.trackerFilter === 'expiring'
        ? ' · تنتهي خلال 48 ساعة'
        : (state.trackerFilter && STAGE_MAP[state.trackerFilter] ? ` · ${STAGE_MAP[state.trackerFilter].label}` : '');
      $('count').textContent = `${filtered.length} شاغر في متتبع تقديماتي${filterTitle}`;
      $('more').hidden = true;
      return;
    }
    if ($('trackerBar')) $('trackerBar').hidden = true;

    if (!append) state.offset = 0;
    const currentSeq = ++requestSeq;
    const useFitSort = state.sort === 'fit' && hasProfile();
    const curFitKey = useFitSort ? buildFilterKey() : '';
    if (!useFitSort) fitCache = null;

    let data;
    if (useFitSort && append && fitCache && fitCache.key === curFitKey) {
      data = fitCache.data;
    } else {
      if (!append) renderSkeletons(6);
      try {
        const rawData = await getWithRetry(
          '/jobs?' + (useFitSort ? buildParams(200, 0) : buildParams()),
          3,
          1800
        );
        if (currentSeq !== requestSeq) return;
        if (useFitSort) {
          const sortedJobs = [...(rawData.jobs || [])].sort((a, b) => fitScore(b) - fitScore(a));
          data = { ...rawData, jobs: sortedJobs };
          fitCache = { key: curFitKey, data };
        } else {
          data = rawData;
        }
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
    }

    if (currentSeq !== requestSeq) return;
    state.total = data.total;
    state.terms = data.terms || [];
    renderActivePills();

    const banner = $('typoBanner');
    if (banner) {
      const understood = data.understood || [];
      if (!append && (understood.length || data.corrected)) {
        // What the search understood from the words, each removable (drops those words from the query).
        banner.hidden = false;
        const parts = [];
        if (data.corrected) {
          parts.push(el('span', null, 'ما لقينا شي بهالكتابة، فرجيناك نتائج '), el('b', null, `«${data.corrected}»`));
        }
        if (understood.length) {
          parts.push(el('span', null, data.corrected ? ' · فهمنا: ' : 'فهمنا من بحثك: '));
          for (const u of understood) {
            parts.push(el('span', { class: 'nl-chip' }, u.label,
              u.span ? el('button', {
                type: 'button',
                class: 'nl-chip-x',
                'aria-label': `شيل «${u.label}» من البحث`,
                onclick: () => {
                  state.q = state.q.replace(u.span, ' ').replace(/\s+/g, ' ').trim();
                  syncFilterUI();
                  refresh();
                }
              }, '×') : null));
          }
        }
        banner.replaceChildren(...parts);
      } else if (data.suggestion && !append) {
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

    if (!append) renderedIds.clear();
    const batch = useFitSort ? (data.jobs || []).slice(state.offset, state.offset + PAGE) : (data.jobs || []);
    const freshJobs = batch.filter((j) => {
      if (!j || renderedIds.has(j.id)) return false;
      renderedIds.add(j.id);
      return true;
    });
    const cards = freshJobs.map((j, i) => card(j, i));
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

    if ($('trGuide')) $('trGuide').hidden = (nlCountryOf(data) || state.country) !== 'tr';
    const shown = Math.min(renderedIds.size, data.total);
    const nlCities = (data.understood || []).filter((u) => u.kind === 'city').map((u) => u.label);
    const selCities = nlCities.length ? nlCities : splitCsv(state.city);
    const nlCountry = nlCountryOf(data);
    const countryDefaultLabel = (nlCountry || state.country) === 'tr' ? 'في كل تركيا' : 'في كل سوريا';
    const whereLabel = selCities.length ? `في ${selCities.join(' و ')}` : countryDefaultLabel;
    $('count').textContent = `${data.total} فرصة عمل مفتوحة ${whereLabel}` + (data.total > shown ? ` · معروض ${shown}` : '');
    if ($('boardOpenLabel')) {
      $('boardOpenLabel').textContent = state.country === 'tr' ? 'فرصة مفتوحة في تركيا' : 'إعلان مفتوح في سوريا';
    }
    const boardOpenNode = document.querySelector('[data-count="board_open"]');
    if (boardOpenNode && !state.city && !state.field && !state.edu && !state.edu_group && !state.q && !state.sector && !state.apply && !state.exp) {
      boardOpenNode.textContent = data.total;
    }
    const maxReachable = useFitSort ? (data.jobs || []).length : data.total;
    $('more').hidden = shown >= maxReachable || (state.offset + PAGE >= maxReachable) || !batch.length;

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
        btn.replaceChildren(
          ico(APPLY_UI[a].ico, 'ico-sm'),
          el('span', null, `${APPLY_UI[a].label} (${counts[a] || 0})`)
        );
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
    state.exp = '';
    state.field = '';
    state.sub = '';
    state.city = '';
    state.source = '';
    state.q = '';
    state.sort = 'relevance';
    state.terms = [];
    state.fresh = false;
    state.instant = false;
    state.saved = false;
    state.trackerFilter = '';
    state.lower = false;
    manualFieldsOpen = null;
    pendingFitSort = false;
    fitCache = null;
    if (location.search) {
      try { history.replaceState(null, '', location.pathname + location.hash); } catch (e) {}
    }

    syncFilterUI();
    refresh();
  }

  // ---------------------------------------------------------------- event listeners
  function setupEvents() {
    // one listener counts «قدّم» clicks and visits we send to each source channel
    document.addEventListener('click', (e) => {
      const a = e.target.closest('a[data-trk], a[data-trk-src]');
      if (!a) return;
      if (a.dataset.trk) track('apply', a.dataset.trk, a.dataset.src || '');
      else track('source', a.dataset.trkSrc);
    }, true);
    if ($('moreFiltersBtn')) {
      $('moreFiltersBtn').addEventListener('click', () => {
        const open = !$('moreFilters').classList.contains('open');
        $('moreFilters').classList.toggle('open', open);
        $('moreFiltersBtn').setAttribute('aria-expanded', String(open));
      });
    }
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

    // Experience level chips (multi-select supported)
    document.querySelectorAll('#expRow .chip').forEach((btn) => {
      btn.addEventListener('click', () => {
        const code = btn.dataset.exp;
        state.exp = code ? toggleCsvItem(state.exp, code) : '';
        syncFilterUI();
        refresh();
      });
    });

    if ($('toggleMarketFields')) {
      $('toggleMarketFields').addEventListener('click', () => {
        const sec = $('marketFieldsSection');
        if (!sec) return;
        manualFieldsOpen = sec.classList.contains('is-collapsed');
        updateMarketFieldsCollapse();
      });
    }

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
        const nextSort = e.target.value;
        if (nextSort === 'fit' && !hasProfile()) {
          pendingFitSort = true;
          e.target.value = state.sort;
          toast('حدّد مؤهلاتك أولاً لترتيب الشواغر حسب المطابقة');
          $('userEdu').value = profile.edu || '';
          $('userExp').value = profile.exp ?? '';
          $('userAge').value = profile.age || '';
          $('userGender').value = profile.gender || '';
          $('profileDrawer').showModal();
          return;
        }
        pendingFitSort = false;
        state.sort = nextSort;
        refresh();
      });
    }

    // Select dropdowns & Green Search Button
    const runSearchAction = (scrollToResults = false) => {
      if ($('q')) state.q = $('q').value.trim();
      if (state.q) track('search');
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
    if ($('instantOnly')) {
      $('instantOnly').addEventListener('click', (e) => {
        state.instant = !state.instant;
        e.currentTarget.setAttribute('aria-pressed', String(state.instant));
        updateMarketFieldsCollapse();
        refresh();
      });
    }
    $('newToday').addEventListener('click', (e) => {
      state.fresh = !state.fresh;
      e.currentTarget.setAttribute('aria-pressed', String(state.fresh));
      refresh();
    });
    $('savedOnly').addEventListener('click', () => {
      if (state.saved && state.trackerFilter === 'saved') {
        state.saved = false;
        state.trackerFilter = '';
      } else {
        state.saved = true;
        state.trackerFilter = 'saved';
      }
      syncFilterUI();
      refresh();
    });
    if ($('appliedOnly')) {
      $('appliedOnly').addEventListener('click', () => {
        if (state.saved && state.trackerFilter === 'applied') {
          state.saved = false;
          state.trackerFilter = '';
        } else {
          state.saved = true;
          state.trackerFilter = 'applied';
        }
        syncFilterUI();
        refresh();
      });
    }
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

    // Theme toggle is unified & pre-paint synced in ui.js via [data-theme-toggle]

    // Profile Drawer (Qualifications matching)
    const openProfileModal = () => {
      $('userEdu').value = profile.edu || '';
      $('userExp').value = profile.exp ?? '';
      $('userAge').value = profile.age || '';
      $('userGender').value = profile.gender || '';
      $('profileDrawer').showModal();
    };
    ['openProfileDrawer', 'navProfileBtn'].forEach((id) => {
      if ($(id)) $(id).addEventListener('click', openProfileModal);
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
      if (pendingFitSort && hasProfile()) {
        pendingFitSort = false;
        state.sort = 'fit';
        syncFilterUI();
      }
      toast('تم حفظ ملفك الشخصي بنجاح وتفعيل المطابقة');
      refresh();
    });
    $('clearProfileBtn').addEventListener('click', () => {
      profile = {};
      saveProfile();
      pendingFitSort = false;
      if (state.sort === 'fit') {
        state.sort = 'relevance';
        syncFilterUI();
      }
      $('userEdu').value = '';
      $('userExp').value = '';
      $('userAge').value = '';
      $('userGender').value = '';
      $('profileDrawer').close();
      toast('تم مسح البيانات الشخصية');
      refresh();
    });

    // Unified Navbar & Back-to-Landing Navigation
    ['navBrandHome', 'backToLandingBtn', 'navHomeLink', 'footerHomeLink'].forEach((id) => {
      if ($(id)) {
        $(id).addEventListener('click', (e) => {
          e.preventDefault();
          showLanding(true);
        });
      }
    });
    if ($('navJobsLink')) {
      $('navJobsLink').addEventListener('click', (e) => {
        e.preventDefault();
        if (state.view === 'board') {
          window.scrollTo({ top: 0, behavior: 'smooth' });
        } else {
          enterBoard({ country: window._wizCountry || state.country || 'sy' }, true);
        }
      });
    }

    // Board Country Switcher Tabs (وظائف سوريا | وظائف تركيا)
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
        const secOpts = [
          new Option(`كل القطاعات والمؤهلات (${cs.open || 0})`, ''),
          new Option(`منظمات دولية وإنسانية NGOs (${cs.ngo_count || 0})`, 'ngo'),
          new Option(`شركات وقطاع خاص (${cs.private_count || 0})`, 'private'),
        ];
        if (!isTr) {
          secOpts.push(new Option(`قطاع حكومي ومسابقات (${cs.gov_count || 0})`, 'gov'));
        }
        secOpts.push(new Option(`فرص سريعة وبدون شهادة (${cs.no_degree_count || 0})`, 'nodegree'));
        selSec.replaceChildren(...secOpts);
        selSec.value = (prevSec === 'gov' && isTr) ? '' : prevSec;
      }

      const bar = $('lpWizSectorsBar');
      if (bar) {
        const countryTag = isTr ? 'تركيا' : 'سوريا';
        const items = [
          { ico: 'building', lbl: 'قطاع خاص', cnt: cs.private_count || 0, sector: 'private' },
          { ico: 'handshake', lbl: 'منظمات دولية', cnt: cs.ngo_count || 0, sector: 'ngo' },
          ...(isTr ? [] : [{ ico: 'landmark', lbl: 'حكومي', cnt: cs.gov_count || 0, sector: 'gov' }]),
          { ico: 'wrench', lbl: 'بدون شهادة', cnt: cs.no_degree_count || 0, eduGroup: 'nodegree' }
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
            return el('button', attrs, ico(it.ico, 'ico-sm'), el('span', null, it.lbl), el('b', null, String(it.cnt)));
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
      if ($('lpNgoLabel')) $('lpNgoLabel').textContent = isTr ? 'شواغر منظمات في تركيا' : 'شواغر منظمات في سوريا';
      if ($('lpCounterPrivate')) {
        $('lpCounterPrivate').setAttribute('data-enter-country', c);
        const b = $('lpCounterPrivate').querySelector('[data-lp-country-stat="private_count"]');
        if (b) b.textContent = String(cs.private_count || 0);
      }
      if ($('lpPrivateLabel')) $('lpPrivateLabel').textContent = isTr ? 'شركات وقطاع خاص في تركيا' : 'شركات وقطاع خاص في سوريا';

      const gwCountrySuffix = isTr ? 'في تركيا' : 'في سوريا';
      const gwLinkText = {
        ngo_count: `تصفح شواغر المنظمات ${gwCountrySuffix}`,
        gov_count: `تصفح الوظائف الحكومية ${gwCountrySuffix}`,
        private_count: `تصفح وظائف الشركات ${gwCountrySuffix}`,
        no_degree_count: `تصفح الفرص السريعة ${gwCountrySuffix}`
      };
      document.querySelectorAll('[data-gw-card]').forEach((card) => {
        const key = card.dataset.gwCard;
        card.hidden = isTr && key === 'gov_count';
        card.setAttribute('data-enter-country', c);
        const cntEl = card.querySelector('.gw-count');
        if (cntEl) cntEl.textContent = String(cs[key] != null ? cs[key] : 0);
        const lnkEl = card.querySelector('.gw-link');
        if (lnkEl && gwLinkText[key]) {
          lnkEl.replaceChildren(el('span', null, gwLinkText[key]), ico('arrow-left', 'ico-sm'));
        }
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

  function startRotator(selector, intervalMs, opts = {}) {
    const spans = [...document.querySelectorAll(selector)];
    if (spans.length < 2) return;
    const { initialDelayMs = 0, exitMs = 1100, onStep } =
      typeof opts === 'function' ? { onStep: opts } : opts;
    let i = 0;
    if (typeof onStep === 'function') onStep(spans[0], 0, true);
    const tick = () => {
      const prev = spans[i];
      prev.classList.remove('on');
      if (!reduce) {
        prev.classList.add('out');
        setTimeout(() => prev.classList.remove('out'), exitMs);
      }
      i = (i + 1) % spans.length;
      spans[i].classList.add('on');
      if (typeof onStep === 'function') onStep(spans[i], i, false);
    };
    if (initialDelayMs > 0) {
      setTimeout(() => {
        tick();
        setInterval(tick, intervalMs);
      }, initialDelayMs);
    } else {
      setInterval(tick, intervalMs);
    }
  }

  function setupHeadlineRotation() {
    const countryBox = $('lpCountryRot');
    startRotator('#rot span', 3200, { exitMs: 500 });
    startRotator('#lpRoleRot span', 3400, { initialDelayMs: 1600, exitMs: 500 });
    startRotator('#lpCountryRot span', 3800, {
      initialDelayMs: 3200,
      exitMs: 500,
      onStep: (activeSpan) => {
        if (!countryBox) return;
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
      ico: 'map-pin',
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
      $('boardCountryTitle').textContent = isTr ? 'في تركيا' : 'في سوريا';
    }
    if ($('boardHeroSub')) {
      $('boardHeroSub').textContent = isTr
        ? 'فرص العمل في تركيا للسوريين والعرب والكفاءات: المنظمات الدولية، الشركات التركية والعربية، المصانع والورش، والعمل عن بُعد مع وسيلة التقديم المباشرة.'
        : 'فرص العمل في سوريا بمكان واحد. شواغر الوزارات، المنظمات الدولية والمحلية، والشركات والمصانع. فلترة حسب مؤهلك ومدينتك وطريقة التقديم، مع دمج المكرر وفلترة المشبوه.';
    }
    if ($('marketDemandTitle')) {
      $('marketDemandTitle').textContent = isTr
        ? 'أكثر المجالات طلباً حالياً في تركيا (اضغط للفلترة السريعة):'
        : 'أكثر المجالات طلباً حالياً في سوريا (اضغط للفلترة السريعة):';
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
      box.querySelectorAll('[data-counter-sector="gov"]').forEach((node) => {
        node.hidden = c === 'tr';
      });
    }
    if ($('boardOpenLabel')) {
      $('boardOpenLabel').textContent = c === 'tr' ? 'فرصة مفتوحة في تركيا' : 'إعلان مفتوح في سوريا';
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

  function syncNavViewState() {
    if ($('navJobsLink')) $('navJobsLink').classList.toggle('is-active', state.view === 'board');
  }

  function showLanding(pushHistory = true) {
    track('view', 'landing');
    state.view = 'landing';
    if ($('landingView')) $('landingView').hidden = false;
    if ($('boardView')) $('boardView').hidden = true;
    syncNavViewState();
    if (pushHistory) {
      try { history.pushState({ view: 'landing' }, '', `${location.pathname}?view=home`); } catch (e) {}
    }
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  function enterBoard(opts = {}, pushHistory = true) {
    if (state.view !== 'board') track('view', 'board');
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
    state.instant = Boolean(opts.instant);
    state.saved = false;
    state.offset = 0;

    if ($('landingView')) $('landingView').hidden = true;
    if ($('boardView')) $('boardView').hidden = false;
    syncNavViewState();

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
      if (state.exp) sp.set('exp', state.exp);
      if (state.instant) sp.set('instant', 'true');
      if (state.sort && state.sort !== 'relevance') sp.set('sort', state.sort);
      if (state.q) sp.set('q', state.q);
      try { history.pushState({ view: 'jobs', country: state.country }, '', `${location.pathname}?${sp.toString()}`); } catch (e) {}
    }

    window.scrollTo({ top: 0, behavior: 'smooth' });
    refresh();
  }

  // ---------------------------------------------------------------- first-visit onboarding
  // «شو شهادتك؟» → (for degree holders) «شو مجالك؟» → «وين بدك تشتغل؟», then straight to the matching jobs.
  // Choices are kept in the profile (on this device only) so the next visit opens the jobs directly.
  const DEGREE_LEVELS = ['institute', 'bachelor', 'master', 'phd'];
  const ob = { step: 'edu', edu: '', field: '', country: 'sy' };

  function obChip(label, onClick, extraCls) {
    return el('button', { type: 'button', class: 'ob-chip' + (extraCls ? ' ' + extraCls : ''), onclick: onClick }, label);
  }

  function obSteps() {
    return (!ob.edu || DEGREE_LEVELS.includes(ob.edu)) ? ['edu', 'field', 'city'] : ['edu', 'city'];
  }

  function obShow(step) {
    ob.step = step;
    const steps = obSteps();
    document.querySelectorAll('#onboard .ob-step').forEach((n) => { n.hidden = n.dataset.step !== step; });
    const idx = steps.indexOf(step);
    document.querySelectorAll('#obProgress i').forEach((dot, i) => {
      dot.hidden = i >= steps.length;
      dot.classList.toggle('on', i <= idx);
    });
    const copy = {
      edu: ['شو شهادتك؟', 'منوريك الشغل يلي بيناسبك. اختياراتك بتنحفظ على جهازك بس.'],
      field: ['شو مجالك؟', 'اختار مجالك، أو «كل المجالات» إذا بدك تشوف كل شي.'],
      city: ['وين بدك تشتغل؟', 'اختار المدينة، أو «كل المدن».']
    }[step];
    $('obQ').textContent = copy[0];
    $('obHint').textContent = copy[1];
    $('obBack').hidden = idx <= 0;
  }

  function obFinish(city) {
    profile.edu = ob.edu || profile.edu || '';
    profile.onboard = { edu: ob.edu, field: ob.field, city: city || '', country: ob.country, at: new Date().toISOString() };
    saveProfile();
    fitCache = null;
    track('onboard', ob.edu || '');
    enterBoard({ country: ob.country, edu: ob.edu, field: ob.field, city: city || '' }, true);
  }

  function obRenderCities() {
    const isTr = ob.country === 'tr';
    const cs = getCountryStats(ob.country);
    const counts = cs.city_counts || {};
    const list = isTr ? TURKISH_PROVINCES : ((META && META.cities && META.cities.length) ? META.cities : SYRIAN_GOVERNORATES);
    $('obCity').replaceChildren(
      obChip(isTr ? 'كل الولايات' : 'كل المدن', () => obFinish(''), 'ob-all'),
      ...list.map((c) => obChip(counts[c] ? `${c} · ${counts[c]}` : c, () => obFinish(c)))
    );
  }

  function setupOnboarding() {
    if (!$('onboard')) return;
    $('obEdu').replaceChildren(...LEVELS.map(([code, label]) => obChip(label, () => {
      ob.edu = code;
      ob.field = '';
      obShow(DEGREE_LEVELS.includes(code) ? 'field' : 'city');
    })));
    const fields = ((META && META.fields) || []).filter((f) => f.code !== 'other');
    $('obField').replaceChildren(
      obChip('كل المجالات', () => { ob.field = ''; obShow('city'); }, 'ob-all'),
      ...fields.map((f) => obChip(f.label, () => { ob.field = f.code; obShow('city'); }))
    );
    obRenderCities();
    document.querySelectorAll('#onboard [data-wiz-country]').forEach((tab) => {
      tab.addEventListener('click', () => { ob.country = tab.dataset.wizCountry === 'tr' ? 'tr' : 'sy'; obRenderCities(); });
    });
    $('obBack').addEventListener('click', () => {
      const steps = obSteps();
      const idx = steps.indexOf(ob.step);
      if (idx > 0) obShow(steps[idx - 1]);
    });
    $('obSkip').addEventListener('click', () => enterBoard({ country: ob.country }, true));
    obShow('edu');
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

    // Counters in Landing & Board Hero + Live Trust & Transparency Telemetry
    const statCounts = getStatCounts();
    document.querySelectorAll('[data-count]').forEach((node) => {
      const k = node.dataset.count;
      countUp(node, statCounts[k] != null ? statCounts[k] : 0);
    });
    const syncTrustMetrics = (animate) => {
      const tr = (STATS && STATS.trust) || null;
      if (!tr) return;
      document.querySelectorAll('[data-trust-stat]').forEach((node) => {
        const k = node.dataset.trustStat;
        if (tr[k] != null) {
          if (animate) countUp(node, tr[k]);
          else node.textContent = String(tr[k]);
        }
      });
    };
    syncTrustMetrics(true);

    populateLandingPortals();
    setupOnboarding();

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
      ico: 'graduation-cap',
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
      ico: 'briefcase',
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
    if (sp.get('view') === 'jobs' || sp.get('country') || sp.get('city') || sp.get('sector') || sp.get('edu_group') || sp.get('exp') || sp.get('q') || sp.get('instant') === 'true' || hashMatch) {
      enterBoard({
        country: sp.get('country') || 'sy',
        city: sp.get('city') || '',
        sector: sp.get('sector') || '',
        edu: sp.get('edu') || '',
        edu_group: sp.get('edu_group') || '',
        field: sp.get('field') || '',
        exp: sp.get('exp') || '',
        instant: sp.get('instant') === 'true',
        sort: sp.get('sort') || 'relevance',
        q: sp.get('q') || ''
      }, false);
      if (hashMatch) {
        get('/jobs/' + hashMatch[1]).then(openDetail).catch(() => {});
      }
    } else if (profile.onboard && sp.get('view') !== 'home') {
      // Returning visitor: open their jobs directly; the home button still shows the landing page.
      const o = profile.onboard;
      enterBoard({ country: o.country || 'sy', edu: o.edu || '', field: o.field || '', city: o.city || '' }, false);
    } else {
      showLanding(false);
    }
  }

  init();

  if ('serviceWorker' in navigator && location.protocol !== 'file:') {
    navigator.serviceWorker.register('sw.js', { updateViaCache: 'none' }).then((reg) => reg.update()).catch(() => {});
  }
})();
