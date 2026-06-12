/* ============================================================
   DevAgent Frontend — Task State Machine (FSM)
   Standardized task lifecycle: idle → running → ... → completed
   Drives UI state transitions, command availability, spinner/error display.
   ============================================================ */

/**
 * Task FSM — defines all legal states and transitions.
 *
 * States:
 *   idle               — No task selected / initial state
 *   creating_task      — User just submitted task creation
 *   running            — Agent is executing
 *   waiting_review     — Agent paused, waiting for human review
 *   waiting_user_input — Agent asked a question, waiting for answer
 *   paused             — User paused execution
 *   repairing          — Agent is in repair/retry cycle
 *   completed          — Task finished successfully
 *   failed             — Task failed with errors
 *   aborted            — User aborted the task
 *
 * Each state defines:
 *   - allowedCommands: what user actions are legal
 *   - ui: CSS class, badge text, icon
 *   - autoRefresh: should frontend poll for updates?
 */

const TASK_FSM = {
  states: {
    idle: {
      commands: [],
      ui: { badge: '', icon: '📋', label: '就绪', cls: '' },
    },
    creating_task: {
      commands: [],
      ui: { badge: 'badge-blue', icon: '⏳', label: '创建中', cls: 'pulse-dot' },
    },
    running: {
      commands: ['pause', 'abort'],
      ui: { badge: 'badge-blue', icon: '🔄', label: '运行中', cls: 'pulse-dot' },
      autoRefresh: true,
    },
    waiting_review: {
      commands: ['approve', 'revise', 'reject', 'abort'],
      ui: { badge: 'badge-orange', icon: '📋', label: '待审核', cls: '' },
    },
    waiting_user_input: {
      commands: ['answer', 'abort'],
      ui: { badge: 'badge-purple', icon: '❓', label: '待回复', cls: '' },
    },
    paused: {
      commands: ['resume', 'abort'],
      ui: { badge: 'badge-cyan', icon: '⏸️', label: '已暂停', cls: '' },
    },
    repairing: {
      commands: ['abort'],
      ui: { badge: 'badge-orange', icon: '🔧', label: '修复中', cls: 'pulse-dot' },
      autoRefresh: true,
    },
    completed: {
      commands: ['download', 'replay', 'retry'],
      ui: { badge: 'badge-green', icon: '✅', label: '已完成', cls: '' },
    },
    failed: {
      commands: ['retry', 'download', 'replay'],
      ui: { badge: 'badge-red', icon: '❌', label: '失败', cls: '' },
    },
    aborted: {
      commands: ['retry', 'download'],
      ui: { badge: 'badge-orange', icon: '⏹️', label: '已中止', cls: '' },
    },
  },

  /**
   * Determine the FSM state from task data.
   * Priority: backend status > review pending > agent question
   */
  derive(task, reviewPending, agentQuestion) {
    if (!task) return 'idle';

    const status = (task.status || '').toUpperCase();

    // Terminal states
    if (status === 'COMPLETED' || status === 'FINISHED') return 'completed';
    if (status === 'FAILED') return 'failed';
    if (status === 'ABORTED') return 'aborted';

    // Paused
    if (status === 'PAUSED') return 'paused';

    // Running sub-states
    if (status === 'RUNNING' || !status) {
      if (reviewPending) return 'waiting_review';
      if (agentQuestion) return 'waiting_user_input';
      if (task.phase === 'repair' || status.includes('REPAIR')) return 'repairing';
      return 'running';
    }

    if (status === 'PENDING') return 'creating_task';
    return 'running';
  },

  /** Check if a command is allowed in the current state */
  canExecute(state, command) {
    const s = this.states[state] || this.states.idle;
    return s.commands.includes(command);
  },

  /** Get allowed commands for current state */
  getAllowedCommands(state) {
    const s = this.states[state] || this.states.idle;
    return s.commands;
  },

  /** Get UI decorators for current state */
  getUI(state) {
    const s = this.states[state] || this.states.idle;
    return s.ui;
  },

  /** Should the frontend auto-refresh in this state? */
  needsAutoRefresh(state) {
    const s = this.states[state] || this.states.idle;
    return !!s.autoRefresh;
  },

  /** Transition validation */
  canTransition(from, to) {
    const valid = {
      idle: ['creating_task'],
      creating_task: ['running', 'failed'],
      running: ['waiting_review', 'waiting_user_input', 'paused', 'repairing', 'completed', 'failed', 'aborted'],
      waiting_review: ['running', 'repairing', 'failed', 'aborted'],
      waiting_user_input: ['running', 'failed', 'aborted'],
      paused: ['running', 'aborted'],
      repairing: ['running', 'completed', 'failed', 'aborted'],
      completed: ['idle', 'creating_task'],
      failed: ['idle', 'creating_task', 'running'],
      aborted: ['idle', 'creating_task'],
    };
    return (valid[from] || []).includes(to);
  },

  /** Get next expected state from a command */
  commandTarget(command) {
    const map = {
      'pause': 'paused', 'resume': 'running', 'abort': 'aborted',
      'retry': 'creating_task', 'approve': 'running', 'revise': 'repairing',
      'answer': 'running',
    };
    return map[command] || null;
  },
};

// Export for use in components
if (typeof module !== 'undefined') { module.exports = { TASK_FSM }; }
