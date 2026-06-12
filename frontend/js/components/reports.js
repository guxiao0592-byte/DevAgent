/* ============================================================
   DevAgent Frontend — Reports & Documentation View
   Fetches real reports and phase data from backend.
   ============================================================ */

App.registerView('reports', (container) => {
  container.innerHTML = `
    <div class="mb-16 flex items-center justify-between">
      <div>
        <h2>📦 报告文档</h2>
        <span class="view-breadcrumb" id="reports-subtitle">加载中...</span>
      </div>
      <div class="flex gap-8">
        <button class="btn" onclick="ReportsView.load()">🔄 刷新</button>
      </div>
    </div>

    <div class="tabs">
      <div class="tab active" data-tab="doc">📄 执行报告</div>
      <div class="tab" data-tab="downloads">📥 下载产物</div>
      <div class="tab" data-tab="artifacts">📋 产物清单</div>
    </div>

    <div id="rp-tab-doc" class="rp-tab-content">
      <div class="card">
        <div class="card-header">
          <span class="card-title">📊 最终执行报告</span>
          <div class="flex gap-8">
            <button class="btn btn-sm" id="rp-dl-doc">📥 下载</button>
            <button class="btn btn-sm" id="rp-fs-doc">⛶ 全屏</button>
          </div>
        </div>
        <div id="reports-doc-content" style="padding:20px;line-height:1.8;font-size:0.9rem;max-height:70vh;overflow-y:auto;">
          <div class="empty-state"><span class="spinner"></span> 加载报告...</div>
        </div>
      </div>
    </div>

    <div id="rp-tab-downloads" class="rp-tab-content" style="display:none;">
      <div id="reports-downloads-content">
        <div class="empty-state"><span class="spinner"></span> 加载...</div>
      </div>
    </div>

    <div id="rp-tab-artifacts" class="rp-tab-content" style="display:none;">
      <div id="reports-artifacts-content">
        <div class="empty-state"><span class="spinner"></span> 加载...</div>
      </div>
    </div>
  `;

  container.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
      container.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      container.querySelectorAll('.rp-tab-content').forEach(c => c.style.display = 'none');
      const target = document.getElementById(`rp-tab-${tab.dataset.tab}`);
      if (target) target.style.display = '';
    });
  });

  ReportsView.load();
});

