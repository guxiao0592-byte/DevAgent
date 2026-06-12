/* ============================================================
   DevAgent Frontend — Utility Functions
   ============================================================ */

const Utils = {
  /**
   * Escape HTML to prevent XSS.
   */
  escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  },

  /**
   * Format a date/time string for display.
   */
  formatTime(isoString) {
    if (!isoString) return '—';
    const d = new Date(isoString);
    const now = new Date();
    const diff = now - d;
    if (diff < 60000) return '刚刚';
    if (diff < 3600000) return `${Math.floor(diff / 60000)} 分钟前`;
    if (diff < 86400000) return `${Math.floor(diff / 3600000)} 小时前`;
    return d.toLocaleString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  },

  /**
   * Format seconds to human readable.
   */
  formatDuration(sec) {
    if (!sec || sec < 0) return '—';
    if (sec < 60) return `${sec.toFixed(1)}s`;
    if (sec < 3600) return `${Math.floor(sec / 60)}m ${Math.floor(sec % 60)}s`;
    return `${Math.floor(sec / 3600)}h ${Math.floor((sec % 3600) / 60)}m`;
  },

  /**
   * Format a number with commas.
   */
  formatNumber(n) {
    if (n == null) return '—';
    return n.toLocaleString();
  },

  /**
   * Format percentage.
   */
  formatPercent(n) {
    if (n == null) return '—';
    return `${(n * 100).toFixed(1)}%`;
  },

  /**
   * Generate a short unique ID.
   */
  shortId() {
    return Math.random().toString(36).substring(2, 10);
  },

  /**
   * Truncate a string.
   */
  truncate(str, len = 100) {
    if (!str) return '';
    return str.length > len ? str.substring(0, len) + '...' : str;
  },

  /**
   * Copy text to clipboard.
   */
  async copyToClipboard(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      return true;
    }
  },

  /**
   * Debounce a function.
   */
  debounce(fn, ms = 300) {
    let timer;
    return (...args) => {
      clearTimeout(timer);
      timer = setTimeout(() => fn(...args), ms);
    };
  },

  /**
   * Simple markdown to HTML renderer (covers common cases).
   */
  markdownToHtml(md) {
    if (!md) return '';
    let html = md;
    // Code blocks
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code class="language-$1">$2</code></pre>');
    // Inline code
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
    // Bold
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    // Italic
    html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');
    // Headers
    html = html.replace(/^#### (.+)$/gm, '<h4>$1</h4>');
    html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
    html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
    html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>');
    // Unordered lists
    html = html.replace(/^- (.+)$/gm, '<li>$1</li>');
    html = html.replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>');
    // Ordered lists
    html = html.replace(/^\d+\. (.+)$/gm, '<li>$1</li>');
    // Links
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank">$1</a>');
    // Line breaks
    html = html.replace(/\n\n/g, '</p><p>');
    html = html.replace(/\n/g, '<br>');
    html = '<p>' + html + '</p>';
    return html;
  },

  /**
   * Simple syntax highlighting for code.
   */
  highlightCode(code, lang = '') {
    if (!code) return '';
    let html = Utils.escapeHtml(code);

    // Keywords (Python + general)
    const kw = '\\b(def|class|return|if|else|elif|for|while|import|from|try|except|raise|with|as|pass|yield|lambda|async|await|and|or|not|in|is|None|True|False|self|break|continue|finally|assert|global|nonlocal)\\b';
    html = html.replace(new RegExp(kw, 'g'), '<span class="kw">$1</span>');

    // Strings
    html = html.replace(/(["'`])(?:(?!\1)[^\\]|\\.)*?\1/g, '<span class="str">$&</span>');
    html = html.replace(/(""")[\s\S]*?(""")/g, '<span class="str">$&</span>');

    // Numbers
    html = html.replace(/\b(\d+\.?\d*)\b/g, '<span class="num">$1</span>');

    // Comments
    html = html.replace(/(#.*)$/gm, '<span class="cm">$1</span>');

    // Function names
    html = html.replace(/\bdef (\w+)/g, 'def <span class="fn">$1</span>');
    html = html.replace(/\bclass (\w+)/g, 'class <span class="tp">$1</span>');

    // Decorators
    html = html.replace(/(@\w+)/g, '<span class="fn">$1</span>');

    return html;
  },

  /**
   * Get status badge HTML.
   */
  statusBadge(status) {
    const map = {
      'RUNNING': 'badge-blue',
      'COMPLETED': 'badge-green',
      'FINISHED': 'badge-green',
      'FAILED': 'badge-red',
      'ABORTED': 'badge-orange',
      'STUCK': 'badge-orange',
      'PENDING': 'badge-purple',
      'PAUSED': 'badge-cyan',
    };
    const cls = map[status] || 'badge-accent';
    const label = {
      'RUNNING': '运行中',
      'COMPLETED': '已完成',
      'FINISHED': '已完成',
      'FAILED': '失败',
      'ABORTED': '已中止',
      'STUCK': '卡住',
      'PENDING': '等待中',
      'PAUSED': '已暂停',
    }[status] || status;
    return `<span class="badge ${cls}">${label}</span>`;
  },

  /**
   * Get event icon by type.
   */
  eventIcon(type) {
    const map = {
      'thinking': '🧠', 'THINKING': '🧠',
      'tool': '🔧', 'TOOL_COMPLETED': '🔧',
      'error': '❌', 'ERROR': '❌',
      'test': '🧪', 'TEST_RESULTS': '🧪',
      'review': '📋', 'review.requested': '📋',
      'approval': '✋', 'approval.requested': '✋',
      'phase': '📌', 'phase.completed': '📌',
      'question': '❓', 'agent.question': '❓',
      'session': '🔗', 'session.created': '🔗',
      'progress': '📊', 'progress.snapshot': '📊',
      'complete': '✅', 'task.completed': '✅',
    };
    return map[type] || '📎';
  },

  /**
   * Get event CSS class by type.
   */
  eventClass(type) {
    if (/thinking|THINKING/i.test(type)) return 'thinking';
    if (/tool|TOOL/i.test(type)) return 'tool';
    if (/error|ERROR/i.test(type)) return 'error';
    if (/test|TEST/i.test(type)) return 'success';
    if (/review/i.test(type)) return 'review';
    if (/success|complete/i.test(type)) return 'success';
    return 'info';
  },

  /**
   * Render a simple trend sparkline (SVG).
   */
  renderTrendLine(containerId, data, options = {}) {
    const container = document.getElementById(containerId);
    if (!container || !data || data.length < 2) return;

    const width = options.width || container.offsetWidth || 200;
    const height = options.height || 60;
    const padding = options.padding || 4;
    const color = options.color || '#00d4aa';
    const fillOpacity = options.fillOpacity || 0.15;

    const w = width - padding * 2;
    const h = height - padding * 2;
    const max = Math.max(...data);
    const min = Math.min(...data);
    const range = max - min || 1;

    const points = data.map((v, i) => {
      const x = padding + (i / (data.length - 1)) * w;
      const y = padding + h - ((v - min) / range) * h;
      return `${x},${y}`;
    });

    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('width', width);
    svg.setAttribute('height', height);
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    svg.style.display = 'block';

    // Fill area
    const area = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
    area.setAttribute('points', `${padding},${height - padding} ${points.join(' ')} ${width - padding},${height - padding}`);
    area.setAttribute('fill', color);
    area.setAttribute('opacity', fillOpacity);
    svg.appendChild(area);

    // Line
    const line = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
    line.setAttribute('points', points.join(' '));
    line.setAttribute('fill', 'none');
    line.setAttribute('stroke', color);
    line.setAttribute('stroke-width', '2');
    line.setAttribute('stroke-linecap', 'round');
    line.setAttribute('stroke-linejoin', 'round');
    svg.appendChild(line);

    container.innerHTML = '';
    container.appendChild(svg);
  },
};

// Export for ES module usage
if (typeof module !== 'undefined' && module.exports) {
  module.exports = Utils;
}
