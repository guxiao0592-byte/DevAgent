/* ============================================================
   DevAgent Frontend — Dashboard Component
   ============================================================ */

App.registerView('dashboard', (container) => {
  container.innerHTML = `
    <!-- Metrics Row -->
    <div class="metrics-grid" id="dashboard-metrics">
      <div class="metric-card accent-green">
        <div class="metric-value" id="met-total">—</div>
        <div class="metric-label">总任务数</div>
        <div class="metric-change up" id="met-total-change"></div>
      </div>
      <div class="metric-card accent-blue">
        <div class="metric-value" id="met-success">—</div>
        <div class="metric-label">成功率</div>
        <div class="metric-change" id="met-success-change"></div>
      </div>
      <div class="metric-card accent-purple">
        <div class="metric-value" id="met-active">—</div>
        <div class="metric-label">活跃任务</div>
      </div>
      <div class="metric-card accent-orange">
        <div class="metric-value" id="met-avg-iter">—</div>
        <div class="metric-label">平均迭代次数</div>
      </div>
      <div class="metric-card accent-cyan">
        <div class="metric-value" id="met-avg-dur">—</div>
        <div class="metric-label">平均耗时</div>
      </div>
    </div>

    <!-- Quick Actions -->
    <div class="card mb-20">
      <div class="card-header">
        <span class="card-title">🚀 快速启动</span>
      </div>
      <div style="display:flex;gap:12px;flex-wrap:wrap;">
        <button class="btn btn-primary btn-lg" onclick="App.navigate('task-create')">
          ➕ 新建任务
        </button>
        <button class="btn btn-lg" onclick="App.navigate('pipeline')">
          📋 查看流水线
        </button>
        <button class="btn btn-lg" onclick="App.navigate('interaction')">
          💬 实时交互
        </button>
      </div>
    </div>

    <!-- Task History -->
    <div class="grid-2 mt-20">
      <div class="card">
        <div class="card-header">
          <span class="card-title">📋 最近任务</span>
          <button class="btn btn-sm" onclick="Dashboard.refresh()">🔄 刷新</button>
        </div>
        <div id="recent-tasks-table" style="max-height:400px;overflow-y:auto;">
          <div class="empty-state"><div class="icon">📭</div><p>加载中...</p></div>
        </div>
      </div>
      <div class="card">
        <div class="card-header">
          <span class="card-title">📊 系统状态</span>
        </div>
        <div id="system-status">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px;">
            <span class="status-dot" id="sys-dot"></span>
            <span id="sys-api-status">检查中...</span>
          </div>
          <div id="sys-info" style="font-size:0.82rem;color:var(--text-muted);"></div>
        </div>
      </div>
    </div>
  `;

  // Initial load
  Dashboard.refresh();
});

const Dashboard = {
  async refresh() {
    try {
      // Load metrics
      const metrics = await API.getDashboardMetrics();
      App.state.metrics = metrics;
      Dashboard._updateMetrics(metrics);
    } catch (e) {
      console.error('Dashboard metrics error:', e);
    }

    try {
      // Load task history
      const history = await API.getTaskHistory();
      Dashboard._renderTaskList(history.tasks || []);
    } catch (e) {
      console.error('Dashboard history error:', e);
    }

    try {
      // Health check
      const health = await API.checkHealth();
      const dot = document.getElementById('sys-dot');
      const status = document.getElementById('sys-api-status');
      const info = document.getElementById('sys-info');
      if (dot) dot.className = 'status-dot ' + (health.status === 'ok' ? '' : 'disconnected');
      if (status) status.textContent = health.status === 'ok' ? 'API 服务正常' : 'API 服务不可达';
      if (info) {
        info.innerHTML = `
          服务: DevAgent API v1.0.0<br>
          地址: ${location.host}<br>
          时间: ${new Date().toLocaleString('zh-CN')}
        `;
      }
    } catch {
      const dot = document.getElementById('sys-dot');
      if (dot) dot.className = 'status-dot disconnected';
      const status = document.getElementById('sys-api-status');
      if (status) status.textContent = 'API 服务不可达 — 请检查后端是否启动';
    }
  },

  _updateMetrics(data) {
    const total = data.total_tasks || data.total || 0;
    const successRate = data.success_rate || 0;
    const active = data.active_tasks || 0;
    const avgIter = data.avg_iterations || 0;
    const avgDur = data.avg_duration_sec || 0;

    const setVal = (id, val, fmt) => {
      const el = document.getElementById(id);
      if (el) el.textContent = typeof fmt === 'function' ? fmt(val) : val;
    };

    setVal('met-total', total, Utils.formatNumber);
    setVal('met-success', successRate, v => (v * 100).toFixed(1) + '%');
    setVal('met-active', active, Utils.formatNumber);
    setVal('met-avg-iter', avgIter, v => v.toFixed(1));
    setVal('met-avg-dur', avgDur, Utils.formatDuration);
  },

  _renderTaskList(tasks) {
    const container = document.getElementById('recent-tasks-table');
    if (!container) return;

    if (!tasks.length) {
      container.innerHTML = '<div class="empty-state"><div class="icon">📭</div><h3>暂无任务</h3><p>创建一个新任务开始使用 DevAgent</p></div>';
      return;
    }

    const rows = tasks.slice(0, 20).map(t => `
      <tr style="cursor:pointer;" onclick="App.setActiveTask('${t.task_id}');App.navigate('pipeline');">
        <td><code style="font-size:0.75rem;">${t.task_id}</code></td>
        <td>${t.task_type || 'agentic'}</td>
        <td>${Utils.statusBadge(t.status)}</td>
        <td>${t.iterations || 0}</td>
        <td>${Utils.formatDuration(t.duration_sec)}</td>
        <td>${Utils.formatTime(t.created_at)}</td>
      </tr>
    `).join('');

    container.innerHTML = `
      <div class="table-container">
        <table>
          <thead><tr><th>任务ID</th><th>类型</th><th>状态</th><th>迭代</th><th>耗时</th><th>时间</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  },
};
