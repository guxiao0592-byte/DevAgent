/* ============================================================
   DevAgent Frontend — Requirements Analysis View
   Fetches real documents and diagrams from backend.
   ============================================================ */

App.registerView('requirements', (container) => {
  container.innerHTML = `
    <div class="mb-16 flex items-center justify-between">
      <div>
        <h2>📋 需求分析</h2>
        <span class="view-breadcrumb" id="req-subtitle">加载中...</span>
      </div>
      <div class="flex gap-8">
        <button class="btn" onclick="ReqView.load()">🔄 刷新</button>
        <button class="btn" onclick="App.navigate('high-design')">总体设计 →</button>
      </div>
    </div>

    <div class="tabs">
      <div class="tab active" data-tab="doc">📄 需求文档</div>
      <div class="tab" data-tab="diagrams">📊 分析图表</div>
      <div class="tab" data-tab="domain">🏷️ 领域模型</div>
    </div>

    <div id="req-tab-doc" class="req-tab-content">
      <div class="card" id="requirements-doc-card">
        <div class="card-header">
          <span class="card-title">IEEE 830 软件需求规格说明书 (SRS)</span>
          <div class="flex gap-8">
            <button class="btn btn-sm" onclick="ReqView._downloadDoc()">📥 下载</button>
            <button class="btn btn-sm" onclick="ReqView._fullscreenDoc()">⛶ 全屏</button>
          </div>
        </div>
        <div id="requirements-doc-content" style="padding:20px;line-height:1.8;font-size:0.9rem;max-height:70vh;overflow-y:auto;">
          <div class="empty-state"><span class="spinner"></span> 加载文档...</div>
        </div>
      </div>
    </div>

    <div id="req-tab-diagrams" class="req-tab-content" style="display:none;">
      <div id="req-diagrams-grid" style="display:flex;flex-direction:column;gap:20px;">
        <div class="empty-state"><span class="spinner"></span> 加载图表...</div>
      </div>
    </div>

    <div id="req-tab-domain" class="req-tab-content" style="display:none;">
      <div class="grid-2">
        <div class="card"><div class="card-header"><span class="card-title">🏷️ 领域实体</span></div>
          <div id="req-domain-entities"><div class="empty-state"><span class="spinner"></span> 加载...</div></div>
        </div>
        <div class="card"><div class="card-header"><span class="card-title">📝 需求统计</span></div>
          <div id="req-stats"><div class="empty-state"><span class="spinner"></span> 加载...</div></div>
        </div>
      </div>
    </div>
  `;

  container.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
      container.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      container.querySelectorAll('.req-tab-content').forEach(c => c.style.display = 'none');
      const target = document.getElementById(`req-tab-${tab.dataset.tab}`);
      if (target) target.style.display = '';
    });
  });

  ReqView.load();
});

