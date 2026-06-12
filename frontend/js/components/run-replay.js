/* ============================================================
   DevAgent Frontend — Run Replay
   Replays agent execution step-by-step using PersistenceStore events.
   ============================================================ */

App.registerView('run-replay', (container) => {
  const taskId = App.state.activeTaskId;

  container.innerHTML = `
    <div class="mb-16 flex items-center justify-between">
      <div><h2>⏪ 执行回放</h2><span class="view-breadcrumb">Agent 操作时间线回放</span></div>
      <div class="flex gap-8">
        ${taskId ? `<code>${taskId}</code>` : ''}
        <button class="btn btn-primary" onclick="RunReplay.load()">🔄 加载</button>
        <button class="btn" onclick="RunReplay._togglePlay()" id="rr-play-btn">▶️ 播放</button>
      </div>
    </div>

    <div class="card mb-16">
      <div class="card-header"><span class="card-title">📊 执行概览</span></div>
      <div class="metrics-grid" id="rr-summary">
        <div class="metric-card accent-blue"><div class="metric-value" id="rr-total">—</div><div class="metric-label">总步数</div></div>
        <div class="metric-card accent-green"><div class="metric-value" id="rr-tools">—</div><div class="metric-label">工具调用</div></div>
        <div class="metric-card accent-orange"><div class="metric-value" id="rr-reviews">—</div><div class="metric-label">审核次数</div></div>
        <div class="metric-card accent-purple"><div class="metric-value" id="rr-errors">—</div><div class="metric-label">错误次数</div></div>
      </div>
    </div>

    <div class="card">
      <div class="card-header"><span class="card-title">⏱️ 事件时间线</span></div>
      <div id="rr-timeline" style="max-height:65vh;overflow-y:auto;padding:16px;">
        <div class="empty-state">
          <div class="icon">⏪</div><h3>执行回放</h3>
          <p>选择一个已完成的任务，点击加载查看 Agent 操作回放</p>
        </div>
      </div>
    </div>
  `;

  if (taskId) RunReplay.load();
});