const ReportsView = {
  _docContent: '',

  async load() {
    const taskId = App.state.activeTaskId;
    if (!taskId) { this._showEmpty(); return; }

    try {
      const [doc, phases] = await Promise.all([
        API.getPhaseDocument(taskId, 'reports').catch(() => null),
        API.getTaskPhases(taskId).catch(() => ({ phases: [] })),
      ]);

      document.getElementById('reports-subtitle').textContent =
        doc ? doc.filename : (phases.phases?.length ? `${phases.phases.length} 个阶段有产出` : '无数据');

      this._renderDoc(doc);
      this._renderDownloads(phases.phases || [], taskId);
      this._renderArtifacts(phases.phases || [], taskId);
    } catch (e) {
      console.error('Reports load error:', e);
      this._showEmpty();
    }
  },

  _showEmpty() {
    document.getElementById('reports-doc-content').innerHTML =
      '<div class="empty-state"><p>无报告数据。请选择一个已完成的任务。</p></div>';
    document.getElementById('reports-subtitle').textContent = '无数据';
  },

  _renderDoc(doc) {
    const el = document.getElementById('reports-doc-content');
    if (doc && doc.content) {
      this._docContent = doc.content;
      el.innerHTML = Utils.markdownToHtml(doc.content);
    } else {
      el.innerHTML = '<div class="empty-state"><p>最终报告尚未生成</p></div>';
    }

    document.getElementById('rp-dl-doc').onclick = () => {
      const blob = new Blob([this._docContent], { type: 'text/markdown' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob); a.download = 'final_report.md';
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
    };
    document.getElementById('rp-fs-doc').onclick = () => {
      const overlay = document.createElement('div');
      overlay.style.cssText = 'position:fixed;inset:0;z-index:9999;background:var(--bg-primary);padding:32px;overflow:auto;';
      overlay.innerHTML = `<button style="position:fixed;top:16px;right:16px;z-index:1;font-size:24px;border:none;background:none;cursor:pointer;color:var(--text-primary);" onclick="this.parentElement.remove()">✕</button>
        <div style="max-width:900px;margin:0 auto;line-height:1.8;font-size:0.9rem;">${Utils.markdownToHtml(this._docContent)}</div>`;
      document.body.appendChild(overlay);
    };
  },

  _renderDownloads(phases, taskId) {
    const el = document.getElementById('reports-downloads-content');
    if (!phases.length) {
      el.innerHTML = '<div class="empty-state"><p>无产出</p></div>';
      return;
    }

    const phaseLabels = {
      requirements: { icon: '📋', label: '需求分析', desc: 'IEEE 830 SRS, 领域模型, 用例图, DFD' },
      design: { icon: '🏗️', label: '架构设计', desc: 'IEEE 1016 SDD, 类图, 时序图, ER图' },
      implementation: { icon: '💻', label: '代码实现', desc: '源代码, 项目脚手架' },
      tests: { icon: '🧪', label: '测试套件', desc: 'pytest 测试, 覆盖率报告' },
      repair: { icon: '🔧', label: '修复', desc: '修复补丁, 回归验证' },
      reports: { icon: '📦', label: '最终报告', desc: '执行摘要, 质量仪表盘' },
    };

    el.innerHTML = `
      <div class="card mb-16">
        <div class="card-header"><span class="card-title">📦 整体下载</span></div>
        <div style="padding:12px;">
          <a class="btn btn-primary btn-lg" href="${API.getDownloadUrl(taskId)}" target="_blank">📦 下载完整项目 ZIP</a>
        </div>
      </div>
      <div class="grid-3">
        ${phases.filter(p => phaseLabels[p.name]).map(p => {
          const info = phaseLabels[p.name];
          return `<div class="card" style="border-left:3px solid var(--accent);">
            <div style="font-size:1.6rem;">${info.icon}</div>
            <div style="font-weight:700;font-size:0.9rem;margin:4px 0;">${info.label}</div>
            <div style="font-size:0.78rem;color:var(--text-muted);margin-bottom:8px;">${info.desc}</div>
            <div class="flex gap-8 items-center">
              <span class="badge badge-accent">${p.file_count} 文件</span>
              <span class="badge badge-blue">${p.size_kb} KB</span>
            </div>
            <div class="mt-8">
              <a class="btn btn-sm btn-primary" href="${API.getPhaseDownloadUrl(taskId, p.name)}" target="_blank">📥 下载</a>
              <button class="btn btn-sm" onclick="App.navigate('${p.name === 'reports' ? 'reports' : p.name === 'design' ? 'high-design' : p.name === 'tests' ? 'testing' : p.name === 'repair' ? 'debug-repair' : p.name}')">👁️ 查看</button>
            </div>
          </div>`;
        }).join('')}
      </div>`;
  },

  _renderArtifacts(phases, taskId) {
    const el = document.getElementById('reports-artifacts-content');
    if (!phases.length) {
      el.innerHTML = '<div class="empty-state"><p>无产物</p></div>';
      return;
    }

    const standardPhases = phases.filter(p =>
      ['requirements','design','implementation','tests','repair','reports'].includes(p.name)
    );

    el.innerHTML = standardPhases.map(p => `
      <div class="card mb-16">
        <div class="card-header">
          <span class="card-title">${p.name === 'requirements' ? '📋 需求分析' :
            p.name === 'design' ? '🏗️ 架构设计' : p.name === 'implementation' ? '💻 代码实现' :
            p.name === 'tests' ? '🧪 测试' : p.name === 'repair' ? '🔧 修复' : '📦 报告'}
            — ${p.directory}</span>
          <a class="btn btn-sm" href="${API.getPhaseDownloadUrl(taskId, p.name)}" target="_blank">📥 下载</a>
        </div>
        <div style="padding:8px 16px;">
          <div class="table-container">
            <table>
              <thead><tr><th>文件名</th><th>类型</th></tr></thead>
              <tbody>
                ${p.files.map(f => {
                  const ext = f.split('.').pop();
                  const icons = { py: '🐍', md: '📝', json: '📊', yml: '⚙️', yaml: '⚙️', mmd: '📊', puml: '🌿', txt: '📄', diff: '🔧' };
                  return `<tr><td><code>${f}</code></td><td>${icons[ext] || '📄'} .${ext}</td></tr>`;
                }).join('')}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    `).join('');
  },
};
