/* ============================================================
   DevAgent Frontend — Debug & Repair View
   Fetches real documents and diagrams from backend.
   ============================================================ */

App.registerView('debug-repair', (container) => {
  container.innerHTML = `
    <div class="mb-16 flex items-center justify-between">
      <div>
        <h2>🐛 调试与修复</h2>
        <span class="view-breadcrumb" id="dr-subtitle">加载中...</span>
      </div>
      <div class="flex gap-8">
        <button class="btn" onclick="DebugView.load()">🔄 刷新</button>
        <button class="btn" onclick="App.navigate('reports')">报告文档 →</button>
      </div>
    </div>

    <div class="tabs">
      <div class="tab active" data-tab="doc">📄 修复文档</div>
      <div class="tab" data-tab="diagrams">📊 分析图表</div>
    </div>

    <div id="dr-tab-doc" class="dr-tab-content">
      <div class="card">
        <div class="card-header">
          <span class="card-title">🔧 修复与验证报告</span>
          <div class="flex gap-8">
            <button class="btn btn-sm" id="dr-dl-doc">📥 下载</button>
            <button class="btn btn-sm" id="dr-fs-doc">⛶ 全屏</button>
          </div>
        </div>
        <div id="dr-doc-content" style="padding:20px;line-height:1.8;font-size:0.9rem;max-height:70vh;overflow-y:auto;">
          <div class="empty-state"><span class="spinner"></span> 加载修复文档...</div>
        </div>
      </div>
    </div>

    <div id="dr-tab-diagrams" class="dr-tab-content" style="display:none;">
      <div id="dr-diagrams-grid" style="display:flex;flex-direction:column;gap:20px;">
        <div class="empty-state"><span class="spinner"></span> 加载图表...</div>
      </div>
    </div>
  `;

  container.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
      container.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      container.querySelectorAll('.dr-tab-content').forEach(c => c.style.display = 'none');
      const target = document.getElementById(`dr-tab-${tab.dataset.tab}`);
      if (target) target.style.display = '';
    });
  });

  DebugView.load();
});

const DebugView = {
  _docContent: '',
  _diagrams: [],

  async load() {
    const taskId = App.state.activeTaskId;
    if (!taskId) { this._showEmpty(); return; }

    try {
      const [doc, diagrams] = await Promise.all([
        API.getPhaseDocument(taskId, 'repair').catch(() => null),
        API.getPhaseDiagrams(taskId, 'repair').catch(() => null),
      ]);

      this._diagrams = diagrams?.diagrams || [];
      document.getElementById('dr-subtitle').textContent =
        doc ? doc.filename : (this._diagrams.length ? `${this._diagrams.length} 个图表` : '无数据');

      this._renderDoc(doc);
      this._renderDiagrams();
    } catch (e) {
      console.error('Debug load error:', e);
      this._showEmpty();
    }
  },

  _showEmpty() {
    document.getElementById('dr-doc-content').innerHTML =
      '<div class="empty-state"><p>该任务暂无修复数据。可能任务成功完成无需修复，或修复阶段尚未执行。</p></div>';
    document.getElementById('dr-diagrams-grid').innerHTML =
      '<div class="empty-state"><p>该阶段无图表</p></div>';
  },

  _renderDoc(doc) {
    const el = document.getElementById('dr-doc-content');
    if (doc && doc.content) {
      this._docContent = doc.content;
      el.innerHTML = Utils.markdownToHtml(doc.content);
      el.querySelectorAll('pre code').forEach(b => {
        b.innerHTML = Utils.highlightCode(b.textContent, b.className.replace('language-',''));
      });
    } else {
      el.innerHTML = '<div class="empty-state"><p>修复阶段文档未生成</p></div>';
    }
    document.getElementById('dr-dl-doc').onclick = () => {
      const blob = new Blob([this._docContent], { type: 'text/markdown' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob); a.download = 'repair_report.md';
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
    };
    document.getElementById('dr-fs-doc').onclick = () => {
      const overlay = document.createElement('div');
      overlay.style.cssText = 'position:fixed;inset:0;z-index:9999;background:var(--bg-primary);padding:32px;overflow:auto;';
      overlay.innerHTML = `<button style="position:fixed;top:16px;right:16px;z-index:1;font-size:24px;border:none;background:none;cursor:pointer;color:var(--text-primary);" onclick="this.parentElement.remove()">✕</button>
        <div style="max-width:900px;margin:0 auto;line-height:1.8;font-size:0.9rem;">${Utils.markdownToHtml(this._docContent)}</div>`;
      document.body.appendChild(overlay);
    };
  },

  _renderDiagrams() {
    const grid = document.getElementById('dr-diagrams-grid');
    if (!this._diagrams.length) {
      grid.innerHTML = '<div class="empty-state"><p>该阶段无图表产出</p></div>';
      return;
    }

    grid.innerHTML = '';
    this._diagrams.forEach((d, i) => {
      const card = document.createElement('div');
      card.className = 'card';
      card.innerHTML = `
        <div class="card-header">
          <span class="card-title">📊 ${d.name}</span>
          <div class="flex gap-8">
            <button class="btn btn-sm" id="dr-dl-${i}">📥 SVG</button>
          </div>
        </div>
        <div id="dr-diag-${i}" style="min-height:100px;"></div>`;
      grid.appendChild(card);

      if (d.format === 'mermaid') {
        Diagrams.render(`dr-diag-${i}`, d.code, { toolbar: false, title: d.name });
      } else {
        document.getElementById(`dr-diag-${i}`).innerHTML = `
          <pre style="text-align:left;max-height:200px;overflow:auto;padding:12px;font-size:0.72rem;">${Utils.escapeHtml(d.code.substring(0, 500))}</pre>`;
      }

      document.getElementById(`dr-dl-${i}`).onclick = () =>
        Diagrams._dl(document.getElementById(`dr-diag-${i}`), d.name);
    });
  },
};
