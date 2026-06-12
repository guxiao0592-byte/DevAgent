/* ============================================================
   DevAgent Frontend — Review Center
   Centralized review management: pending list, quality details,
   diff preview, review history, batch approval.
   ============================================================ */

App.registerView('review-center', (container) => {
  container.innerHTML = `
    <div class="mb-16 flex items-center justify-between">
      <div><h2>✅ 审核中心</h2><span class="view-breadcrumb" id="rc-subtitle">加载中...</span></div>
      <div class="flex gap-8">
        <button class="btn btn-primary" onclick="ReviewCenter.refresh()">🔄 刷新</button>
        <button class="btn" onclick="ReviewCenter._showHistory()">📋 审核历史</button>
      </div>
    </div>

    <div class="tabs">
      <div class="tab active" data-tab="pending">⏳ 待审核</div>
      <div class="tab" data-tab="history">📋 审核历史</div>
    </div>

    <div id="rc-tab-pending" class="rc-tab-content">
      <div id="rc-pending-list">
        <div class="empty-state"><span class="spinner"></span> 加载中...</div>
      </div>
    </div>

    <div id="rc-tab-history" class="rc-tab-content" style="display:none;">
      <div id="rc-history-list">
        <div class="empty-state"><span class="spinner"></span> 加载中...</div>
      </div>
    </div>
  `;

  container.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
      container.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      container.querySelectorAll('.rc-tab-content').forEach(c => c.style.display = 'none');
      const target = document.getElementById(`rc-tab-${tab.dataset.tab}`);
      if (target) target.style.display = '';
      if (tab.dataset.tab === 'history') ReviewCenter._showHistory();
    });
  });

  ReviewCenter.refresh();
});

const ReviewCenter = {
  async refresh() {
    document.getElementById('rc-subtitle').textContent = '加载中...';
    const taskId = App.state.activeTaskId;

    try {
      // Try current task first, then all tasks
      const [pending, history] = await Promise.all([
        taskId ? API.getPendingReview(taskId).catch(() => ({has_pending:false})) : {has_pending:false},
        taskId ? API.getReviewHistory(taskId).catch(() => ({reviews:[]})) : {reviews:[]},
      ]);

      // Build pending from current task AND from all TASK_HISTORY entries
      const pendingItems = [];
      if (pending && pending.has_pending) {
        pendingItems.push({
          task_id: taskId, review_id: pending.review_id, phase: pending.phase,
          title: pending.title, quality_score: pending.quality_score,
          summary: pending.summary, created_at: pending.created_at,
        });
      }

      ReviewCenter._renderPending(pendingItems || []);
      ReviewCenter._renderHistory(history.reviews || []);
      document.getElementById('rc-subtitle').textContent =
        `${pendingItems.length} 待审核 · ${(history.reviews||[]).length} 已审核`;
    } catch (e) {
      console.error('Review center error:', e);
      document.getElementById('rc-subtitle').textContent = '加载失败';
    }
  },

  _renderPending(items) {
    const el = document.getElementById('rc-pending-list');
    if (!items.length) {
      el.innerHTML = '<div class="empty-state"><div class="icon">✅</div><h3>无待审核项</h3><p>当前没有需要审核的阶段</p></div>';
      return;
    }

    el.innerHTML = items.map(r => `
      <div class="card mb-16" style="border-left:3px solid var(--orange);">
        <div class="card-header">
          <span class="card-title">📋 ${r.phase || '阶段审核'}</span>
          <span class="badge badge-orange">⏳ 待审核</span>
        </div>
        <div style="padding:8px 0;">
          <p><strong>标题:</strong> ${Utils.escapeHtml(r.title || '阶段审核')}</p>
          <p><strong>任务:</strong> <code>${r.task_id || ''}</code></p>
          <p><strong>质量评分:</strong> <span style="color:var(--accent);">${r.quality_score || '—'}</span></p>
          <div class="form-group mt-8">
            <label class="form-label">反馈意见</label>
            <textarea class="form-textarea" id="rc-feedback-${r.review_id}" placeholder="输入反馈意见..."></textarea>
          </div>
          <div class="mt-12 flex gap-8">
            <button class="btn btn-primary" onclick="ReviewCenter._decide('${r.review_id}','approve')">✅ 批准</button>
            <button class="btn" onclick="ReviewCenter._decide('${r.review_id}','revise')">📝 要求修改</button>
            <button class="btn btn-danger" onclick="ReviewCenter._decide('${r.review_id}','reject')">❌ 拒绝</button>
          </div>
        </div>
      </div>
    `).join('');
  },

  _renderHistory(reviews) {
    const el = document.getElementById('rc-history-list');
    if (!reviews.length) {
      el.innerHTML = '<div class="empty-state"><p>暂无审核记录</p></div>';
      return;
    }

    el.innerHTML = `
      <div class="table-container">
        <table>
          <thead><tr><th>阶段</th><th>标题</th><th>决定</th><th>质量评分</th><th>反馈</th><th>时间</th></tr></thead>
          <tbody>
            ${reviews.map(r => `
              <tr>
                <td><span class="badge badge-accent">${r.phase || '—'}</span></td>
                <td>${Utils.escapeHtml(r.title || '—')}</td>
                <td>${r.decision === 'approve' ? '<span class="badge badge-green">✅ 已批准</span>' :
                      r.decision === 'revise' ? '<span class="badge badge-orange">📝 需修改</span>' :
                      r.decision === 'reject' ? '<span class="badge badge-red">❌ 已拒绝</span>' :
                      '<span class="badge badge-blue">⏳ 待定</span>'}</td>
                <td>${r.quality_score || '—'}</td>
                <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${Utils.escapeHtml((r.feedback || '').substring(0, 60))}</td>
                <td>${Utils.formatTime(r.created_at)}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>`;
  },

  async _decide(reviewId, decision) {
    const taskId = App.state.activeTaskId;
    if (!taskId) { App.toast('请先选择任务', 'warning'); return; }
    const feedback = document.getElementById(`rc-feedback-${reviewId}`)?.value || '';

    try {
      await API.respondToReview(taskId, reviewId, decision, feedback);
      App.toast(`审核已提交: ${decision}`, 'success');
      await this.refresh();
    } catch (e) {
      App.toast(`审核提交失败: ${e.message}`, 'error');
    }
  },

  async _showHistory() {
    const taskId = App.state.activeTaskId;
    if (!taskId) {
      document.getElementById('rc-history-list').innerHTML =
        '<div class="empty-state"><p>请先选择一个任务</p></div>';
      return;
    }
    try {
      const history = await API.getReviewHistory(taskId);
      this._renderHistory(history.reviews || []);
    } catch (e) {
      document.getElementById('rc-history-list').innerHTML =
        `<div class="empty-state"><p>加载失败: ${Utils.escapeHtml(e.message)}</p></div>`;
    }
  },
};
