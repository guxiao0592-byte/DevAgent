/* ============================================================
   DevAgent Frontend — Real-time Interaction Panel
   ============================================================ */

App.registerView('interaction', (container) => {
  container.innerHTML = `
    <div class="mb-16 flex items-center justify-between">
      <div>
        <h2>💬 实时交互</h2>
        <span class="view-breadcrumb">WebSocket · 事件流 · 审核 · 命令</span>
      </div>
      <div class="flex gap-8">
        <button class="btn" id="int-connect-btn" onclick="Interaction.toggleConnection()">
          🔗 连接
        </button>
        <button class="btn btn-sm" onclick="Interaction.clearEvents()">🗑️ 清空</button>
      </div>
    </div>

    <!-- Connection Bar -->
    <div class="card mb-16">
      <div style="display:flex;align-items:center;gap:12px;">
        <span class="status-dot" id="int-ws-dot"></span>
        <span id="int-ws-status">未连接</span>
        <span style="flex:1;"></span>
        <span style="font-size:0.82rem;color:var(--text-muted);" id="int-task-display">
          ${App.state.activeTaskId ? `任务: ${App.state.activeTaskId}` : '无活跃任务'}
        </span>
        <div class="flex gap-8">
          <button class="btn btn-sm" id="btn-pause" onclick="Interaction.sendCmd('pause')" disabled>⏸️ 暂停</button>
          <button class="btn btn-sm" id="btn-resume" onclick="Interaction.sendCmd('resume')" disabled>▶️ 恢复</button>
          <button class="btn btn-sm btn-danger" id="btn-abort" onclick="Interaction.sendCmd('abort')" disabled>⏹️ 中止</button>
        </div>
      </div>
    </div>

    <div class="grid-2">
      <!-- Event Timeline -->
      <div class="card">
        <div class="card-header">
          <span class="card-title">📋 事件时间线</span>
          <span id="int-event-count" style="font-size:0.8rem;color:var(--text-muted);">0 事件</span>
        </div>
        <div id="int-timeline" style="max-height:500px;overflow-y:auto;min-height:300px;">
          <div class="empty-state">
            <div class="icon">📡</div>
            <h3>连接 WebSocket 以接收实时事件</h3>
            <p>输入任务 ID 并点击连接</p>
          </div>
        </div>
      </div>

      <!-- Review & Command Panel -->
      <div>
        <!-- Pending Review -->
        <div class="card mb-16" id="int-review-card">
          <div class="card-header"><span class="card-title">📋 待审核项</span></div>
          <div id="int-review-content">
            <div class="empty-state"><p>暂无待审核项</p></div>
          </div>
        </div>

        <!-- Command Console -->
        <div class="card">
          <div class="card-header"><span class="card-title">⌨️ 命令控制台</span></div>
          <div class="form-group">
            <label class="form-label">任务 ID（留空使用当前活跃任务）</label>
            <input class="form-input" id="int-task-id" placeholder="task_xxxxxxxx" value="${App.state.activeTaskId || ''}">
          </div>
          <div style="display:flex;flex-wrap:wrap;gap:8px;">
            <button class="btn" onclick="Interaction.sendRestCmd('pause')">⏸️ 暂停</button>
            <button class="btn" onclick="Interaction.sendRestCmd('resume')">▶️ 恢复</button>
            <button class="btn btn-danger" onclick="Interaction.sendRestCmd('abort')">⏹️ 中止</button>
            <button class="btn" onclick="Interaction.sendRestCmd('retry')">🔄 重试</button>
          </div>
          <div class="mt-12" style="display:flex;gap:8px;">
            <input class="form-input flex-1" id="int-inject-text" placeholder="注入上下文 / 提示语">
            <button class="btn" onclick="Interaction.injectContext()">💉 注入</button>
          </div>
        </div>
      </div>
    </div>
  `;

  Interaction._updateConnectionUI();
});

