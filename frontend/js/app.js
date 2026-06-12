/* ============================================================
   DevAgent Frontend — Application Core
   SPA Router, State Management, Global Event Handling
   ============================================================ */

const App = {
  // --- State ---
  state: {
    currentView: 'dashboard',
    activeTaskId: null,
    activeTask: null,
    tasks: [],
    metrics: {},
    connected: false,
    sidebarOpen: false,
    interactiveMode: 'controller',
    fsmState: 'idle',         // FSM-driven
    reviewPending: false,     // FSM-driven
    agentQuestion: null,      // FSM-driven
  },

  // --- FSM helpers ---
  _updateFSM() {
    const prev = this.state.fsmState;
    this.state.fsmState = TASK_FSM.derive(
      this.state.activeTask,
      this.state.reviewPending,
      this.state.agentQuestion
    );
    if (prev !== this.state.fsmState) {
      this._emit('fsm-changed', { from: prev, to: this.state.fsmState });
    }
  },

  get fsm() { return TASK_FSM; },
  get fsmState() { return this.state.fsmState; },
  get fsmUI() { return TASK_FSM.getUI(this.state.fsmState); },
  canCommand(cmd) { return TASK_FSM.canExecute(this.state.fsmState, cmd); },

  // --- View registry ---
  views: {},
  wsClient: null,

  // --- Initialize ---
  init() {
    API.init();
    this.wsClient = new WSClient();
    this._bindSidebar();
    this._bindGlobalEvents();
    this.navigate(this.state.currentView);
    this._startPolling();
    this._checkHealth();
  },

  // --- View Registration ---
  registerView(name, renderFn) {
    this.views[name] = renderFn;
  },

  // --- Navigation ---
  navigate(viewName, params = {}) {
    const isRevisit = viewName === this.state.currentView;
    if (isRevisit && !params.force) return;

    this.state.currentView = viewName;
    Object.assign(this.state, params);

    // Update sidebar
    document.querySelectorAll('.nav-item').forEach(el => {
      el.classList.toggle('active', el.dataset.view === viewName);
    });

    // Update topbar
    const titles = {
      dashboard: '仪表盘', 'task-create': '创建任务', pipeline: '流水线执行',
      requirements: '需求分析', 'high-design': '总体设计', 'detail-design': '详细设计',
      implementation: '代码实现', testing: '测试执行', 'debug-repair': '调试与修复',
      interaction: '实时交互', reports: '报告文档', 'review-center': '审核中心', 'run-replay': '执行回放',
    };
    const titleEl = document.getElementById('view-title');
    if (titleEl) titleEl.textContent = titles[viewName] || viewName;

    // Render view
    const contentArea = document.getElementById('content-area');
    if (contentArea && this.views[viewName]) {
      contentArea.innerHTML = '';
      try { this.views[viewName](contentArea); } catch(e) {
        contentArea.innerHTML = `<div class="empty-state"><h3>视图加载失败</h3><p>${Utils.escapeHtml(e.message)}</p></div>`;
        console.error('View render error:', viewName, e);
      }
    }

    // Update breadcrumb
    const bcEl = document.getElementById('view-breadcrumb');
    if (bcEl) bcEl.textContent = this.state.activeTaskId ? `任务: ${this.state.activeTaskId}` : 'DevAgent';
  },

  // --- Task Management ---
  setActiveTask(taskId) {
    if (this.state.activeTaskId === taskId) return;
    // Immediately update state so all async loads use the right task
    this.state.activeTaskId = taskId;
    this.state.activeTask = null;  // clear stale data
    // Update breadcrumb immediately
    const bcEl = document.getElementById('view-breadcrumb');
    if (bcEl) bcEl.textContent = `任务: ${taskId}`;
    // Fetch detail and re-render
    this._fetchTaskDetail(taskId).then(() => {
      this.navigate(this.state.currentView, { force: true });
    }).catch(() => {
      this.navigate(this.state.currentView, { force: true });
    });
  },

  async _fetchTaskDetail(taskId) {
    try {
      const task = await API.getTaskStatus(taskId);
      this.state.activeTask = task;
      this._updateFSM();
      this._emit('task-updated', task);
    } catch (e) {
      console.error('Failed to fetch task detail:', e);
      this.state.fsmState = 'idle';
    }
  },

  // --- Event System ---
  _listeners: {},

  on(event, cb) {
    if (!this._listeners[event]) this._listeners[event] = [];
    this._listeners[event].push(cb);
  },

  off(event, cb) {
    if (!this._listeners[event]) return;
    this._listeners[event] = this._listeners[event].filter(c => c !== cb);
  },

  _emit(event, data) {
    (this._listeners[event] || []).forEach(cb => {
      try { cb(data); } catch (e) { console.error(`App event error [${event}]:`, e); }
    });
  },

  // --- Polling ---
  _startPolling() {
    // Poll dashboard metrics every 10s
    setInterval(() => this._refreshMetrics(), 10000);
    // Poll task status if active (skip if FSM says no auto-refresh needed)
    setInterval(() => {
      if (this.state.activeTaskId) {
        const needsRefresh = TASK_FSM.needsAutoRefresh(this.state.fsmState) || !this.state.activeTask;
        if (needsRefresh) {
          this._fetchTaskDetail(this.state.activeTaskId);
        }
      }
    }, 5000);
    // Poll for pending reviews
    setInterval(() => this._checkPendingReview(), 4000);
  },

  async _checkPendingReview() {
    if (!this.state.activeTaskId) return;
    try {
      const pending = await API.getPendingReview(this.state.activeTaskId);
      const hasPending = pending && (pending.has_pending || pending.review_id);
      if (this.state.reviewPending !== hasPending) {
        this.state.reviewPending = hasPending;
        this._updateFSM();
        if (hasPending) {
          this._emit('review-pending-changed', pending);
        }
      }
    } catch(e) { /* silent */ }
  },

  async _refreshMetrics() {
    try {
      const data = await API.getDashboardMetrics();
      this.state.metrics = data;
      this._emit('metrics-updated', data);
    } catch (e) {
      // Silently fail
    }
  },

  async _checkHealth() {
    try {
      const health = await API.checkHealth();
      this.state.connected = health.status === 'ok';
      this._updateConnectionStatus();
      this._emit('health-checked', health);
    } catch {
      this.state.connected = false;
      this._updateConnectionStatus();
    }
  },

  _updateConnectionStatus() {
    const dot = document.getElementById('status-dot');
    if (dot) {
      dot.className = 'status-dot';
      if (this.state.connected) {
        dot.classList.add('connected');
      } else {
        dot.classList.add('disconnected');
      }
    }
    const text = document.getElementById('status-text');
    if (text) {
      text.textContent = this.state.connected ? 'API 已连接' : 'API 未连接';
    }
  },

  // --- Sidebar ---
  _bindSidebar() {
    document.querySelectorAll('.nav-item').forEach(el => {
      el.addEventListener('click', () => {
        const view = el.dataset.view;
        if (view) this.navigate(view);
      });
    });

    // Mobile menu toggle
    const menuBtn = document.getElementById('mobile-menu-btn');
    if (menuBtn) {
      menuBtn.addEventListener('click', () => {
        this.state.sidebarOpen = !this.state.sidebarOpen;
        document.getElementById('sidebar').classList.toggle('open', this.state.sidebarOpen);
      });
    }
  },

  _bindGlobalEvents() {
    // Keyboard shortcuts
    document.addEventListener('keydown', (e) => {
      if (e.ctrlKey || e.metaKey) {
        switch (e.key) {
          case '1': e.preventDefault(); this.navigate('dashboard'); break;
          case '2': e.preventDefault(); this.navigate('task-create'); break;
          case '3': e.preventDefault(); this.navigate('pipeline'); break;
        }
      }
    });
  },

  // --- Toast Notifications ---
  toast(message, type = 'info', duration = 4000) {
    const container = document.getElementById('toast-container') || this._createToastContainer();
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    const icons = { success: '✅', error: '❌', info: 'ℹ️', warning: '⚠️' };
    toast.innerHTML = `<span>${icons[type] || ''}</span> ${Utils.escapeHtml(message)}`;
    container.appendChild(toast);
    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateX(100%)';
      toast.style.transition = '0.3s ease';
      setTimeout(() => toast.remove(), 300);
    }, duration);
  },

  _createToastContainer() {
    const container = document.createElement('div');
    container.id = 'toast-container';
    container.className = 'toast-container';
    document.body.appendChild(container);
    return container;
  },

  // --- Modal ---
  showModal(title, bodyHtml, footerHtml = '') {
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    overlay.innerHTML = `
      <div class="modal">
        <div class="modal-header">
          <span class="modal-title">${Utils.escapeHtml(title)}</span>
          <button class="modal-close" onclick="this.closest('.modal-overlay').remove()">✕</button>
        </div>
        <div class="modal-body">${bodyHtml}</div>
        ${footerHtml ? `<div class="modal-footer">${footerHtml}</div>` : ''}
      </div>`;
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) overlay.remove();
    });
    document.body.appendChild(overlay);
    return overlay;
  },
};

// Initialize on DOM ready
document.addEventListener('DOMContentLoaded', () => App.init());
