/* ==========================================================================
   编辑页交互：音节卡片的增删、下加字叠层、实时预览、保存前校验
   合成藏文的逻辑全部放在服务端（tibetan.py），这里只负责收集数据并展示结果，
   避免前后端两套规则不一致。
   ========================================================================== */

(function () {
  'use strict';

  const COMPONENT_KEYS = [
    'prefix', 'superscript', 'root', 'subjoined', 'vowel', 'suffix', 'suffix2'
  ];
  // 除下加字外都是单个字符串；下加字是一个有序列表（可以叠多层）
  const SCALAR_KEYS = COMPONENT_KEYS.filter((key) => key !== 'subjoined');

  const container = document.getElementById('syllables');
  const template = document.getElementById('syllable-template');
  const subjoinedTemplate = document.getElementById('subjoined-row-template');
  const previewEl = document.getElementById('preview');
  const previewMeta = document.getElementById('preview-meta');
  const issuesEl = document.getElementById('issues');
  const autoJoinEl = document.getElementById('auto_join');
  const trailingShadEl = document.getElementById('trailing_shad');
  const form = document.getElementById('word-form');
  const componentsHidden = document.getElementById('components_json');
  const tibetanHidden = document.getElementById('tibetan_hidden');

  if (!container || !template || !subjoinedTemplate || !form) return;

  const MAX_ROWS = typeof MAX_SUBJOINED === 'number' ? MAX_SUBJOINED : 3;

  /** 最近一次服务端返回的校验结果 */
  let lastIssues = [];
  /** 用户是否已经确认过警告，确认后不再重复弹窗 */
  let warningsConfirmed = false;
  /** 最近一次预览得到的藏文 */
  let lastTibetan = '';

  // ------------------------------------------------------------ 读取状态
  /** 把「下加字」输入归一成数组；旧数据可能是字符串 */
  function normalizeSubjoined(value) {
    if (Array.isArray(value)) return value.filter(Boolean);
    if (typeof value === 'string' && value) return [value];
    return [];
  }

  function readState() {
    const syllables = [];
    container.querySelectorAll('.syllable-card').forEach((card) => {
      const syllable = {};

      SCALAR_KEYS.forEach((key) => {
        const select = card.querySelector('select[data-component="' + key + '"]');
        syllable[key] = select ? select.value : '';
      });

      // 下加字：按从上到下的顺序收集，空的那层直接丢掉
      syllable.subjoined = Array.from(card.querySelectorAll('.subjoined-row select'))
        .map((select) => select.value)
        .filter(Boolean);

      syllables.push(syllable);
    });
    return {
      version: 2,
      auto_join: autoJoinEl.checked,
      trailing_shad: trailingShadEl.checked,
      syllables: syllables
    };
  }

  function syllableIsBlank(syllable) {
    return COMPONENT_KEYS.every((key) => {
      const value = syllable[key];
      return Array.isArray(value) ? value.length === 0 : !value;
    });
  }

  function isBlank(state) {
    return state.syllables.every(syllableIsBlank);
  }

  //: 这两个字母结尾的词语后面不写 །；ང 结尾要先写 ་ 再写 །。
  //: 规则来源见 tibetan.py 的 append_trailing_shad，两边必须一致。
  const SHAD_ABSENT_AFTER = ['ཀ', 'ག'];
  const TSHEG_BEFORE_SHAD_AFTER = ['ང'];

  /** 取词尾的字母：跳过结尾的元音符号，并把下加形式换算回正常形式。
   *  གོ 的字母是 ག；སྐུ 剥掉 ུ 后是 ྐ，要换算回 ཀ。 */
  function finalLetter(text) {
    const stripped = String(text || '').replace(/[ཱ-ཽ]+$/, '');
    if (!stripped) return '';
    const last = stripped.charAt(stripped.length - 1);
    const code = last.charCodeAt(0);
    if (code >= 0x0F90 && code <= 0x0FBC) return String.fromCharCode(code - 0x50);
    return last;
  }

  /** 最后一个音节里有没有上加字/下加字（基字会写成下加形式 U+0F90–U+0FBC）。
   *  有的话一律加 །，不适用 ཀ/ག/ང 的规则：སྐུ། ལྔ། སྒོ། */
  function hasStack(text) {
    const tail = String(text || '').split('་').pop();
    for (let i = 0; i < tail.length; i += 1) {
      const code = tail.charCodeAt(i);
      if (code >= 0x0F90 && code <= 0x0FBC) return true;
    }
    return false;
  }

  /** 按词尾字母决定怎么收尾（tibetan.append_trailing_shad 的镜像） */
  function appendTrailingShad(text) {
    if (!text) return text;
    if (hasStack(text)) return text + '།';
    const last = finalLetter(text);
    if (SHAD_ABSENT_AFTER.indexOf(last) !== -1) return text;
    if (TSHEG_BEFORE_SHAD_AFTER.indexOf(last) !== -1) return text + '་།';
    return text + '།';
  }

  /**
   * 没有音节组件的词条会有一个直接输入藏文的框；返回它当前应当被保存的文本。
   *
   * 这段文本服务端无法重新合成，所以「词尾加 །」的规则要两边各应用一次
   * （app.py 的 word_from_request 里是同一套逻辑，那边才是**权威**的，
   * 这里只是为了让预览和最终保存的结果一致）。
   * 先去掉结尾已有的 །（连同它前面的 ་）再按词尾重新收尾，反复保存不会越加越多。
   */
  function typedTibetan() {
    const box = document.getElementById('tibetan_text');
    if (!box) return '';
    const text = box.value.trim();
    if (!text) return '';
    return trailingShadEl.checked
      ? appendTrailingShad(text.replace(/་?།+$/, ''))
      : text;
  }

  // ------------------------------------------------------------ 下加字叠层
  function addSubjoinedRow(card, value) {
    const rows = card.querySelector('.subjoined-rows');
    if (!rows || rows.querySelectorAll('.subjoined-row').length >= MAX_ROWS) return null;

    const node = subjoinedTemplate.content.firstElementChild.cloneNode(true);
    const select = node.querySelector('select');
    select.value = value || '';
    select.addEventListener('change', scheduleRefresh);

    node.querySelector('.btn-remove-subjoined').addEventListener('click', () => {
      node.remove();
      syncSubjoinedButtons(card);
      scheduleRefresh();
    });

    rows.appendChild(node);
    syncSubjoinedButtons(card);
    return node;
  }

  /** 层数到上限时禁用「+ 加一层」 */
  function syncSubjoinedButtons(card) {
    const button = card.querySelector('.btn-add-subjoined');
    if (!button) return;
    const count = card.querySelectorAll('.subjoined-row').length;
    button.disabled = count >= MAX_ROWS;
    button.title = button.disabled
      ? '最多 ' + MAX_ROWS + ' 层下加字'
      : '藏文里下加字之上通常只再跟一个 ྭ（wa-zur），如 གྲྭ、དྲྭ་བ、ཕྱྭ';
  }

  // ------------------------------------------------------------ 音节卡片
  function addCard(values) {
    const node = template.content.firstElementChild.cloneNode(true);

    SCALAR_KEYS.forEach((key) => {
      const select = node.querySelector('select[data-component="' + key + '"]');
      if (select && values && values[key]) select.value = values[key];
    });

    node.querySelector('.btn-remove').addEventListener('click', () => {
      node.remove();
      // 至少保留一张卡片，否则页面会空掉
      if (!container.querySelector('.syllable-card')) addCard(null);
      scheduleRefresh();
    });

    node.querySelectorAll('select').forEach((select) => {
      select.addEventListener('change', scheduleRefresh);
    });

    node.querySelector('.btn-add-subjoined').addEventListener('click', () => {
      const row = addSubjoinedRow(node, '');
      if (row) {
        row.querySelector('select').focus();
        scheduleRefresh();
      }
    });

    // 先挂进文档再建下加字行，这样卡片在页面上就已经是完整结构
    container.appendChild(node);

    const initial = normalizeSubjoined(values && values.subjoined);
    (initial.length ? initial : ['']).forEach((letter) => addSubjoinedRow(node, letter));

    renumber();
    return node;
  }

  function renumber() {
    container.querySelectorAll('.syllable-card').forEach((card, index) => {
      const label = card.querySelector('.index-num');
      if (label) label.textContent = String(index + 1);
    });
  }

  // ------------------------------------------------------------ 预览刷新
  let timer = null;
  function scheduleRefresh() {
    renumber();
    window.clearTimeout(timer);
    timer = window.setTimeout(refresh, 140);
  }

  async function refresh() {
    const state = readState();

    // 音节卡片都还没填时，不显示报错，给一句中性提示
    if (isBlank(state)) {
      const typed = typedTibetan();
      lastIssues = [];
      lastTibetan = typed;   // 提交时会被写进 tibetan 隐藏域
      previewEl.textContent = typed || ' ';
      previewMeta.textContent = typed ? '直接输入的藏文（未使用音节组件）' : '';
      renderIssues([], !typed);
      updateCardPreviews(state.syllables.map(() => ''));
      applyAllowed([]);   // 没有内容时解除所有置灰，别留下上一轮的痕迹
      return;
    }

    try {
      const response = await fetch('/api/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(state)
      });
      if (!response.ok) throw new Error('HTTP ' + response.status);

      const data = await response.json();
      lastTibetan = data.tibetan || '';
      lastIssues = data.issues || [];

      previewEl.textContent = lastTibetan || '（请至少选择基字）';
      const syllableCount = state.syllables.filter((s) => s.root).length;
      previewMeta.textContent = lastTibetan
        ? syllableCount + ' 个音节' + (state.auto_join ? '，用 ་ 连接' : '，直接相连')
        : '';

      renderIssues(lastIssues, false);
      updateCardPreviews(data.syllable_strings || []);
      applyAllowed(data.allowed || []);
    } catch (error) {
      previewMeta.textContent = '预览失败：' + error.message;
    }
  }

  function updateCardPreviews(strings) {
    container.querySelectorAll('.syllable-card').forEach((card, index) => {
      const box = card.querySelector('.syllable-preview');
      if (box) box.textContent = strings[index] || '';
    });
  }

  // -------------------------------------------------- 按规则过滤下拉框
  // 服务端 /api/preview 会连「每个字段还能选什么」一起返回（tibetan.allowed_components），
  // 这里把它应用到下拉框上：不在名单里的选项置灰。
  //
  // 用 disabled 而不是把 option 删掉，有三个原因：
  //   1. 选项位置保持不动，不会因为列表变短而跳来跳去；
  //   2. 用户看得见「这个确实配不了」，本身就是规则提示；
  //   3. 删掉再重建容易把当前选中项弄丢。
  // 另外「无」和「当前已选中项」永远不置灰——否则界面看起来像坏了。
  function disableUnlisted(select, values) {
    select.querySelectorAll('option').forEach(function (option) {
      if (option.value === '' || option.selected) {
        option.disabled = false;
        return;
      }
      option.disabled = values !== null && values.indexOf(option.value) === -1;
    });
  }

  function applyAllowed(allowedList) {
    container.querySelectorAll('.syllable-card').forEach(function (card, index) {
      const allowed = allowedList[index];
      function valuesFor(key) {
        return (allowed && allowed[key]) || null;
      }

      // 下加字是叠层：第一层用 subjoined，第二层及以后用 subjoined_tail
      card.querySelectorAll('.subjoined-rows').forEach(function (rows) {
        let rowIndex = 0;
        rows.querySelectorAll('select[data-component="subjoined"]').forEach(function (select) {
          disableUnlisted(select, valuesFor(rowIndex === 0 ? 'subjoined' : 'subjoined_tail'));
          rowIndex += 1;
        });
      });

      card.querySelectorAll('select[data-component]').forEach(function (select) {
        if (select.dataset.component === 'subjoined') return;   // 上面已处理
        disableUnlisted(select, valuesFor(select.dataset.component));
      });
    });
  }

  function renderIssues(issues, blank) {
    issuesEl.innerHTML = '';

    if (blank) {
      const hint = document.createElement('div');
      hint.className = 'issue';
      hint.style.background = '#f4f1ec';
      hint.style.color = '#6b6259';
      hint.textContent = '填写组件后，这里会实时显示合成结果与组合提示。';
      issuesEl.appendChild(hint);
      return;
    }

    issues.forEach((issue) => {
      const div = document.createElement('div');
      div.className = 'issue issue-' + (issue.level === 'error' ? 'error' : 'warning');
      div.textContent = (issue.level === 'error' ? '✕ ' : '⚠ ') + issue.message;
      issuesEl.appendChild(div);
    });
  }

  // ------------------------------------------------------------ 提交
  function syncHidden() {
    const state = readState();
    componentsHidden.value = JSON.stringify(state);
    // 卡片里没有基字时，藏文以「直接输入」框的内容为准；
    // 有基字时以服务端合成结果为准（服务端也会再合成一次，这里只是保持一致）
    tibetanHidden.value = isBlank(state) ? typedTibetan() : (lastTibetan || '');
  }

  form.addEventListener('submit', (event) => {
    const state = readState();
    syncHidden();

    // 没有音节组件的词条允许只靠「直接输入藏文」那个框提交
    if (isBlank(state) && !typedTibetan()) {
      event.preventDefault();
      alert('请至少为一个音节选择基字。');
      return;
    }

    const errors = lastIssues.filter((issue) => issue.level === 'error');
    if (errors.length > 0) {
      event.preventDefault();
      alert('还有问题需要修正：\n\n' + errors.map((i) => '· ' + i.message).join('\n'));
      return;
    }

    const warnings = lastIssues.filter((issue) => issue.level === 'warning');
    if (warnings.length > 0 && !warningsConfirmed) {
      event.preventDefault();
      const detail = warnings.map((i) => '· ' + i.message).join('\n');
      const ok = confirm(
        '下列组合在藏文正字法中不常见：\n\n' + detail +
        '\n\n这不影响保存，但建议再核对一遍拼写。仍然保存吗？'
      );
      if (ok) {
        warningsConfirmed = true;
        form.submit();   // 用原生 submit，跳过本监听器
      }
      return;
    }
  });

  // ------------------------------------------------------------ 初始化
  autoJoinEl.addEventListener('change', () => {
    warningsConfirmed = false;
    scheduleRefresh();
  });

  // 词尾 ཤད（།）开关：只是给预览叠加一个字符，走服务端合成保持一致
  trailingShadEl.addEventListener('change', () => {
    warningsConfirmed = false;
    scheduleRefresh();
  });

  // 没有音节组件时才会出现的「直接输入藏文」框
  const tibetanTextBox = document.getElementById('tibetan_text');
  if (tibetanTextBox) {
    tibetanTextBox.addEventListener('input', () => {
      warningsConfirmed = false;
      scheduleRefresh();
    });
  }

  document.getElementById('btn-add-syllable').addEventListener('click', () => {
    const card = addCard(null);
    const firstSelect = card.querySelector('select[data-component="root"]');
    if (firstSelect) firstSelect.focus();
    scheduleRefresh();
  });

  // 用服务端回填的组件建卡片；没有组件时给一张空卡片
  const initialSyllables = (INITIAL && INITIAL.syllables) || [];
  if (initialSyllables.length > 0) {
    initialSyllables.forEach((syllable) => addCard(syllable));
  } else {
    addCard(null);
  }

  // 保存后重新进入页面时，立刻展示一次服务端的校验结果
  refresh();
})();