const Interaction = {
  events: [],

  toggleConnection() {
    if (App.wsClient.connected) {
      App.wsClient.disconnect();
      this._updateConnectionUI();
      return;
    }

    const taskId = document.getElementById('int-task-id')?.value?.trim() || App.state.activeTaskId;
    if (!taskId) {
      App.toast('请先输入任务 ID 或选择一个活跃任务', 'warning');
      return;
    }

    App.wsClient.connect(taskId, 'controller');
    this._bindWSEvents();
    this._updateConnectionUI();
  },

  _bindWSEvents() {
    const ws = App.wsClient;

    ws.on('connected', (data) => {
      this.addEvent('session', 'WebSocket 已连接', data);
      App.toast('WebSocket 已连接', 'success');
      this._updateConnectionUI();
    });

    ws.on('disconnected', (data) => {
      this.addEvent('info', 'WebSocket 已断开', data);
      this._updateConnectionUI();
    });

    ws.on('reconnecting', (data) => {
      this.addEvent('info', `正在重连... (${data.attempt}/${App.wsClient.maxReconnectAttempts})`, data);
      this._updateConnectionUI();
    });

    ws.on('error', (data) => {
      this.addEvent('error', `WebSocket 错误: ${data.message}`, data);
      App.toast('WebSocket 错误: ' + data.message, 'error');
    });

    ws.on('progress.snapshot', (data) => {
      this.addEvent('tool', '进度快照', data);
    });

    ws.on('review.requested', (data) => {
      this.addEvent('review', '审核请求', data);
      this._showReviewRequest(data);
      App.toast('收到审核请求', 'info');
    });

    ws.on('agent.question', (data) => {
      this.addEvent('info', 'Agent 提问', data);
      this._showAgentQuestion(data);
    });

    ws.on('approval.requested', (data) => {
      this.addEvent('review', '审批请求', data);
      this._showApprovalRequest(data);
    });

    ws.on('tool.completed', (data) => {
      this.addEvent('tool', `工具完成: ${data.tool || data.data?.tool || ''}`, data);
    });

    ws.on('phase.completed', (data) => {
      this.addEvent('success', `阶段完成: ${data.phase || data.data?.phase || ''}`, data);
    });

    ws.on('task.completed', (data) => {
      this.addEvent('success', '任务完成 ✅', data);
      App.toast('任务已完成', 'success');
    });

    ws.on('session.created', (data) => {
      this.addEvent('session', '会话已创建', data);
    });

    ws.on('pong', () => {});
    ws.on('heartbeat', () => {});
    ws.on('echo', () => {});

    ws.on('message', (msg) => {
      if (!msg.type || ['pong', 'heartbeat', 'echo'].includes(msg.type)) return;
      const knownTypes = ['connected', 'disconnected', 'reconnecting', 'error', 'progress.snapshot',
        'review.requested', 'agent.question', 'approval.requested', 'tool.completed',
        'phase.completed', 'task.completed', 'session.created'];
      if (!knownTypes.includes(msg.type)) {
        this.addEvent('info', msg.type, msg);
      }
    });
  },

  addEvent(type, description, data = {}) {
    this.events.unshift({ type, description, data, time: new Date() });
    if (this.events.length > 200) this.events.pop();
    this._renderTimeline();
    this._updateConnectionUI();
  },

  _renderTimeline() {
    const container = document.getElementById('int-timeline');
    const count = document.getElementById('int-event-count');
    if (!container) return;
    if (count) count.textContent = `${this.events.length} 事件`;

    if (!this.events.length) {
      container.innerHTML = '<div class="empty-state"><div class="icon">📡</div><h3>等待事件...</h3></div>';
      return;
    }

    container.innerHTML = this.events.slice(0, 100).map(e => `
      <div class="timeline-event ${Utils.eventClass(e.type)}">
        <div class="ts">${Utils.formatTime(e.time)}</div>
        <div>${Utils.eventIcon(e.type)} <strong>${Utils.escapeHtml(e.description)}</strong></div>
        ${e.data && Object.keys(e.data).length ? `<div style="font-size:0.75rem;color:var(--text-muted);margin-top:4px;">
          ${Utils.escapeHtml(JSON.stringify(e.data).substring(0, 200))}
        </div>` : ''}
      </div>
    `).join('');
  },

  _updateConnectionUI() {
    const dot = document.getElementById('int-ws-dot');
    const status = document.getElementById('int-ws-status');
    const connectBtn = document.getElementById('int-connect-btn');
    const pauseBtn = document.getElementById('btn-pause');
    const resumeBtn = document.getElementById('btn-resume');
    const abortBtn = document.getElementById('btn-abort');

    if (dot) {
      dot.className = 'status-dot';
      if (App.wsClient.connected) dot.classList.add('connected');
      else if (App.wsClient.reconnectTimer) dot.classList.add('connecting');
      else dot.classList.add('disconnected');
    }

    if (status) {
      status.textContent = App.wsClient.connected ? '已连接' :
        App.wsClient.reconnectTimer ? '重连中...' : '未连接';
    }

    if (connectBtn) {
      connectBtn.textContent = App.wsClient.connected ? '🔌 断开' : '🔗 连接';
    }

    const connected = App.wsClient.connected;
    if (pauseBtn) pauseBtn.disabled = !connected;
    if (resumeBtn) resumeBtn.disabled = !connected;
    if (abortBtn) abortBtn.disabled = !connected;
  },

  sendCmd(command) {
    if (!App.wsClient.connected) {
      App.toast('请先连接 WebSocket', 'warning');
      return;
    }
    App.wsClient.sendCommand(command);
    this.addEvent('tool', `发送命令: ${command}`);
  },

  async sendRestCmd(command) {
    const taskId = document.getElementById('int-task-id')?.value?.trim() || App.state.activeTaskId;
    if (!taskId) {
      App.toast('请输入任务 ID', 'warning');
      return;
    }
    try {
      await API.sendCommand(taskId, command);
      this.addEvent('tool', `REST 命令: ${command} → ${taskId}`);
      App.toast(`命令已发送: ${command}`, 'success');
    } catch (e) {
      App.toast(`命令发送失败: ${e.message}`, 'error');
    }
  },

  async injectContext() {
    const taskId = document.getElementById('int-task-id')?.value?.trim() || App.state.activeTaskId;
    const text = document.getElementById('int-inject-text')?.value?.trim();
    if (!taskId || !text) {
      App.toast('请输入任务 ID 和上下文内容', 'warning');
      return;
    }
    try {
      await API.sendCommand(taskId, 'inject', { context: text });
      this.addEvent('tool', `注入上下文 → ${taskId}: ${Utils.truncate(text, 80)}`);
      App.toast('上下文已注入', 'success');
      document.getElementById('int-inject-text').value = '';
    } catch (e) {
      App.toast(`注入失败: ${e.message}`, 'error');
    }
  },

  clearEvents() {
    this.events = [];
    this._renderTimeline();
  },

  _showReviewRequest(data) {
    const reviewData = data.data || data;
    const reviewId = reviewData.review_id || data.review_id || '';
    const phase = reviewData.phase || '';
    const title = reviewData.title || '';
    const qualityScore = reviewData.quality_score || 'unknown';

    const footer = `
      <button class="btn btn-primary" onclick="Interaction._respondReview('${reviewId}', 'approve')">✅ 批准</button>
      <button class="btn" onclick="Interaction._respondReview('${reviewId}', 'revise')">📝 要求修改</button>
      <button class="btn btn-danger" onclick="Interaction._respondReview('${reviewId}', 'reject')">❌ 拒绝</button>`;

    App.showModal(`审核请求 — ${phase}`, `
      <p><strong>阶段:</strong> ${Utils.escapeHtml(phase)}</p>
      <p><strong>标题:</strong> ${Utils.escapeHtml(title)}</p>
      <p><strong>质量评分:</strong> ${Utils.escapeHtml(String(qualityScore))}</p>
      <div class="form-group mt-12">
        <label class="form-label">反馈意见</label>
        <textarea class="form-textarea" id="review-feedback" placeholder="输入反馈意见（修改时必填）"></textarea>
      </div>
    `, footer);

    // Also update the review card
    const content = document.getElementById('int-review-content');
    if (content) {
      content.innerHTML = `
        <div style="padding:8px;">
          <p><strong>阶段:</strong> ${Utils.escapeHtml(phase)}</p>
          <p><strong>质量评分:</strong> ${Utils.escapeHtml(String(qualityScore))}</p>
          <div class="mt-8 flex gap-8">
            <button class="btn btn-sm btn-primary" onclick="Interaction._respondReview('${reviewId}', 'approve')">批准</button>
            <button class="btn btn-sm" onclick="Interaction._respondReview('${reviewId}', 'revise')">修改</button>
            <button class="btn btn-sm btn-danger" onclick="Interaction._respondReview('${reviewId}', 'reject')">拒绝</button>
          </div>
        </div>`;
    }
  },

  _showAgentQuestion(data) {
    const qData = data.data || data;
    App.showModal('Agent 提问', `
      <p>${Utils.escapeHtml(qData.question || qData.message || '')}</p>
      <div class="form-group mt-12">
        <textarea class="form-textarea" id="question-answer" placeholder="输入回答..."></textarea>
      </div>`, `
      <button class="btn btn-primary" onclick="Interaction._answerQuestion('${qData.question_id || ''}')">📤 回复</button>`);
  },

  _showApprovalRequest(data) {
    const aData = data.data || data;
    App.showModal('审批请求', `
      <p><strong>操作:</strong> ${Utils.escapeHtml(aData.action || aData.operation || '')}</p>
      <p><strong>详情:</strong> ${Utils.escapeHtml(aData.details || aData.description || '')}</p>`, `
      <button class="btn btn-primary" onclick="App.wsClient.sendApprovalResponse('${aData.approval_id || ''}', 'approve')">✅ 批准</button>
      <button class="btn btn-danger" onclick="App.wsClient.sendApprovalResponse('${aData.approval_id || ''}', 'deny')">❌ 拒绝</button>`);
  },

  _respondReview(reviewId, decision) {
    const feedback = document.getElementById('review-feedback')?.value || '';
    if (App.wsClient.connected) {
      App.wsClient.sendReviewResponse(reviewId, decision, feedback);
    } else {
      const taskId = App.state.activeTaskId;
      if (taskId) {
        API.respondToReview(taskId, reviewId, decision, feedback).then(() => {
          App.toast(`审核已响应: ${decision}`, 'success');
        }).catch(e => App.toast(`审核响应失败: ${e.message}`, 'error'));
      }
    }
    // Close modal
    const overlay = document.querySelector('.modal-overlay');
    if (overlay) overlay.remove();
  },

  _answerQuestion(questionId) {
    const answer = document.getElementById('question-answer')?.value || '';
    if (App.wsClient.connected) {
      App.wsClient.sendQuestionResponse(questionId, answer);
    }
    const overlay = document.querySelector('.modal-overlay');
    if (overlay) overlay.remove();
  },
};