const RunReplay = {
  events: [],
  playing: false,
  playIdx: 0,
  _timer: null,

  async load() {
    const taskId = App.state.activeTaskId;
    if (!taskId) {
      document.getElementById('rr-timeline').innerHTML =
        '<div class="empty-state"><p>请先选择一个任务</p></div>';
      return;
    }

    document.getElementById('rr-timeline').innerHTML =
      '<div class="empty-state"><span class="spinner"></span> 加载事件数据...</div>';

    try {
      // Primary: get events from PersistenceStore API
      const resp = await fetch(`${API.baseURL}/api/v2/tasks/${taskId}/events`)
        .then(r => r.json()).catch(() => ({ events: [] }));

      this.events = resp.events || [];

      // Fallback: if no events in DB, generate synthetic timeline from task data
      if (!this.events.length) {
        await this._buildSyntheticTimeline(taskId);
      }

      this._renderSummary();
      this._renderTimeline();
    } catch (e) {
      document.getElementById('rr-timeline').innerHTML =
        `<div class="empty-state"><p>加载失败: ${Utils.escapeHtml(e.message)}</p></div>`;
    }
  },

  async _buildSyntheticTimeline(taskId) {
    const events = [];
    let seq = 0;
    const now = () => new Date().toISOString();

    // Task creation
    events.push({ event_type: 'TASK_CREATED', payload: { task_id: taskId }, sequence_no: seq++, created_at: now() });

    // Load phase data to build a timeline
    try {
      const phases = await API.getTaskPhases(taskId);
      const phaseOrder = ['requirements', 'design', 'implementation', 'tests', 'repair', 'reports'];
      const phaseIcons = { requirements: '📋', design: '🏗️', implementation: '💻', tests: '🧪', repair: '🔧', reports: '📦' };

      for (const pname of phaseOrder) {
        const phase = (phases.phases || []).find(p => p.name === pname);
        if (phase) {
          events.push({
            event_type: 'PHASE_COMPLETED',
            payload: { phase: pname, file_count: phase.file_count, size_kb: phase.size_kb },
            sequence_no: seq++, created_at: now(),
          });
          events.push({
            event_type: 'TOOL_CALLED',
            payload: { tool: `${pname}_agent`, result: `${phase.file_count} files generated` },
            sequence_no: seq++, created_at: now(),
          });
        }
      }

      const task = App.state.activeTask;
      if (task) {
        events.push({
          event_type: 'TASK_FINISHED',
          payload: { status: task.status, iterations: task.iterations || 0 },
          sequence_no: seq++, created_at: now(),
        });
      }
    } catch(e) { /* phase data unavailable */ }

    this.events = events;
  },

  _renderSummary() {
    const total = this.events.length;
    const tools = this.events.filter(e => e.event_type === 'TOOL_CALLED' || e.payload?.tool).length;
    const reviews = this.events.filter(e => e.event_type?.includes('REVIEW')).length;
    const errors = this.events.filter(e => e.event_type === 'ERROR' || e.payload?.error).length;
    document.getElementById('rr-total').textContent = total;
    document.getElementById('rr-tools').textContent = tools;
    document.getElementById('rr-reviews').textContent = reviews;
    document.getElementById('rr-errors').textContent = errors;
  },

  _renderTimeline() {
    const el = document.getElementById('rr-timeline');
    if (!this.events.length) {
      el.innerHTML = '<div class="empty-state"><p>暂无事件数据</p></div>';
      return;
    }

    const icons = {
      TASK_CREATED: '🚀', TASK_FINISHED: '✅', PHASE_COMPLETED: '📌',
      TOOL_CALLED: '🔧', TOOL_COMPLETED: '✅', THINKING: '🧠',
      REVIEW_REQUESTED: '📋', REVIEW_APPROVED: '✅', REVIEW_REVISED: '📝',
      ERROR: '❌', TEST_RESULTS: '🧪', PROGRESS: '📊',
    };

    el.innerHTML = this.events.map((e, i) => {
      const type = e.event_type || 'UNKNOWN';
      const icon = icons[type] || '📎';
      const payload = typeof e.payload === 'string' ? JSON.parse(e.payload || '{}') : (e.payload || {});
      const time = e.created_at ? Utils.formatTime(e.created_at) : `#${e.sequence_no || i}`;

      let detail = '';
      if (type === 'TOOL_CALLED' || type === 'TOOL_COMPLETED') {
        detail = `🔧 ${payload.tool || ''} ${payload.result || ''}`;
      } else if (type === 'PHASE_COMPLETED') {
        detail = `📌 阶段: ${payload.phase || ''} — ${payload.file_count || 0} 文件`;
      } else if (type === 'REVIEW_REQUESTED' || type === 'REVIEW_APPROVED') {
        detail = `📋 ${payload.phase || ''} — ${payload.decision || ''}`;
      } else if (type === 'ERROR') {
        detail = `❌ ${(payload.error || payload.message || '').substring(0, 100)}`;
      } else if (type === 'TASK_FINISHED') {
        detail = `✅ 状态: ${payload.status || ''} — ${payload.iterations || 0} 迭代`;
      } else {
        detail = JSON.stringify(payload).substring(0, 100);
      }

      return `<div class="timeline-event ${type.toLowerCase().includes('error') ? 'error' : type.toLowerCase().includes('completed') || type.toLowerCase().includes('finished') ? 'success' : 'info'}">
        <div class="ts">⏱ ${time}</div>
        <div>${icon} <strong>${type}</strong></div>
        <div style="font-size:0.78rem;color:var(--text-muted);margin-top:2px;">${Utils.escapeHtml(detail)}</div>
      </div>`;
    }).join('');
  },

  _togglePlay() {
    const btn = document.getElementById('rr-play-btn');
    if (this.playing) {
      this.playing = false;
      if (this._timer) { clearInterval(this._timer); this._timer = null; }
      if (btn) btn.textContent = '▶️ 播放';
      return;
    }
    this.playing = true;
    this.playIdx = 0;
    if (btn) btn.textContent = '⏸️ 暂停';
    this._playStep();
  },

  _playStep() {
    if (!this.playing) return;
    const items = document.querySelectorAll('#rr-timeline .timeline-event');
    items.forEach((el, i) => {
      el.style.opacity = i <= this.playIdx ? '1' : '0.3';
      el.style.transform = i <= this.playIdx ? 'translateX(0)' : 'translateX(10px)';
      el.style.transition = '0.3s ease';
    });
    if (this.playIdx < items.length - 1) {
      this.playIdx++;
      this._timer = setTimeout(() => this._playStep(), 500);
    } else {
      this.playing = false;
      const btn = document.getElementById('rr-play-btn');
      if (btn) btn.textContent = '▶️ 播放';
    }
  },
};
