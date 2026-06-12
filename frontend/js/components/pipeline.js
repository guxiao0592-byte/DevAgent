/* ============================================================
   DevAgent Frontend — Pipeline Execution View
   Progressive, accurate phase tracking via output artifacts.
   ============================================================ */

App.registerView('pipeline', (container) => {
  const taskId = App.state.activeTaskId;
  const task = App.state.activeTask;

  container.innerHTML = `
    <!-- Pipeline Progress -->
    <div class="card mb-20">
      <div class="card-header">
        <span class="card-title">🔄 流水线进度</span>
        <div class="flex gap-8">
          ${taskId ? `<code>${taskId}</code>` : ''}
          <span class="badge" id="pipeline-status-badge">—</span>
          <button class="btn btn-sm" onclick="Pipeline.refresh()">🔄 刷新</button>
        </div>
      </div>
      <div style="padding: 20px 40px;">
        <div class="progress-steps" id="pipeline-steps">
          <div class="progress-step" data-phase="requirements">
            <span class="dot">1</span><span class="line"></span>
            <span class="step-label">需求分析</span>
          </div>
          <div class="progress-step" data-phase="design">
            <span class="dot">2</span><span class="line"></span>
            <span class="step-label">架构设计</span>
          </div>
          <div class="progress-step" data-phase="implementation">
            <span class="dot">3</span><span class="line"></span>
            <span class="step-label">代码生成</span>
          </div>
          <div class="progress-step" data-phase="testing">
            <span class="dot">4</span><span class="line"></span>
            <span class="step-label">测试执行</span>
          </div>
          <div class="progress-step" data-phase="repair">
            <span class="dot">5</span><span class="line"></span>
            <span class="step-label">修复验证</span>
          </div>
          <div class="progress-step" data-phase="delivery">
            <span class="dot">6</span>
            <span class="step-label">交付完成</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Task Detail -->
    <div class="card">
      <div class="card-header">
        <span class="card-title">📊 任务详情</span>
        <div class="flex gap-8">
          <button class="btn" onclick="App.navigate('requirements')">📋 需求分析</button>
          <button class="btn" onclick="App.navigate('high-design')">🏗️ 总体设计</button>
          <button class="btn" onclick="App.navigate('detail-design')">📐 详细设计</button>
          <button class="btn" onclick="App.navigate('implementation')">💻 代码</button>
          <button class="btn" onclick="App.navigate('testing')">🧪 测试</button>
          <button class="btn" onclick="App.navigate('interaction')">💬 实时交互</button>
        </div>
      </div>
      <div id="pipeline-detail">
        ${taskId ? '<div class="empty-state"><span class="spinner"></span> 加载中...</div>' : '<div class="empty-state"><div class="icon">📋</div><h3>无活跃任务</h3><p>创建新任务或从仪表盘选择一个任务来查看流水线</p><button class="btn btn-primary mt-12" onclick="App.navigate(\'task-create\')">➕ 创建任务</button></div>'}
      </div>
    </div>

    <!-- Phase Results -->
    <div id="phase-results" class="mt-20"></div>
  `;

  if (taskId) Pipeline.refresh();
});

