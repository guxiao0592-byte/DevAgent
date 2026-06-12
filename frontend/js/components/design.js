/* ============================================================
   DevAgent Frontend — Design Views (High-Level + Detailed)
   Fetches real documents and diagrams from backend.
   ============================================================ */

App.registerView('high-design', (container) => {
  container.innerHTML = `
    <div class="mb-16 flex items-center justify-between">
      <div>
        <h2>🏗️ 总体设计 + 详细设计</h2>
        <span class="view-breadcrumb" id="design-subtitle">加载中...</span>
      </div>
      <div class="flex gap-8">
        <button class="btn" onclick="DesignView.load()">🔄 刷新</button>
        <button class="btn" onclick="App.navigate('implementation')">代码实现 →</button>
      </div>
    </div>

    <div class="tabs">
      <div class="tab active" data-tab="doc">📄 设计文档</div>
      <div class="tab" data-tab="diagrams">📊 设计图表</div>
      <div class="tab" data-tab="tech">🛠 技术栈</div>
    </div>

    <div id="ds-tab-doc" class="ds-tab-content">
      <div class="card">
        <div class="card-header">
          <span class="card-title">IEEE 1016 软件设计说明书 (SDD)</span>
          <div class="flex gap-8">
            <button class="btn btn-sm" onclick="DesignView._downloadDoc()">📥 下载</button>
            <button class="btn btn-sm" onclick="DesignView._fullscreenDoc()">⛶ 全屏</button>
          </div>
        </div>
        <div id="design-doc-content" style="padding:20px;line-height:1.8;font-size:0.9rem;max-height:70vh;overflow-y:auto;">
          <div class="empty-state"><span class="spinner"></span> 加载设计文档...</div>
        </div>
      </div>
    </div>

    <div id="ds-tab-diagrams" class="ds-tab-content" style="display:none;">
      <div id="design-diagrams-grid" style="display:flex;flex-direction:column;gap:20px;">
        <div class="empty-state"><span class="spinner"></span> 加载图表...</div>
      </div>
    </div>

    <div id="ds-tab-tech" class="ds-tab-content" style="display:none;">
      <div id="design-tech-content">
        <div class="empty-state"><span class="spinner"></span> 加载技术栈...</div>
      </div>
    </div>
  `;

  container.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
      container.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      container.querySelectorAll('.ds-tab-content').forEach(c => c.style.display = 'none');
      const target = document.getElementById(`ds-tab-${tab.dataset.tab}`);
      if (target) target.style.display = '';
    });
  });

  DesignView.load();
});

