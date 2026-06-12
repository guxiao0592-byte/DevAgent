/* ============================================================
   DevAgent Frontend — API Client (REST + WebSocket)
   ============================================================ */

const API = {
  baseURL: '',  // Same origin by default
  wsBaseURL: '',

  /**
   * Initialize the API client.
   */
  init(baseURL) {
    if (baseURL) {
      this.baseURL = baseURL;
    }
    // Derive WS URL from current location
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = location.host;
    this.wsBaseURL = `${proto}//${host}`;
  },

  /**
   * Make a REST API request.
   */
  async request(method, path, body = null) {
    const url = `${this.baseURL}${path}`;
    const opts = {
      method,
      headers: { 'Content-Type': 'application/json' },
    };
    if (body && method !== 'GET') {
      opts.body = JSON.stringify(body);
    }
    const resp = await fetch(url, opts);
    if (!resp.ok) {
      const errText = await resp.text().catch(() => 'Unknown error');
      throw new Error(`API ${method} ${path} failed (${resp.status}): ${errText}`);
    }
    return resp.json();
  },

  get(path) { return this.request('GET', path); },
  post(path, body) { return this.request('POST', path, body); },

  // --- Task APIs ---

  /** Get all task history */
  getTaskHistory() {
    return this.get('/api/v1/tasks/history');
  },

  /** Get a specific task's status */
  getTaskStatus(taskId) {
    return this.get(`/api/v1/tasks/${taskId}`);
  },

  /** Create an agentic task (fires background agent, returns immediately) */
  createAgenticTask({ description, workspace = '.', output = './outputs' }) {
    return this.post('/api/v2/tasks/agentic', {
      task: 'agentic',
      input: description,
      code: workspace,
      output,
    });
  },

  /** Create a full pipeline task */
  createFullTask({ input, output = './outputs', maxRetry = 2 }) {
    const params = new URLSearchParams({ input, output, max_retry: maxRetry });
    return this.post(`/api/v1/tasks/full?${params}`);
  },

  /** Create a design-only task */
  createDesignTask({ input, output = './outputs' }) {
    const params = new URLSearchParams({ input, output });
    return this.post(`/api/v1/tasks/design?${params}`);
  },

  /** Create an implement task */
  createImplementTask({ input, output = './outputs' }) {
    const params = new URLSearchParams({ input, output });
    return this.post(`/api/v1/tasks/implement?${params}`);
  },

  /** Create a repair task */
  createRepairTask({ code, tests, output = './outputs' }) {
    const params = new URLSearchParams({ code: code || '', tests: tests || '', output });
    return this.post(`/api/v1/tasks/repair?${params}`);
  },

  // --- File Upload & Download ---

  /** Upload a requirements file. Returns { saved_path, preview, ... } */
  async uploadFile(file) {
    const url = `${this.baseURL}/api/v1/upload`;
    const formData = new FormData();
    formData.append('file', file);
    const resp = await fetch(url, { method: 'POST', body: formData });
    if (!resp.ok) {
      const errText = await resp.text().catch(() => 'Unknown error');
      throw new Error(`Upload failed (${resp.status}): ${errText}`);
    }
    return resp.json();
  },

  /** Get download URL for full project ZIP */
  getDownloadUrl(taskId) {
    return `${this.baseURL}/api/v1/tasks/${taskId}/download`;
  },

  /** Get download URL for a specific phase */
  getPhaseDownloadUrl(taskId, phase) {
    return `${this.baseURL}/api/v1/tasks/${taskId}/download/${phase}`;
  },

  /** List available phases and their files for a task */
  getTaskPhases(taskId) {
    return this.get(`/api/v1/tasks/${taskId}/phases`);
  },

  // --- Dashboard APIs ---

  getDashboardMetrics() {
    return this.get('/api/v1/dashboard/metrics');
  },

  getDashboardTrend(metric = 'success_rate') {
    return this.get(`/api/v1/dashboard/trend?metric=${metric}`);
  },

  getProjectStructure(path = '.') {
    return this.get(`/api/v1/project/structure?path=${encodeURIComponent(path)}`);
  },

  // --- Document & Diagram APIs ---

  /** Get the generated document for a phase */
  getPhaseDocument(taskId, phase) {
    return this.get(`/api/v1/tasks/${taskId}/document/${phase}`);
  },

  /** Get all diagrams for a phase */
  getPhaseDiagrams(taskId, phase) {
    return this.get(`/api/v1/tasks/${taskId}/diagrams/${phase}`);
  },

  /** Get structured JSON data for a phase */
  getPhaseStructured(taskId, phase) {
    return this.get(`/api/v1/tasks/${taskId}/structured/${phase}`);
  },

  /** Render a diagram to SVG via backend (Kroki — more stable than browser Mermaid) */
  renderDiagram(code, format = 'mermaid') {
    return this.post('/api/v1/diagrams/render', { code, format });
  },

  /** Batch render multiple diagrams at once */
  batchRenderDiagrams(diagrams) {
    return this.post('/api/v1/diagrams/batch-render', { diagrams });
  },

  // --- Review APIs ---

  getPendingReview(taskId) {
    return this.get(`/api/v2/tasks/${taskId}/review/pending`);
  },

  getReviewHistory(taskId) {
    return this.get(`/api/v2/tasks/${taskId}/review/history`);
  },

  respondToReview(taskId, reviewId, decision, feedback = '', suggestions = []) {
    const params = new URLSearchParams({
      review_id: reviewId,
      decision,
      feedback,
      suggestions: suggestions.join(','),
    });
    return this.post(`/api/v2/tasks/${taskId}/review/respond?${params}`);
  },

  // --- Command APIs ---

  sendCommand(taskId, command, extra = {}) {
    const params = new URLSearchParams({ command });
    if (extra.focus) params.set('focus', extra.focus);
    if (extra.context) params.set('context', extra.context);
    if (extra.hint) params.set('hint', extra.hint);
    return this.post(`/api/v2/tasks/${taskId}/command?${params}`);
  },

  getInteractionStatus(taskId) {
    return this.get(`/api/v2/tasks/${taskId}/status`);
  },

  // --- Health ---

  checkHealth() {
    return this.get('/health').catch(() => ({ status: 'unreachable' }));
  },
};