const Pipeline = {
  _pollTimer: null,

  refresh() {
    const taskId = App.state.activeTaskId;
    if (!taskId) return;

    // Fetch task detail + phases in parallel
    Promise.all([
      App._fetchTaskDetail(taskId),
      API.getTaskPhases(taskId).catch(() => ({ phases: [] })),
    ]).then(([_, phaseData]) => {
      Pipeline._updateProgress(App.state.activeTask, phaseData.phases);
      Pipeline._renderDetail(App.state.activeTask, phaseData.phases);
      Pipeline._renderPhases(phaseData.phases, taskId);
    });

    // Auto-refresh if task is running
    const task = App.state.activeTask;
    if (task && (task.status === 'RUNNING' || task.status === 'PENDING')) {
      if (!this._pollTimer) {
        this._pollTimer = setInterval(() => {
          const t = App.state.activeTask;
          if (!t || (t.status !== 'RUNNING' && t.status !== 'PENDING')) {
            clearInterval(this._pollTimer);
            this._pollTimer = null;
            Pipeline.refresh(); // final refresh
            return;
          }
          Pipeline.refresh();
        }, 3000);
      }
    } else {
      if (this._pollTimer) {
        clearInterval(this._pollTimer);
        this._pollTimer = null;
      }
    }
  },

  /**
   * Determine real progress from two sources:
   * 1. phaseData (from filesystem — most reliable)
   * 2. task status string (fallback)
   */
  _updateProgress(task, phases) {
    const phaseOrder = ['requirements', 'design', 'implementation', 'testing', 'repair', 'delivery'];
    const status = (task && task.status) ? (task.status || '').toString().toUpperCase() : '';

    // Progress from filesystem (most reliable)
    let doneIdx = -1;
    const seen = new Set((phases || []).map(p => p.name));
    if (seen.has('reports'))   doneIdx = 5;
    else if (seen.has('repair')) doneIdx = 4;
    else if (seen.has('tests'))  doneIdx = 3;
    else if (seen.has('implementation')) doneIdx = 2;
    else if (seen.has('design'))  doneIdx = 1;
    else if (seen.has('requirements')) doneIdx = 0;

    // Fallback: task status and phase_index
    const phaseIdx = task?.phase_index;
    if (doneIdx < 0 && typeof phaseIdx === 'number' && phaseIdx >= 0) {
      doneIdx = Math.max(-1, phaseIdx - 1); // phase_index = current phase; doneIdx = last completed
    }

    const isFinished = status === 'COMPLETED' || status === 'FINISHED';
    const isFailed = status === 'FAILED';

    // Update badge
    const badge = document.getElementById('pipeline-status-badge');
    if (badge) {
      const labels = { RUNNING:'运行中', COMPLETED:'已完成', FINISHED:'已完成', FAILED:'失败', PENDING:'等待中', ABORTED:'已中止' };
      badge.className = `badge ${isFinished ? 'badge-green' : isFailed ? 'badge-red' : status==='RUNNING' ? 'badge-blue' : 'badge-accent'}`;
      badge.textContent = labels[status] || status || '—';
    }

    // Render steps
    document.querySelectorAll('#pipeline-steps .progress-step').forEach(el => {
      const idx = phaseOrder.indexOf(el.dataset.phase);
      el.classList.remove('done', 'active', 'failed');
      if (isFinished) { el.classList.add('done'); return; }
      if (isFailed && idx === doneIdx + 1) { el.classList.add('failed'); return; }
      if (idx <= doneIdx) el.classList.add('done');
      else if (status === 'RUNNING' && idx === doneIdx + 1) el.classList.add('active');
    });
  },

  _renderDetail(task, phases) {
    const container = document.getElementById('pipeline-detail');
    if (!container) return;
    if (!task) {
      container.innerHTML = '<div class="empty-state"><p>无任务数据</p></div>';
      return;
    }

    const testPassed = task.test_passed || task.metrics?.test_passed || '—';
    const testFailed = task.test_failed || task.metrics?.test_failed || '—';
    const filesMod = (task.files_modified || task.metrics?.files_modified || []);
    const fileCount = Array.isArray(filesMod) ? filesMod.length : (filesMod || 0);

    // Phase summary from directories
    const phaseSummary = (phases || []).map(p => {
      const icons = { requirements: '📋', design: '🏗️', implementation: '💻', tests: '🧪', repair: '🔧', reports: '📦' };
      const labels = { requirements: '需求分析', design: '架构设计', implementation: '代码实现', tests: '测试执行', repair: '修复验证', reports: '最终报告' };
      return `<span style="display:inline-flex;align-items:center;gap:4px;font-size:0.82rem;">
        ${icons[p.name] || '📄'} <strong>${labels[p.name] || p.name}</strong>: ${p.file_count} 文件
      </span>`;
    }).join('&nbsp;&nbsp;|&nbsp;&nbsp;') || '<span style="color:var(--text-muted);">等待产出...</span>';

    container.innerHTML = `
      <div class="grid-3 mt-8">
        <div>
          <div class="form-label">状态</div>
          <div>${Utils.statusBadge(task.status)}</div>
        </div>
        <div>
          <div class="form-label">类型</div>
          <div>${task.task_type || '—'}</div>
        </div>
        <div>
          <div class="form-label">迭代次数</div>
          <div><strong>${task.iterations || task.metrics?.iterations || '—'}</strong></div>
        </div>
        <div>
          <div class="form-label">测试通过</div>
          <div style="color:var(--green);"><strong>${testPassed}</strong></div>
        </div>
        <div>
          <div class="form-label">测试失败</div>
          <div style="color:var(--red);"><strong>${testFailed}</strong></div>
        </div>
        <div>
          <div class="form-label">修改文件数</div>
          <div><strong>${fileCount}</strong></div>
        </div>
        <div style="grid-column:1/-1;">
          <div class="form-label">阶段产出</div>
          <div class="mt-4">${phaseSummary}</div>
        </div>
        ${task.errors && task.errors.length ? `
        <div style="grid-column:1/-1;">
          <div class="form-label">错误 (${task.errors.length})</div>
          <div class="json-viewer mt-8">${Utils.escapeHtml(JSON.stringify(task.errors, null, 2))}</div>
        </div>` : ''}
      </div>`;
  },

  _renderPhases(phases, taskId) {
    const container = document.getElementById('phase-results');
    if (!container) return;

    if (!phases || !phases.length) {
      container.innerHTML = `
        <div class="card">
          <div class="card-header"><span class="card-title">📊 执行进度</span></div>
          <div class="empty-state" style="padding:40px;">
            <div class="icon">⏳</div>
            <h3>任务执行中...</h3>
            <p>阶段产物将在各阶段完成后出现</p>
            <div class="mt-12"><span class="spinner"></span></div>
          </div>
        </div>`;
      return;
    }

    let html = '';

    // Phase overview cards
    const phaseLabels = {
      requirements: { icon: '📋', label: '需求分析', desc: '领域模型、功能需求、用例图、DFD 数据流图、活动图' },
      design: { icon: '🏗️', label: '架构设计', desc: '系统架构图、类图、时序图、状态机图、ER 图、部署图' },
      implementation: { icon: '💻', label: '代码实现', desc: '源代码文件、类型标注、docstring、项目脚手架' },
      tests: { icon: '🧪', label: '测试执行', desc: 'pytest 测试套件、覆盖率报告、lint 检查结果' },
      repair: { icon: '🔧', label: '修复验证', desc: '故障定位结果、修复补丁、回归验证' },
      reports: { icon: '📦', label: '最终报告', desc: '执行摘要、质量仪表盘、产物清单' },
    };

    html += '<div class="grid-3 mb-16">';
    for (const p of phases) {
      const info = phaseLabels[p.name] || { icon: '📄', label: p.name, desc: '' };
      html += `
        <div class="card" style="border-left:3px solid var(--green);">
          <div style="font-size:1.4rem;margin-bottom:4px;">${info.icon}</div>
          <div style="font-weight:700;font-size:0.9rem;">${info.label}</div>
          <div style="font-size:0.78rem;color:var(--text-muted);margin-top:2px;">${info.desc}</div>
          <div style="margin-top:8px;display:flex;align-items:center;gap:8px;">
            <span class="badge badge-green">✅ 已完成</span>
            <span style="font-size:0.78rem;color:var(--text-muted);">${p.file_count} 文件 · ${p.size_kb} KB</span>
          </div>
          <div class="mt-8">
            <a class="btn btn-sm" href="${API.getPhaseDownloadUrl(taskId, p.name)}" target="_blank">📥 下载</a>
            <button class="btn btn-sm" onclick="App.navigate('${p.name === 'implementation' ? 'implementation' : p.name === 'tests' ? 'testing' : p.name === 'repair' ? 'debug-repair' : p.name === 'reports' ? 'reports' : p.name === 'design' ? 'high-design' : 'requirements'}')">
              👁️ 查看
            </button>
          </div>
        </div>`;
    }
    html += '</div>';

    // Overall download
    html += `<div class="card">
      <div class="card-header">
        <span class="card-title">📥 下载全部产物</span>
      </div>
      <div style="padding:8px;">
        <div class="download-row mb-12">
          <a class="btn btn-primary" href="${API.getDownloadUrl(taskId)}" target="_blank">
            📦 下载完整项目 ZIP
          </a>
        </div>
        <div class="form-label mb-8">分阶段下载：</div>
        <div class="download-row">
          ${phases.map(p => {
            const shortLabels = { requirements: '📋 需求', design: '🏗️ 设计', implementation: '💻 代码', tests: '🧪 测试', repair: '🔧 修复', reports: '📦 报告' };
            return `<a class="btn btn-sm" href="${API.getPhaseDownloadUrl(taskId, p.name)}" target="_blank">
              ${shortLabels[p.name] || p.name} (${p.file_count}文件 · ${p.size_kb}KB)
            </a>`;
          }).join('')}
        </div>
      </div>
    </div>`;

    container.innerHTML = html;
  },
};