const DesignView = {
  _docContent: '',

  async load() {
    const taskId = App.state.activeTaskId;
    if (!taskId) { this._showEmpty(); return; }

    try {
      const [doc, diagrams, structured] = await Promise.all([
        API.getPhaseDocument(taskId, 'design').catch(() => null),
        API.getPhaseDiagrams(taskId, 'design').catch(() => null),
        API.getPhaseStructured(taskId, 'design').catch(() => null),
      ]);

      document.getElementById('design-subtitle').textContent =
        doc ? `${doc.filename}` : '该任务暂未生成设计文档';

      this._renderDoc(doc);
      this._renderDiagrams(diagrams);
      this._renderTech(structured, diagrams);
    } catch (e) {
      console.error('Design load error:', e);
      this._showEmpty();
    }
  },

  _showEmpty() {
    const taskId = App.state.activeTaskId;
    const msg = taskId
      ? `<p>该任务未找到设计产出。请等待任务完成设计阶段。</p>
         <button class="btn btn-primary mt-12" onclick="DesignView.load()">🔄 重试</button>`
      : `<p>请先选择或创建一个任务来查看设计文档。</p>
         <button class="btn btn-primary mt-12" onclick="App.navigate('task-create')">➕ 创建任务</button>`;
    document.getElementById('design-doc-content').innerHTML = `<div class="empty-state">${msg}</div>`;
    document.getElementById('design-diagrams-grid').innerHTML = `<div class="empty-state">${msg}</div>`;
    document.getElementById('design-tech-content').innerHTML = `<div class="empty-state">${msg}</div>`;
  },

  _renderDoc(doc) {
    const el = document.getElementById('design-doc-content');
    if (!doc || !doc.content) {
      el.innerHTML = '<div class="empty-state"><p>设计文档尚未生成</p></div>';
      return;
    }
    this._docContent = doc.content;
    // Render markdown, but extract Mermaid blocks for live rendering
    let html = Utils.markdownToHtml(doc.content);

    // After HTML conversion, find mermaid code blocks and insert diagram containers
    // The markdown renderer wraps ```mermaid blocks in <pre><code>
    // We'll post-process: replace <pre><code class="language-mermaid"> with a div
    const mermaidRegex = /<pre><code class="language-mermaid">([\s\S]*?)<\/code><\/pre>/g;
    let counter = 0;
    html = html.replace(mermaidRegex, (_, code) => {
      const id = `doc-mmd-${counter++}`;
      // Schedule rendering after DOM update
      setTimeout(() => {
        const div = document.getElementById(id);
        if (div) Diagrams.render(id, code, { toolbar: true, title: `图 ${counter}` });
      }, 100);
      return `<div id="${id}" style="min-height:100px;background:#fff;border-radius:8px;margin:16px 0;padding:16px;"></div>`;
    });

    el.innerHTML = html;
    el.querySelectorAll('pre code').forEach(b => {
      const lang = b.className.replace('language-', '');
      if (lang !== 'mermaid') b.innerHTML = Utils.highlightCode(b.textContent, lang);
    });
  },

  _renderDiagrams(diagramsData) {
    const grid = document.getElementById('design-diagrams-grid');
    const diagrams = diagramsData?.diagrams || [];
    if (!diagrams.length) {
      grid.innerHTML = '<div class="empty-state"><p>该阶段无图表产出</p></div>';
      return;
    }

    // Group by type for better organization
    const grouped = {};
    for (const d of diagrams) {
      const t = d.type || 'other';
      if (!grouped[t]) grouped[t] = [];
      grouped[t].push(d);
    }

    const typeLabels = {
      class: '📊 类图', er: '🗄 ER 图', sequence: '⏱ 时序图', state: '🔄 状态机',
      flowchart: '🔀 流程图', component: '🧩 组件图', deployment: '☸️ 部署图',
      usecase: '🎭 用例图', dfd: '🔀 数据流图', activity: '🎯 活动图', other: '📎 其他',
    };

    grid.innerHTML = '';
    for (const [type, items] of Object.entries(grouped)) {
      items.forEach((d, i) => {
        const card = document.createElement('div');
        card.className = 'card';
        card.innerHTML = `
          <div class="card-header">
            <span class="card-title">${typeLabels[type] || '📎'} ${d.name}</span>
            <div class="flex gap-8">
              <button class="btn btn-sm" id="ds-dl-${type}-${i}">📥 SVG</button>
              <button class="btn btn-sm" id="ds-fs-${type}-${i}">⛶ 全屏</button>
            </div>
          </div>
          <div id="ds-diag-${type}-${i}" style="min-height:100px;"></div>`;
        grid.appendChild(card);

        const diagId = `ds-diag-${type}-${i}`;
        if (d.format === 'mermaid') {
          Diagrams.render(diagId, d.code, { toolbar: false, title: d.name });
        } else {
          document.getElementById(diagId).innerHTML = `
            <div style="text-align:center;padding:20px;color:var(--text-muted);font-size:0.85rem;">
              <p>🌿 PlantUML 图 — <code>${Utils.escapeHtml(d.filename)}</code></p>
              <pre style="text-align:left;max-height:200px;overflow:auto;margin-top:8px;font-size:0.72rem;">${Utils.escapeHtml(d.code.substring(0, 500))}</pre>
            </div>`;
        }

        document.getElementById(`ds-dl-${type}-${i}`).onclick = () => DesignView._downloadDiagramSVG(diagId, d.name);
        document.getElementById(`ds-fs-${type}-${i}`).onclick = () => {
          const el = document.getElementById(diagId);
          const dc = el?.querySelector('.diagram-container');
          if (dc) Diagrams._fs(el);
        };
      });
    }
  },

  _renderTech(structured, diagramsData) {
    const el = document.getElementById('design-tech-content');
    const data = structured?.data || {};
    const arch = data.architecture_overview || {};
    const techStack = data.technology_stack || data.tech_stack || {};
    const modules = data.module_division || [];

    const diagrams = diagramsData?.diagrams || [];
    const diagramTypes = new Set(diagrams.map(d => d.type).filter(Boolean));

    let html = '<div class="grid-3">';

    // Architecture summary
    html += `<div class="card">
      <div class="card-header"><span class="card-title">🏛️ 架构模式</span></div>
      <div style="padding:8px;">
        <p><strong>模式:</strong> ${arch.pattern || '未指定'}</p>
        ${(arch.key_design_decisions || []).map((d, i) => `<p style="font-size:0.82rem;margin-top:4px;">🔑 ADR-${i+1}: ${Utils.escapeHtml(typeof d === 'string' ? d : d.title || JSON.stringify(d).substring(0, 80))}</p>`).join('') || ''}
      </div>
    </div>`;

    // Technology stack
    html += `<div class="card">
      <div class="card-header"><span class="card-title">🛠 技术栈</span></div>
      <div style="padding:8px;line-height:2.2;font-size:0.85rem;">`;
    if (typeof techStack === 'object' && Object.keys(techStack).length) {
      for (const [k, v] of Object.entries(techStack)) {
        const val = typeof v === 'object' ? (v.name || v.value || JSON.stringify(v).substring(0, 40)) : String(v);
        html += `<div>🔹 <strong>${k}:</strong> ${Utils.escapeHtml(val)}</div>`;
      }
    } else {
      html += '<p style="color:var(--text-muted);">技术栈数据未生成</p>';
    }
    html += '</div></div>';

    // Diagram summary
    html += `<div class="card">
      <div class="card-header"><span class="card-title">📊 产出图表</span></div>
      <div style="padding:8px;line-height:2.2;font-size:0.85rem;">
        <div>📊 类图: ${diagramTypes.has('class') ? '✅' : '—'}</div>
        <div>🗄 ER 图: ${diagramTypes.has('er') ? '✅' : '—'}</div>
        <div>⏱ 时序图: ${diagramTypes.has('sequence') ? '✅' : '—'}</div>
        <div>🔄 状态机: ${diagramTypes.has('state') ? '✅' : '—'}</div>
        <div>🔀 流程图: ${diagramTypes.has('flowchart') ? '✅' : '—'}</div>
        <div>🧩 组件图: ${diagramTypes.has('component') ? '✅' : '—'}</div>
        <div>☸️ 部署图: ${diagramTypes.has('deployment') ? '✅' : '—'}</div>
        <div>📦 总计: ${diagrams.length} 个图表</div>
      </div>
    </div>`;

    // Modules
    if (modules.length) {
      html += `<div class="card" style="grid-column:1/-1;">
        <div class="card-header"><span class="card-title">📦 模块划分 (${modules.length})</span></div>
        <div class="table-container">
          <table><thead><tr><th>模块</th><th>职责</th><th>依赖</th></tr></thead><tbody>
            ${modules.map(m => `<tr>
              <td><strong>${Utils.escapeHtml(m.name)}</strong></td>
              <td style="font-size:0.82rem;">${Utils.escapeHtml(m.responsibility || m.description || '')}</td>
              <td style="font-size:0.78rem;">${(m.dependencies || []).map(d => `<code>${Utils.escapeHtml(d)}</code>`).join(' ') || '—'}</td>
            </tr>`).join('')}
          </tbody></table>
        </div>
      </div>`;
    }

    html += '</div>';
    el.innerHTML = html;
  },

  _downloadDoc() {
    const blob = new Blob([this._docContent], { type: 'text/markdown' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'design_specification.md';
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
  },

  _fullscreenDoc() {
    const overlay = document.createElement('div');
    overlay.style.cssText = 'position:fixed;inset:0;z-index:9999;background:var(--bg-primary);padding:32px;overflow:auto;';
    overlay.innerHTML = `
      <button style="position:fixed;top:16px;right:16px;z-index:1;font-size:24px;border:none;background:none;cursor:pointer;color:var(--text-primary);" onclick="this.parentElement.remove()">✕</button>
      <div style="max-width:900px;margin:0 auto;line-height:1.8;font-size:0.9rem;">${Utils.markdownToHtml(this._docContent)}</div>`;
    document.body.appendChild(overlay);
  },

  _downloadDiagramSVG(containerId, name) {
    Diagrams._dl(document.getElementById(containerId), name);
  },
};
