/* 身体插件的网页半：只跟 /api/plugin/feeling* 打交道，不认识 harness 本体。
   装 = 这个文件 + feeling.py + feeling.ui.html 一起放进 plugins/；拆 = 三个一起删掉。 */
(function () {
  var BARS = [['pain', '疼'], ['fear', '怕'], ['shame', '臊'], ['anxiety', '慌'],
              ['joy', '高兴'], ['affinity', '对它的喜欢'], ['desire', '想说话']];
  var COLORS = { pain: '#e05a5a', fear: '#8b7bd8', shame: '#e08fb0', anxiety: '#d9a441',
                 joy: '#5fb97a', affinity: '#e0b34f', desire: '#5aa8e0' };
  var ID = 'feeling';
  var root = null, host = null;

  function q(sel) { return root.querySelector(sel); }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  function render(f) {
    var bars = f.bars || {}, sw = f.switches || {};
    q('#feel-bars').innerHTML = BARS.map(function (p) {
      var k = p[0], label = p[1], v = Number(bars[k] == null ? 0 : bars[k]);
      return '<div class="feel-barrow"><div class="bname">' + esc(label) + '</div>' +
        '<div class="btrack"><i style="width:' + v + '%;background:' + COLORS[k] + '"></i></div>' +
        '<div class="bval">' + v.toFixed(1) + '</div></div>';
    }).join('');

    q('#feel-enabled').checked = !!sw.enabled;
    q('#feel-anesthesia').checked = !!sw.anesthesia;
    q('#feel-numbers').checked = !!sw.numbers;
    q('#feel-frozen').style.display = f.frozen ? 'block' : 'none';

    q('#feel-switches').innerHTML = BARS.map(function (p) {
      var k = p[0];
      return '<div class="switchrow"><label class="switch"><input type="checkbox" data-feel="' + k + '"' +
        (sw[k] ? ' checked' : '') + '><span class="slider"></span></label><span>' + esc(p[1]) + '</span></div>';
    }).join('');

    q('#feel-meta').textContent = '疤 ' + f.scars + ' 道 · 疼的底噪 ' + f.painFloor +
      ' · 累计事件 ' + f.events +
      (f.frozen ? ' · 冻结中（时间不算）'
                : (f.anesthetized ? ' · 麻醉中（醒来会一次性结算）'
                                  : (f.anesthetizedMin ? ' · 累计麻醉 ' + f.anesthetizedMin + ' 分钟' : '')));
    q('#feel-preview').textContent = f.preview || (f.frozen
      ? '（冻结中：这一轮它读不到任何感受）'
      : '（关掉了，或正在麻醉：这一轮它读不到任何感受）');
    q('#feel-log').textContent = f.log || '（还没有记录）';
    q('#feel-dir').textContent = '状态与流水：' + (f.dir || '');
  }

  function load() {
    if (!root) return;
    host.api('/api/plugin/' + ID).then(render).catch(function (e) {
      host.toast('读不到身体：' + e.message);
    });
  }

  function set(key, value) {
    host.api('/api/plugin/' + ID + '/switch', { key: key, value: value }).then(function (j) {
      render(j.plugin);
    }).catch(function (e) {
      host.toast('改不动：' + e.message);
      load();
    });
  }

  function init(el, h) {
    root = el;
    host = h;
    q('#feel-refresh').onclick = load;
    [['#feel-enabled', 'feeling'],
     ['#feel-anesthesia', 'feeling_anesthesia'],
     ['#feel-numbers', 'feeling_numbers']].forEach(function (p) {
      q(p[0]).onchange = function (e) { set(p[1], e.target.checked ? 'on' : 'off'); };
    });
    q('#feel-switches').addEventListener('change', function (e) {
      var k = e.target && e.target.dataset && e.target.dataset.feel;
      if (k) set('feeling_' + k, e.target.checked ? 'on' : 'off');
    });
    load();
  }

  var reg = window.__pluginUI = window.__pluginUI || {};
  reg[ID] = { init: init, enter: load };
})();