/* ============================================================
   WebSocket Client for real-time interaction
   ============================================================ */

class WSClient {
  constructor() {
    this.ws = null;
    this.taskId = null;
    this.mode = 'controller';
    this.listeners = {};
    this.heartbeatTimer = null;
    this.reconnectTimer = null;
    this.connected = false;
    this.reconnectAttempts = 0;
    this.maxReconnectAttempts = 5;
  }

  /**
   * Connect to a task's interactive WebSocket.
   */
  connect(taskId, mode = 'controller') {
    this.taskId = taskId;
    this.mode = mode;

    const url = `${API.wsBaseURL}/api/v2/tasks/${taskId}/interactive?mode=${mode}`;
    this._log(`Connecting to ${url}`);

    try {
      this.ws = new WebSocket(url);
    } catch (e) {
      this._emit('error', { message: `Failed to create WebSocket: ${e.message}` });
      return;
    }

    this.ws.onopen = () => {
      this.connected = true;
      this.reconnectAttempts = 0;
      this._log('WebSocket connected');
      this._startHeartbeat();
      this._emit('connected', { taskId, mode });
    };

    this.ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        this._handleMessage(msg);
      } catch (e) {
        this._log('Failed to parse message', event.data);
      }
    };

    this.ws.onclose = (event) => {
      this.connected = false;
      this._stopHeartbeat();
      this._log(`WebSocket closed: ${event.code} ${event.reason}`);
      this._emit('disconnected', { code: event.code, reason: event.reason });

      // Auto-reconnect
      if (this.reconnectAttempts < this.maxReconnectAttempts) {
        this.reconnectAttempts++;
        const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts), 15000);
        this._log(`Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`);
        this._emit('reconnecting', { attempt: this.reconnectAttempts, delay });
        this.reconnectTimer = setTimeout(() => this.connect(taskId, mode), delay);
      }
    };

    this.ws.onerror = (error) => {
      this._log('WebSocket error', error);
      this._emit('error', { message: 'WebSocket connection error' });
    };
  }

  /**
   * Disconnect the WebSocket.
   */
  disconnect() {
    this.maxReconnectAttempts = 0; // Prevent auto-reconnect
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this._stopHeartbeat();
    if (this.ws) {
      this.ws.close(1000, 'Client disconnect');
      this.ws = null;
    }
    this.connected = false;
  }

  /**
   * Send a message to the server.
   */
  send(type, data = {}) {
    if (!this.ws || !this.connected) {
      this._log('Cannot send: not connected');
      return false;
    }
    const msg = { type, data, timestamp: new Date().toISOString() };
    this.ws.send(JSON.stringify(msg));
    return true;
  }

  // --- Convenience send methods ---

  sendCommand(command) {
    return this.send(`command.${command}`);
  }

  sendPause() { return this.sendCommand('pause'); }
  sendResume() { return this.sendCommand('resume'); }
  sendAbort() { return this.sendCommand('abort'); }
  sendRetry(hint = '') {
    return this.send('command.retry', { hint });
  }

  sendReviewResponse(reviewId, decision, feedback = '', suggestions = []) {
    return this.send('review.response', { review_id: reviewId, decision, feedback, suggestions });
  }

  sendQuestionResponse(questionId, answer) {
    return this.send('question.response', { question_id: questionId, answer });
  }

  sendApprovalResponse(approvalId, resolution, note = '') {
    return this.send('approval.response', { approval_id: approvalId, resolution, note });
  }

  // --- Event listeners ---

  on(event, callback) {
    if (!this.listeners[event]) this.listeners[event] = [];
    this.listeners[event].push(callback);
  }

  off(event, callback) {
    if (!this.listeners[event]) return;
    this.listeners[event] = this.listeners[event].filter(cb => cb !== callback);
  }

  // --- Internal ---

  _handleMessage(msg) {
    const type = msg.type || 'unknown';
    this._log(`Received: ${type}`, msg);
    this._emit(type, msg);
    // Also emit generic 'message' for any unhandled types
    this._emit('message', msg);
  }

  _emit(event, data) {
    const cbs = this.listeners[event] || [];
    cbs.forEach(cb => {
      try { cb(data); } catch (e) { console.error(`WS event handler error [${event}]:`, e); }
    });
  }

  _startHeartbeat() {
    this._stopHeartbeat();
    this.heartbeatTimer = setInterval(() => {
      if (this.connected) {
        this.send('ping');
      }
    }, 28000);
  }

  _stopHeartbeat() {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }

  _log(...args) {
    // console.log('[WS]', ...args);
  }
}

// Export
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { API, WSClient };
}
