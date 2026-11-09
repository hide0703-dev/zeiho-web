/* 税務六法Web — 条文ビューア（依存ライブラリなし） */
(() => {
  'use strict';

  const KIND_LABEL = { act: '法律', co: '施行令', mo: '規則' };
  const $ = (s, el = document) => el.querySelector(s);
  const main = $('#main');
  const tocEl = $('#toc');
  const msg = $('#jumpMsg');

  let INDEX = null;
  const tocCache = new Map();
  const chunkCache = new Map();

  // ── 設定（文字サイズ・色分け・薄字）──────────────────────
  const SIZES = [14, 15, 16, 17, 18, 20, 22, 24, 27];
  const settings = { color: true, thin: false, fs: 17 };
  try { Object.assign(settings, JSON.parse(localStorage.getItem('zw-settings') || '{}')); } catch (e) { /* 保存できない環境 */ }
  function applySettings() {
    document.body.classList.toggle('color', !!settings.color);
    document.body.classList.toggle('thin', !!settings.thin);
    document.documentElement.style.setProperty('--fs', settings.fs + 'px');
    $('#tColor').setAttribute('aria-pressed', String(!!settings.color));
    $('#tThin').setAttribute('aria-pressed', String(!!settings.thin));
    try { localStorage.setItem('zw-settings', JSON.stringify(settings)); } catch (e) { /* 無視 */ }
  }
  $('#tColor').onclick = () => { settings.color = !settings.color; applySettings(); };
  $('#tThin').onclick = () => { settings.thin = !settings.thin; applySettings(); };
  const step = d => {
    let i = SIZES.findIndex(s => s >= settings.fs);
    if (i < 0) i = SIZES.length - 1;
    settings.fs = SIZES[Math.max(0, Math.min(SIZES.length - 1, i + d))];
    applySettings();
  };
  $('#tSmall').onclick = () => step(-1);
  $('#tLarge').onclick = () => step(1);
  applySettings();

  // ── 目次の引き出し（狭い画面）───────────────────────────
  const tocBtn = $('#tocBtn');
  tocBtn.onclick = () => setDrawer(!tocEl.classList.contains('open'));
  function setDrawer(open) {
    tocEl.classList.toggle('open', open);
    tocBtn.setAttribute('aria-expanded', String(open));
    if (open) { const cur = tocEl.querySelector('[aria-current="true"]'); if (cur) cur.scrollIntoView({ block: 'center' }); }
  }
  document.addEventListener('keydown', e => { if (e.key === 'Escape') setDrawer(false); });
  tocEl.addEventListener('click', e => {
    if (e.target === tocEl && tocEl.classList.contains('open')) setDrawer(false);
    if (e.target.closest('a')) setDrawer(false);
  });

  // ── データ ─────────────────────────────────────────────
  async function getJSON(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error(`${url} を読めませんでした（${r.status}）`);
    return r.json();
  }
  const law = key => INDEX.laws.find(l => l.key === key);
  const tsu = key => (INDEX.tsutatsu || []).find(t => t.key === key);
  async function getToc(key) {
    if (!tocCache.has(key)) {
      const t = await getJSON(`data/${key}/toc.json`);
      t.order = [];
      t.parent = new Map();
      t.leaf = new Map();
      (function walk(nodes, path, parentNode) {
        for (const n of nodes) {
          if (n.k === 'A') { t.order.push(n.id); t.parent.set(n.id, { path, node: parentNode }); t.leaf.set(n.id, n); }
          else walk(n.c || [], path.concat(n.t), n);
        }
      })(t.toc, [], { c: t.toc });
      tocCache.set(key, t);
    }
    return tocCache.get(key);
  }
  async function getChunk(key, prefix, n) {
    const id = `${key}/${prefix}${n}`;
    if (!chunkCache.has(id)) chunkCache.set(id, getJSON(`data/${id}.json`));
    return chunkCache.get(id);
  }
  async function getArticles(key, toc, ids) {
    const files = [...new Set(ids.map(i => toc.where[i]))];
    const lists = await Promise.all(files.map(f => getChunk(key, 'm', f)));
    const by = new Map();
    lists.flat().forEach(a => by.set(a.id, a));
    return ids.map(i => by.get(i)).filter(Boolean);
  }

  // ── 文字の装飾：括弧の深さで色分け、ただし書、定義語 ──────
  const esc = s => s.replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const FIG = /［図｜([^｜］]*)｜([^］]*)］/g;
  function decoFig(text) {
    if (!text || text.indexOf('［図｜') < 0) return deco(text);
    let out = '', last = 0, m;
    FIG.lastIndex = 0;
    while ((m = FIG.exec(text))) {
      out += deco(text.slice(last, m.index));
      const src = m[1];
      out += /^https:\/\/www\.nta\.go\.jp\//.test(src)
        ? `<img class="fig" src="${esc(src)}" alt="${esc(m[2])}" loading="lazy">`
        : esc(m[2]);
      last = FIG.lastIndex;
    }
    return out + deco(text.slice(last));
  }
  function deco(text) {
    if (!text) return '';
    let out = '', depth = 0;
    for (const ch of text) {
      if (ch === '（' || ch === '(') { depth++; out += `<span class="k${((depth - 1) % 3) + 1}">` + esc(ch); }
      else if ((ch === '）' || ch === ')') && depth > 0) { out += esc(ch) + '</span>'; depth--; }
      else out += esc(ch);
    }
    out += '</span>'.repeat(depth);
    out = out.replace(/ただし、/g, '<span class="tadashi">ただし、</span>');
    out = out.replace(/「([^「」<]{1,40})」(?=(?:<[^>]+>)*という)/g, '「<span class="def">$1</span>」');
    return out;
  }

  // ── 条文のHTML ─────────────────────────────────────────
  function extrasHTML(xs) {
    if (!xs) return '';
    return xs.map(x => {
      if (x.table) return tableHTML(x.table);
      if (x.note) return `<p class="note">${deco(x.note)}</p>`;
      if (x.text) return `<p>${deco(x.text)}</p>`;
      if (x.list) return `<ul class="items">${x.list.map(s => `<li>${deco(s)}</li>`).join('')}</ul>`;
      return '';
    }).join('');
  }
  function tableHTML(t) {
    const cell = (c, tag) => `<${tag}${c.r ? ` rowspan="${c.r}"` : ''}${c.c ? ` colspan="${c.c}"` : ''}>${deco(c.t)}</${tag}>`;
    const head = (t.head || []).map(r => `<tr>${r.map(c => cell(c, 'th')).join('')}</tr>`).join('');
    const body = t.rows.map(r => `<tr>${r.map(c => cell(c, 'td')).join('')}</tr>`).join('');
    return `${t.title ? `<p class="note">${esc(t.title)}</p>` : ''}<div class="tbl-wrap"><table>${head ? `<thead>${head}</thead>` : ''}<tbody>${body}</tbody></table></div>` +
      (t.remarks || []).map(r => `<p class="note">${deco(r)}</p>`).join('');
  }
  function itemsHTML(items) {
    if (!items || !items.length) return '';
    return `<ul class="items">${items.map(i =>
      `<li><span class="ino">${esc(i.t)}</span>${deco(i.s)}${itemsHTML(i.i)}${extrasHTML(i.x)}</li>`).join('')}</ul>`;
  }
  function articleHTML(key, a, target) {
    const head = a.t ? `<a class="ano" href="#/${key}/${encodeURIComponent(a.id)}">${esc(a.t)}</a>` : '';
    const paras = a.p.map((p, i) => {
      const no = i === 0 ? head : (p.t ? `<span class="pno">${esc(p.t)}</span>` : '');
      const cap = p.c ? `<p class="pcap">${esc(p.c)}</p>` : '';
      return `${cap}<div class="para" id="p-${a.id}-${p.n || i + 1}"><p style="margin:0">${no}${deco(p.s)}</p>${itemsHTML(p.i)}${extrasHTML(p.x)}</div>`;
    }).join('');
    return `<section class="jo${a.del ? ' deleted' : ''}${target ? ' target' : ''}" id="a-${a.id}">` +
      (a.c ? `<p class="cap">${esc(a.c)}</p>` : '') + paras + `</section>`;
  }

  // ── 目次 ───────────────────────────────────────────────
  function renderToc(key, toc, currentId) {
    const L = law(key);
    const sibs = INDEX.laws.filter(l => l.group === L.group);
    const open = new Set();
    if (currentId && toc.parent.has(currentId)) toc.parent.get(currentId).path.forEach(p => open.add(p));
    const nodeHTML = nodes => `<ul>${nodes.map(n => {
      if (n.k === 'A') {
        const cur = n.id === currentId ? ' aria-current="true"' : '';
        return `<li><a class="art${n.del ? ' del' : ''}" href="#/${key}/${encodeURIComponent(n.id)}"${cur}>${esc(n.t)}${n.c ? ` <span class="cap">${esc(n.c)}</span>` : ''}</a></li>`;
      }
      return `<li><details${open.has(n.t) ? ' open' : ''}><summary>${esc(n.t)}</summary>${nodeHTML(n.c || [])}</details></li>`;
    }).join('')}</ul>`;
    tocEl.innerHTML =
      `<p class="lawhead">${esc(L.title)}</p>` +
      `<p class="lawmeta">${esc(L.law_num || '')}<br>施行日 ${esc(L.enforced || '－')} 時点</p>` +
      `<p class="kinds">${sibs.map(s => `<a href="#/${s.key}"${s.key === key ? ' aria-current="page"' : ''}>${KIND_LABEL[s.kind]}</a>`).join('')}</p>` +
      nodeHTML(toc.toc) +
      `<div class="extra"><ul>` +
      (toc.suppl.length ? `<li><a class="art" href="#/${key}/s">附則（${toc.suppl.length}本）</a></li>` : '') +
      toc.appdx.map(x => `<li><a class="art" href="#/${key}/x/${x.id}">${esc(x.t)}</a></li>`).join('') +
      `</ul></div>`;
    tocEl.hidden = false;
    tocBtn.hidden = false;
    const cur = tocEl.querySelector('[aria-current="true"]');
    if (cur && window.innerWidth > 900) cur.scrollIntoView({ block: 'center' });
  }
  function hideToc() { tocEl.hidden = true; tocBtn.hidden = true; setDrawer(false); }

  // ── 画面 ───────────────────────────────────────────────
  function homeView() {
    hideToc();
    document.title = '税務六法Web';
    const groups = INDEX.groups.map(g => {
      const books = INDEX.laws.filter(l => l.group === g.id).map(l =>
        `<a class="book" href="#/${l.key}"><b>${KIND_LABEL[l.kind]}</b><small>${esc(l.abbr)}</small></a>`).join('');
      return `<li><span class="gname">${esc(g.name)}</span><span class="books">${books}${(INDEX.tsutatsu || []).filter(t => t.group === g.id).map(t =>
        `<a class="book tsu" href="#/${t.key}"><b>通達</b><small>${esc(t.abbr)}</small></a>`).join('')}</span></li>`;
    }).join('');
    main.innerHTML = `<div class="home">
      <h1>税務六法</h1>
      <p class="lead">国税・地方税の法律・施行令・施行規則を e-Gov法令検索の原文から、法令解釈通達を国税庁ホームページの原文から表示します。</p>
      <ul class="shelf">${groups}</ul>
      <div class="howto">
        <h2>条文へ一発で移動する</h2>
        <ul><li>上の入力欄に「法法22」「所令7の2」「措法41の5③」「法基通9-2-9」「消基通13-2-1」「措通31の3-11」「徴基通47-1」のように入れて「直行」。</li>
        <li>略称は 所法・所令・所規、法法・法令・法規、相法、消法、措法、通法、徴法、印法、地法 など。正式名称（法人税法施行令 22）でも移動できます。</li></ul>
        <h2>読みやすく表示する</h2>
        <ul><li>「色分け」で括弧の深さごとに色を変えます。「薄字」で括弧書きを薄くして、条文の骨格だけを読めます。</li>
        <li>「小」「大」で文字の大きさを変えられます。設定はこの端末に保存されます。</li></ul>
        <h2>アプリのように使う</h2>
        <ul><li>iPhone・iPad：Safari の共有ボタン →「ホーム画面に追加」。</li><li>Android：Chrome のメニュー（︙）→「ホーム画面に追加」。</li></ul>
      </div></div>`;
  }

  function hashira(L, path) {
    return `<div class="hashira"><b>${esc(L.title)}</b>${path.map(p => `<span class="sep">›</span><span>${esc(p)}</span>`).join('')}</div>`;
  }
  function srcLine(L, toc) {
    const link = toc.law_id ? `　<a href="https://laws.e-gov.go.jp/law/${encodeURIComponent(toc.law_id)}" target="_blank" rel="noopener">e-Gov法令検索で原文を開く</a>` : '';
    return `<p class="src">${esc(L.title)}（${esc(L.law_num || '')}）施行日 ${esc(L.enforced || '－')} 時点。出典：e-Gov法令検索。${link}</p>`;
  }

  async function lawView(key, id, para) {
    const L = law(key);
    if (!L) return notFound(`「${key}」という法令は収録していません。`);
    const toc = await getToc(key);
    if (!id) id = toc.order.find(i => !toc.leaf.get(i).del) || toc.order[0];
    if (!toc.leaf.has(id)) return notFound(`${L.title}に「${idLabel(id)}」はありません。`, key, toc);

    // 同じ款・節などに属する条をまとめて表示（印刷の六法のように前後を続けて読める）
    const { path, node } = toc.parent.get(id);
    let ids = (node.c || []).filter(n => n.k === 'A').map(n => n.id);
    if (ids.length > 40 || ids.length === 0) {
      const i = toc.order.indexOf(id);
      ids = toc.order.slice(Math.max(0, i - 5), i + 15);
    }
    const arts = await getArticles(key, toc, ids);
    renderToc(key, toc, id);
    document.title = `${L.abbr}${idLabel(id)}｜税務六法Web`;

    const i0 = toc.order.indexOf(ids[0]);
    const i1 = toc.order.indexOf(ids[ids.length - 1]);
    const prev = toc.order[i0 - 1];
    const next = toc.order[i1 + 1];
    main.innerHTML = `<div class="page">${hashira(L, path)}
      <div class="law-text">${arts.map(a => articleHTML(key, a, a.id === id)).join('')}</div>
      <nav class="nav-arts" aria-label="前後">
        ${prev ? `<a href="#/${key}/${encodeURIComponent(prev)}">前へ（${esc(toc.leaf.get(prev).t)}）</a>` : '<span></span>'}
        ${next ? `<a href="#/${key}/${encodeURIComponent(next)}">次へ（${esc(toc.leaf.get(next).t)}）</a>` : '<span></span>'}
      </nav>${srcLine(L, toc)}</div>`;

    const target = para ? document.getElementById(`p-${id}-${para}`) : null;
    if (target) target.classList.add('target');
    const el = target || document.getElementById(`a-${id}`);
    if (el) el.scrollIntoView({ block: 'start' });
  }

  async function supplView(key, sid) {
    const L = law(key);
    const toc = await getToc(key);
    renderToc(key, toc, null);
    if (!sid) {
      document.title = `${L.abbr}附則｜税務六法Web`;
      main.innerHTML = `<div class="page">${hashira(L, ['附則'])}
        <p class="src">制定時の附則と、改正法ごとの附則（新しいものほど下）です。</p>
        <ul class="suppl-list">${toc.suppl.map(s =>
          `<li><a href="#/${key}/s/${s.id}">${esc(s.t)}<small>${s.law ? esc(s.law) : '制定時'}</small></a></li>`).join('')}</ul>
        ${srcLine(L, toc)}</div>`;
      return;
    }
    const meta = toc.suppl.find(s => s.id === sid);
    if (!meta) return notFound('その附則は見つかりません。', key, toc);
    const s = (await getChunk(key, 's', meta.f)).find(x => x.id === sid);
    document.title = `${L.abbr}附則｜税務六法Web`;
    const body = s.body.map(a => a.kind ? `<section class="jo"><p class="cap">${esc(a.t)}</p>${extrasHTML(a.x)}</section>` : articleHTML(key + '/s/' + sid, a, false)).join('');
    main.innerHTML = `<div class="page">${hashira(L, ['附則', s.law || '制定時'])}
      <div class="law-text">${body}</div>
      <nav class="nav-arts"><a href="#/${key}/s">附則の一覧へ</a><span></span></nav>${srcLine(L, toc)}</div>`;
    main.querySelectorAll('.ano').forEach(a => a.removeAttribute('href'));
  }

  async function appdxView(key, xid) {
    const L = law(key);
    const toc = await getToc(key);
    renderToc(key, toc, null);
    const meta = toc.appdx.find(x => x.id === xid);
    if (!meta) return notFound('その別表は見つかりません。', key, toc);
    const x = (await getChunk(key, 'x', meta.f)).find(a => a.id === xid);
    document.title = `${L.abbr}${x.t}｜税務六法Web`;
    main.innerHTML = `<div class="page">${hashira(L, [x.t])}
      <div class="law-text"><section class="jo"><p class="cap">${esc(x.t)}${x.rel ? `　${esc(x.rel)}` : ''}</p>${extrasHTML(x.x)}</section></div>
      ${srcLine(L, toc)}</div>`;
  }

  function notFound(text, key, toc) {
    if (key && toc) renderToc(key, toc, null); else hideToc();
    main.innerHTML = `<div class="page"><p class="loading">${esc(text)}<br><a href="${key ? '#/' + key : '#/'}">${key ? '目次から探す' : 'トップへ戻る'}</a></p></div>`;
  }

  // ── 通達 ───────────────────────────────────────────────
  const tsuTocCache = new Map();
  async function getTsuToc(key) {
    if (!tsuTocCache.has(key)) tsuTocCache.set(key, getJSON(`data/${key}/toc.json`));
    return tsuTocCache.get(key);
  }
  function renderTsuToc(key, toc, curPage) {
    const T = tsu(key);
    const pages = new Map(toc.pages.map(p => [p.n, p]));
    const sibs = INDEX.laws.filter(l => l.group === T.group).map(l => `<a href="#/${l.key}">${KIND_LABEL[l.kind]}</a>`).join('') +
      INDEX.tsutatsu.filter(t => t.group === T.group).map(t => `<a href="#/${t.key}"${t.key === key ? ' aria-current="page"' : ''}>${esc(t.abbr)}</a>`).join('');
    const rows = toc.toc.map(e => {
      const pad = `style="padding-left:${Math.max(0, (e.d || 0) - 1) * 12 + 4}px"`;
      if (e.g) return `<li class="tg" ${pad}>${esc(e.g)}</li>`;
      const p = pages.get(e.n);
      const cur = e.n === curPage ? ' aria-current="true"' : '';
      return `<li ${pad}><a class="art${p && p.old ? ' del' : ''}" href="#/${key}/p${e.n}"${cur}>${esc(e.t)}${p && p.old ? ' <span class="cap">（旧）</span>' : ''}</a></li>`;
    }).join('');
    tocEl.innerHTML = `<p class="lawhead">${esc(T.title)}</p>
      <p class="lawmeta">国税庁ホームページから取得（${esc((toc.fetched_at || '').slice(0, 10))}）</p>
      <p class="kinds">${sibs}</p><ul class="tsu-toc">${rows}</ul>`;
    tocEl.hidden = false;
    tocBtn.hidden = false;
    const cur = tocEl.querySelector('[aria-current="true"]');
    if (cur && window.innerWidth > 900) cur.scrollIntoView({ block: 'center' });
  }
  function blockHTML(b) {
    if (b.h) return b.h === 1 ? '' : `<p class="tsu-h h${b.h}">${decoFig(b.t)}</p>`;
    if (b.table) {
      const rows = b.table.map(r => `<tr>${r.map(c => {
        const tag = c.h ? 'th' : 'td';
        return `<${tag}${c.r ? ` rowspan="${c.r}"` : ''}${c.c ? ` colspan="${c.c}"` : ''}>${decoFig(c.t)}</${tag}>`;
      }).join('')}</tr>`).join('');
      return `${b.cap ? `<p class="note">${esc(b.cap)}</p>` : ''}<div class="tbl-wrap"><table><tbody>${rows}</tbody></table></div>`;
    }
    const lv = Math.min(b.lv || 0, 6);
    let body = decoFig(b.p).replace(/\n/g, '<br>');
    if (b.id) {
      const m = b.p.match(/^\S+/);
      if (m) body = `<a class="ano" href="#/${CUR_TSU}/${encodeURIComponent(b.id)}">${esc(m[0])}</a>` + decoFig(b.p.slice(m[0].length)).replace(/\n/g, '<br>');
    }
    return `<p class="tp lv${lv}${b.ctr ? ' ctr' : ''}${b.id ? ' item' : ''}"${b.id ? ` id="i-${esc(b.id)}"` : ''}>${b.li ? `<span class="ino">(${b.li})</span>` : ''}${body}</p>`;
  }
  let CUR_TSU = '';
  async function tsuView(key, arg) {
    const T = tsu(key);
    const toc = await getTsuToc(key);
    let n, itemId = null;
    if (!arg) n = (toc.pages.find(p => p.items.length) || toc.pages[0]).n;
    else if (/^p\d+$/.test(arg)) n = +arg.slice(1);
    else if (toc.where[arg] !== undefined) { n = toc.where[arg]; itemId = arg; }
    else return notFound(`${T.abbr}に「${arg}」は見つかりません。`, null, null);
    const page = await getJSON(`data/${key}/t${n}.json`);
    CUR_TSU = key;
    renderTsuToc(key, toc, n);
    document.title = `${T.abbr}${itemId ? itemId : ''}｜税務六法Web`;
    const idx = toc.pages.findIndex(p => p.n === n);
    const prev = toc.pages[idx - 1], next = toc.pages[idx + 1];
    main.innerHTML = `<div class="page">${hashira({ title: T.title }, [page.title || ''].filter(Boolean))}
      ${page.old ? `<p class="oldver">この節は改正前（${esc(page.old.slice(0, 4))}年${+page.old.slice(4, 6)}月${+page.old.slice(6, 8)}日以前など）の取扱いです。現行の取扱いは目次の該当箇所をご覧ください。</p>` : ''}
      <div class="law-text tsu">${page.blocks.map(blockHTML).join('')}</div>
      <nav class="nav-arts" aria-label="前後">
        ${prev ? `<a href="#/${key}/p${prev.n}">前へ（${esc(prev.t)}）</a>` : '<span></span>'}
        ${next ? `<a href="#/${key}/p${next.n}">次へ（${esc(next.t)}）</a>` : '<span></span>'}
      </nav>
      <p class="src">${esc(T.title)}。出典：国税庁ホームページ 法令解釈通達（${esc((toc.fetched_at || '').slice(0, 10))} 取得）。<a href="${esc(page.url)}" target="_blank" rel="noopener">国税庁ホームページで原文を開く</a></p></div>`;
    const el = itemId ? document.getElementById('i-' + itemId) : null;
    if (el) {
      el.classList.add('target');
      const cap = el.previousElementSibling && el.previousElementSibling.classList.contains('tsu-h') ? el.previousElementSibling : el;
      cap.scrollIntoView({ block: 'start' });
    } else window.scrollTo(0, 0);
  }

  // ── 直行：「法法22の2③」などを解釈する ───────────────────
  const KANJI = { '〇': 0, '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9 };
  function kanjiToNum(s) {
    let total = 0, cur = 0;
    for (const ch of s) {
      if (ch in KANJI) cur = cur * 10 + KANJI[ch];
      else {
        const unit = { '十': 10, '百': 100, '千': 1000 }[ch];
        total += (cur || 1) * unit; cur = 0;
      }
    }
    return total + cur;
  }
  function idLabel(id) {
    if (/^p\d+/.test(id)) return '';
    const [n, ...rest] = id.split('_');
    return n + '条' + rest.map(r => 'の' + r).join('');
  }
  function parseJump(raw) {
    let s = raw.trim().replace(/[\s　]/g, '');
    s = s.replace(/[①-⑳]/g, c => '項' + (c.charCodeAt(0) - 0x2460 + 1));
    s = s.normalize('NFKC');
    s = s.replace(/[〇一二三四五六七八九十百千]+/g, m => String(kanjiToNum(m)));
    const names = [];
    INDEX.laws.forEach(l => { names.push([l.abbr, l.key]); names.push([l.title, l.key]); });
    (INDEX.tsutatsu || []).forEach(t => { names.push([t.abbr, t.key]); names.push([t.title, t.key]); });
    names.push(['所得税基本通達', 'shotoku_kt'], ['法人税基本通達', 'houjin_kt'], ['消費税法基本通達', 'shohi_kt'], ['消基通', 'shohi_kt']);
    names.sort((a, b) => b[0].length - a[0].length);
    const hit = names.find(([n]) => s.startsWith(n));
    if (!hit) {
      return { error: `「${raw}」の法令名が分かりません。例：法法22、所令7の2、措法41の5③` };
    }
    const rest0 = s.slice(hit[0].length);
    if (tsu(hit[1])) {
      const item = rest0.replace(/[‐‑‒–—―−ーｰ－]/g, '-').replace(/[〜～]/g, '~').replace(/^第(\d+(?:の\d+)?)条$/, '$1').replace(/関係/, '').replace(/^-/, '');
      return { key: hit[1], tsu: true, item };
    }
    const rest = rest0.replace(/^第/, '');
    if (!rest) return { key: hit[1] };
    const m = rest.match(/^(\d+)条?((?:の\d+)*)条?(?:(?:項|-|第)(\d+)項?|(\d+)項)?(?:(\d+)号)?$/);
    if (!m) return { error: `「${rest}」の条番号が読めません。例：22、22の2、22③、22-3` };
    const id = [m[1], ...m[2].split('の').filter(Boolean)].join('_');
    return { key: hit[1], id, para: m[3] || m[4] || null };
  }
  // 措通は4本に分かれているので、番号でまとめて探す
  const TSU_FAMILY = { sochi_kt: ['sochi_kt', 'sochi_jt', 'sochi_kabu', 'sochi_gensen'] };
  async function findItem(key, item) {
    const keys = TSU_FAMILY[key] || [key];
    for (const k of keys) {
      const t = await getTsuToc(k);
      if (t.where[item] !== undefined) return { key: k, id: item };
    }
    for (const k of keys) {  // 「9-2」のように途中までなら、その最初の項目へ
      const t = await getTsuToc(k);
      const hit = Object.keys(t.where).find(i => i.startsWith(item + '-'));
      if (hit) return { key: k, id: hit };
    }
    return null;
  }
  $('#jump').addEventListener('submit', async e => {
    e.preventDefault();
    const v = $('#q').value;
    if (!v.trim()) return;
    const r = parseJump(v);
    msg.textContent = r.error || '';
    if (r.error) return;
    if (r.tsu) {
      if (!r.item) { location.hash = `#/${r.key}`; return; }
      const f = await findItem(r.key, r.item);
      if (!f) { msg.textContent = `${tsu(r.key).abbr}に「${r.item}」は見つかりません。目次から探してください。`; if (location.hash !== `#/${r.key}`) { keepMsg = true; location.hash = `#/${r.key}`; } return; }
      location.hash = `#/${f.key}/${encodeURIComponent(f.id)}`;
      $('#q').blur();
      return;
    }
    location.hash = `#/${r.key}` + (r.id ? `/${encodeURIComponent(r.id)}` : '') + (r.para ? `/${r.para}` : '');
    $('#q').blur();
  });

  // ── 経路 ───────────────────────────────────────────────
  async function route() {
    const parts = location.hash.replace(/^#\/?/, '').split('/').map(decodeURIComponent).filter(Boolean);
    try {
      if (!parts.length) homeView();
      else if (parts[1] === 's') await supplView(parts[0], parts[2]);
      else if (parts[1] === 'x') await appdxView(parts[0], parts[2]);
      else if (tsu(parts[0])) await tsuView(parts[0], parts[1]);
      else await lawView(parts[0], parts[1], parts[2]);
      if (!parts[1] || parts[1] === 's' || parts[1] === 'x') window.scrollTo(0, 0);
    } catch (err) {
      main.innerHTML = `<div class="page"><p class="loading">表示できませんでした：${esc(err.message)}<br>通信状態を確かめて、もう一度開いてください。</p></div>`;
    }
  }
  let keepMsg = false;
  window.addEventListener('hashchange', () => { if (!keepMsg) msg.textContent = ''; keepMsg = false; route(); });

  (async () => {
    try {
      INDEX = await getJSON('data/index.json');
      $('#footBuild').textContent = `データ生成 ${INDEX.built_at.slice(0, 10)}（各法令の施行日時点は目次に表示）`;
      route();
    } catch (err) {
      main.innerHTML = `<p class="loading">データを読み込めませんでした。通信状態を確かめて再読み込みしてください。</p>`;
    }
  })();

  if ('serviceWorker' in navigator && location.protocol === 'https:') {
    navigator.serviceWorker.register('sw.js').catch(() => {});
  }
})();