const ReqView = {
  _docContent: '',

  async load() {
    const taskId = App.state.activeTaskId;
    if (!taskId) { this._showEmpty(); return; }

    try {
      const [doc, diagrams, structured] = await Promise.all([
        API.getPhaseDocument(taskId, 'requirements').catch(() => null),
        API.getPhaseDiagrams(taskId, 'requirements').catch(() => null),
        API.getPhaseStructured(taskId, 'requirements').catch(() => null),
      ]);

      document.getElementById('req-subtitle').textContent =
        doc ? `${doc.filename} — ${doc.format}` : '该任务暂未生成需求文档';

      this._renderDoc(doc);
      this._renderDiagrams(diagrams);
      this._renderDomain(structured);
    } catch (e) {
      console.error('Requirements load error:', e);
      this._showEmpty();
    }
  },

  _showEmpty() {
    const taskId = App.state.activeTaskId;
    const msg = taskId
      ? `<p>该任务未找到需求分析产出。请等待任务完成需求分析阶段。</p>
         <button class="btn btn-primary mt-12" onclick="ReqView.load()">🔄 重试</button>`
      : `<p>请先选择或创建一个任务来查看需求分析。</p>
         <button class="btn btn-primary mt-12" onclick="App.navigate('task-create')">➕ 创建任务</button>`;
    document.getElementById('requirements-doc-content').innerHTML = `<div class="empty-state">${msg}</div>`;
    document.getElementById('req-diagrams-grid').innerHTML = `<div class="empty-state">${msg}</div>`;
    document.getElementById('req-domain-entities').innerHTML = `<div class="empty-state">${msg}</div>`;
    document.getElementById('req-subtitle').textContent = '无数据';
  },

  _renderDoc(doc) {
    const el = document.getElementById('requirements-doc-content');
    if (!doc || !doc.content) {
      el.innerHTML = '<div class="empty-state"><p>需求文档尚未生成</p></div>';
      return;
    }
    this._docContent = doc.content;
    el.innerHTML = Utils.markdownToHtml(doc.content);
    // Add syntax highlighting to code blocks
    el.querySelectorAll('pre code').forEach(b => {
      b.innerHTML = Utils.highlightCode(b.textContent, b.className.replace('language-',''));
    });
  },

  _renderDiagrams(diagramsData) {
    const grid = document.getElementById('req-diagrams-grid');
    const diagrams = diagramsData?.diagrams || [];
    if (!diagrams.length) {
      grid.innerHTML = '<div class="empty-state"><p>该阶段无图表产出</p></div>';
      return;
    }

    grid.innerHTML = '';
    diagrams.forEach((d, i) => {
      const card = document.createElement('div');
      card.className = 'card';
      card.innerHTML = `
        <div class="card-header">
          <span class="card-title">📊 ${d.name}</span>
          <div class="flex gap-8">
            <button class="btn btn-sm" id="req-dl-btn-${i}">📥 SVG</button>
            <button class="btn btn-sm" onclick="Diagrams._fs(document.getElementById('req-diag-${i}'))">⛶ 全屏</button>
          </div>
        </div>
        <div id="req-diag-${i}" style="min-height:100px;"></div>`;
      grid.appendChild(card);

      // Render diagram
      if (d.format === 'mermaid') {
        Diagrams.render(`req-diag-${i}`, d.code, { toolbar: false, title: d.name });
      } else if (d.format === 'plantuml') {
        document.getElementById(`req-diag-${i}`).innerHTML = `
          <div style="text-align:center;padding:20px;color:var(--text-muted);font-size:0.85rem;">
            <p>🌿 PlantUML 图 — <code>${d.filename}</code></p>
            <pre style="text-align:left;max-height:200px;overflow:auto;margin-top:8px;font-size:0.72rem;">${Utils.escapeHtml(d.code)}</pre>
          </div>`;
      }

      // Download button
      document.getElementById(`req-dl-btn-${i}`).onclick = () => ReqView._downloadDiagramSVG(`req-diag-${i}`, d.name);
    });
  },

  _renderDomain(structured) {
    const entitiesEl = document.getElementById('req-domain-entities');
    const statsEl = document.getElementById('req-stats');
    const data = structured?.data || {};

    // Try various JSON structures
    const domain = data.domain_model || data;
    const entities = domain?.entities || data?.entities || [];
    const frs = data.functional_requirements || data?.functionalRequirements || [];
    const nfrs = data.nonfunctional_requirements || data?.nonFunctionalRequirements || [];
    const useCases = data.use_cases || data?.useCases || [];

    if (entities.length) {
      entitiesEl.innerHTML = entities.map(e => `
        <div style="padding:10px;margin-bottom:6px;background:var(--bg-card);border-radius:var(--radius-sm);border:1px solid var(--border);">
          <strong style="color:var(--accent);">${Utils.escapeHtml(e.name)}</strong>
          <div style="font-size:0.78rem;color:var(--text-muted);margin-top:2px;">${Utils.escapeHtml(e.description || '')}</div>
          ${(e.attributes || []).length ? `<div style="font-size:0.72rem;color:var(--text-secondary);margin-top:4px;">
            ${e.attributes.slice(0,8).map(a => `<code style="font-size:0.68rem;">${a.name || a}</code>`).join(' ')}
          </div>` : ''}
        </div>`).join('');
    } else {
      entitiesEl.innerHTML = '<div class="empty-state"><p>暂无结构化实体数据</p></div>';
    }

    statsEl.innerHTML = `
      <div style="padding:8px;line-height:2.2;">
        <div>📋 功能需求: <strong>${frs.length || '—'}</strong></div>
        <div>⚙️ 非功能需求: <strong>${nfrs.length || '—'}</strong></div>
        <div>🎭 用例: <strong>${useCases.length || '—'}</strong></div>
        <div>🏷️ 领域实体: <strong>${entities.length || '—'}</strong></div>
        ${frs.length ? `<div class="mt-8">${frs.filter(f => f.priority === 'high' || f.priority === 'critical').length} 个高优先级需求</div>` : ''}
      </div>`;
  },

  _fullscreenDoc() {
    const overlay = document.createElement('div');
    overlay.style.cssText = 'position:fixed;inset:0;z-index:9999;background:var(--bg-primary);padding:32px;overflow:auto;';
    overlay.innerHTML = `
      <button style="position:fixed;top:16px;right:16px;z-index:1;font-size:24px;border:none;background:none;cursor:pointer;color:var(--text-primary);" onclick="this.parentElement.remove()">✕</button>
      <div style="max-width:900px;margin:0 auto;line-height:1.8;font-size:0.9rem;">${Utils.markdownToHtml(this._docContent)}</div>`;
    document.body.appendChild(overlay);
  },

  _downloadDoc() {
    const blob = new Blob([this._docContent], { type: 'text/markdown' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'requirements_specification.md';
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
  },

  _downloadDiagramSVG(containerId, name) {
    Diagrams._dl(document.getElementById(containerId), name);
  },
};
