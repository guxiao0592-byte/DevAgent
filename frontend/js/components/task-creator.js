/* ============================================================
   DevAgent Frontend — Task Creator (with file upload)
   ============================================================ */

App.registerView('task-create', (container) => {
  container.innerHTML = `
    <div class="grid-2">
      <div class="card">
        <div class="card-header"><span class="card-title">⚙️ 任务配置</span></div>

        <div class="form-group">
          <label class="form-label">任务模式</label>
          <select class="form-select" id="task-mode" onchange="TaskCreator._onModeChange()">
            <option value="full">🔄 全流程（分析→设计→编码→测试→交付）</option>
            <option value="design">📐 仅分析+设计</option>
            <option value="implement">💻 分析→设计→编码→测试</option>
            <option value="repair">🔧 Bug 修复</option>
            <option value="agentic" selected>🤖 自主模式（Agent 自主决策）</option>
            <option value="test">🧪 测试生成+执行</option>
            <option value="debug">🐛 调试分析</option>
          </select>
        </div>

        <!-- File Upload -->
        <div class="form-group">
          <label class="form-label">📎 上传需求文件</label>
          <div class="file-drop-zone" id="file-drop-zone"
               ondragover="event.preventDefault();this.classList.add('dragover');"
               ondragleave="this.classList.remove('dragover');"
               ondrop="TaskCreator._handleDrop(event);this.classList.remove('dragover');">
            <input type="file" id="task-file-input" accept=".md,.txt,.py,.json,.yaml,.yml,.rst"
                   onchange="TaskCreator._handleFileSelect(this)" style="display:none;">
            <div style="text-align:center;padding:16px 12px;">
              <div style="font-size:2rem;">📁</div>
              <div style="font-weight:600;margin:4px 0;">拖拽文件到此处</div>
              <div style="font-size:0.78rem;color:var(--text-muted);">
                或 <a href="#" onclick="document.getElementById('task-file-input').click();return false;">点击选择文件</a>
              </div>
              <div style="font-size:0.7rem;color:var(--text-muted);margin-top:2px;">.md .txt .py .json .yaml .yml .rst</div>
            </div>
          </div>
          <div id="file-upload-status" style="margin-top:8px;font-size:0.85rem;min-height:20px;"></div>
        </div>

        <div class="form-group">
          <label class="form-label">任务描述（如不上传文件，请在此输入）</label>
          <textarea class="form-textarea" id="task-input" placeholder="输入任务描述或粘贴需求文档内容...&#10;&#10;示例：&#10;构建一个支持 +、-、*、/ 运算的计算器应用，需要错误处理和完整的测试套件。"></textarea>
          <div class="form-hint">如已上传文件，此处可留空；如同时提供，文件内容优先</div>
        </div>

        <div class="form-row">
          <div class="form-group">
            <label class="form-label">工作空间路径</label>
            <input class="form-input" id="task-workspace" value="." placeholder="./src/">
          </div>
          <div class="form-group">
            <label class="form-label">输出目录</label>
            <input class="form-input" id="task-output" value="./outputs" placeholder="./outputs">
          </div>
        </div>

        <div class="form-row">
          <div class="form-group">
            <label class="form-label">最大重试次数</label>
            <input class="form-input" id="task-max-retry" type="number" value="2" min="0" max="5">
          </div>
          <div class="form-group">
            <label class="form-label">交互模式</label>
            <select class="form-select" id="task-interactive">
              <option value="off">关闭（自动执行）</option>
              <option value="observe">仅观察</option>
              <option value="approval">审批模式</option>
              <option value="full">完整交互</option>
            </select>
          </div>
        </div>

        <div class="mt-16 flex gap-8">
          <button class="btn btn-primary btn-lg" onclick="TaskCreator.submit()">🚀 启动任务</button>
          <button class="btn btn-lg" onclick="TaskCreator._loadTemplate('calculator')">📝 加载示例</button>
          <button class="btn btn-lg" onclick="App.navigate('pipeline')">📋 查看任务</button>
        </div>
        <div id="task-create-result" class="mt-12"></div>
      </div>

      <!-- Right panel -->
      <div>
        <div class="card mb-16">
          <div class="card-header"><span class="card-title">📖 模式说明</span></div>
          <div id="mode-info" style="font-size:0.85rem;color:var(--text-secondary);line-height:1.7;">
            选择一个任务模式以查看说明。
          </div>
        </div>
        <div class="card">
          <div class="card-header"><span class="card-title">⚡ 快捷场景</span></div>
          <div style="display:flex;flex-direction:column;gap:8px;">
            <button class="btn" onclick="TaskCreator._loadTemplate('calculator')" style="text-align:left;">🔢 计算器应用</button>
            <button class="btn" onclick="TaskCreator._loadTemplate('todo')" style="text-align:left;">✅ 待办事项 API</button>
            <button class="btn" onclick="TaskCreator._loadTemplate('auth')" style="text-align:left;">🔐 用户认证系统</button>
            <button class="btn" onclick="TaskCreator._loadTemplate('blog')" style="text-align:left;">✍️ 博客系统</button>
          </div>
        </div>
      </div>
    </div>
  `;
  TaskCreator._onModeChange();
  TaskCreator._uploadedFilePath = null;
  TaskCreator._uploadedFileName = null;
});

const TaskCreator = {
  _uploadedFilePath: null,
  _uploadedFileName: null,

  _onModeChange() {
    const mode = document.getElementById('task-mode')?.value;
    const info = document.getElementById('mode-info');
    if (!info) return;
    const descriptions = {
      full: `<strong>全流程模式</strong> — 端到端自动化<br><br>📋 需求分析 → 🏗️ 架构设计 → 💻 代码生成 → 🧪 测试执行 → 📦 交付<br><br>每阶段可人工审核。完成后可下载完整项目 ZIP。<br><br>⏱ 预计 5-15 分钟`,
      design: `<strong>分析+设计模式</strong> — 文档产出<br><br>📋 需求分析 → 🏗️ 架构设计<br><br>产出：需求文档、用例图、DFD数据流图、类图、时序图等。<br><br>⏱ 预计 3-8 分钟`,
      implement: `<strong>实现模式</strong> — 带设计的编码<br><br>📋 需求 → 🏗️ 设计 → 💻 编码 → 🧪 测试<br><br>完整的分析-设计-编码-测试流程。<br><br>⏱ 预计 8-15 分钟`,
      repair: `<strong>修复模式</strong> — Bug修复<br><br>🐛 调试 → 🔧 定位 → 📝 补丁 → 🧪 验证<br><br>三层故障定位，最小变更修复。<br><br>⏱ 预计 2-5 分钟`,
      agentic: `<strong>自主模式</strong> — Agent自主<br><br>🤖 ReAct 循环（Think→Act→Observe），调用 30+ 工具完成任务。<br><br>适合探索性任务、复杂调试。<br><br>⏱ 预计 5-20 分钟`,
      test: `<strong>测试模式</strong> — 测试套件<br><br>🔍 代码分析 → 🧪 测试生成 → ▶️ 执行<br><br>自动生成 pytest 测试套件。<br><br>⏱ 预计 2-5 分钟`,
      debug: `<strong>调试模式</strong> — 问题诊断<br><br>🐛 5步根因分析 → 10种缺陷分类 → 报告<br><br>⏱ 预计 2-5 分钟`,
    };
    info.innerHTML = descriptions[mode] || '';
  },

  _handleFileSelect(input) {
    if (input.files && input.files[0]) {
      TaskCreator._uploadFile(input.files[0]);
    }
  },

  _handleDrop(event) {
    event.preventDefault();
    const files = event.dataTransfer.files;
    if (files && files[0]) {
      TaskCreator._uploadFile(files[0]);
    }
  },

  async _uploadFile(file) {
    const status = document.getElementById('file-upload-status');
    if (!status) return;

    status.innerHTML = '<span class="spinner"></span> 正在上传...';
    try {
      const result = await API.uploadFile(file);
      this._uploadedFilePath = result.saved_path;
      this._uploadedFileName = result.filename;
      // Fill textarea with preview
      const input = document.getElementById('task-input');
      if (input && result.preview) {
        input.value = result.preview;
      }
      // Update drop zone
      const dz = document.getElementById('file-drop-zone');
      if (dz) dz.classList.add('has-file');
      status.innerHTML = `<span style="color:var(--green);">✅ ${Utils.escapeHtml(result.filename)} — ${Utils.formatNumber(result.size_bytes)} 字节</span>
        <button class="btn btn-sm mt-4" onclick="TaskCreator._clearFile()">✕ 清除</button>`;
    } catch (e) {
      status.innerHTML = `<span style="color:var(--red);">❌ 上传失败: ${Utils.escapeHtml(e.message)}</span>`;
    }
  },

  _clearFile() {
    this._uploadedFilePath = null;
    this._uploadedFileName = null;
    const status = document.getElementById('file-upload-status');
    if (status) status.innerHTML = '';
    const dz = document.getElementById('file-drop-zone');
    if (dz) dz.classList.remove('has-file');
    const fi = document.getElementById('task-file-input');
    if (fi) fi.value = '';
  },

  async submit() {
    const mode = document.getElementById('task-mode').value;
    const workspace = document.getElementById('task-workspace').value.trim() || '.';
    const output = document.getElementById('task-output').value.trim() || './outputs';
    const maxRetry = parseInt(document.getElementById('task-max-retry').value) || 2;
    const resultDiv = document.getElementById('task-create-result');

    // Use uploaded file path if available, otherwise use text input
    let input = '';
    if (this._uploadedFilePath) {
      input = this._uploadedFilePath;
    } else {
      input = document.getElementById('task-input').value.trim();
    }

    if (!input) {
      resultDiv.innerHTML = '<div class="alert alert-error">请输入任务描述或上传需求文件</div>';
      return;
    }

    resultDiv.innerHTML = '<div class="alert alert-info"><span class="spinner"></span> 正在启动任务...</div>';

    try {
      let resp;
      switch (mode) {
        case 'full':
          resp = await API.createFullTask({ input, output, maxRetry });
          break;
        case 'design':
          resp = await API.createDesignTask({ input, output });
          break;
        case 'implement':
          resp = await API.createImplementTask({ input, output });
          break;
        case 'repair':
          resp = await API.createRepairTask({ code: workspace, output });
          break;
        case 'test':
        case 'debug':
          resp = await API.createAgenticTask({ description: `【${mode.toUpperCase()} MODE】\n${input}`, workspace, output });
          break;
        case 'agentic':
        default:
          resp = await API.createAgenticTask({ description: input, workspace, output });
          break;
      }

      const downUrl = API.getDownloadUrl(resp.task_id);
      const isPipeline = ['full', 'design', 'implement'].includes(mode);

      resultDiv.innerHTML = `
        <div class="alert alert-success">
          ✅ 任务已创建！<br>
          <strong>任务 ID:</strong> <code>${resp.task_id}</code><br>
          <strong>状态:</strong> ${resp.status}<br>
          <strong>输出:</strong> ${resp.output_dir}
        </div>
        <div class="mt-8 flex gap-8" style="flex-wrap:wrap;">
          <button class="btn btn-primary" onclick="App.setActiveTask('${resp.task_id}');App.navigate('pipeline');">
            📋 查看流水线
          </button>
          <button class="btn" onclick="App.setActiveTask('${resp.task_id}');App.navigate('interaction');">
            💬 交互面板
          </button>
          ${isPipeline ? `<a class="btn" href="${downUrl}" target="_blank">📥 下载项目 ZIP</a>` : ''}
        </div>
      `;

      App.setActiveTask(resp.task_id);
      App.toast('任务已创建: ' + resp.task_id, 'success');
    } catch (e) {
      resultDiv.innerHTML = `<div class="alert alert-error">❌ 创建失败: ${Utils.escapeHtml(e.message)}</div>`;
    }
  },

  _loadTemplate(name) {
    TaskCreator._clearFile();
    const templates = {
      calculator: `# 计算器应用需求

## 功能需求
1. 支持加法、减法、乘法、除法四种基本运算
2. 支持连续运算（链式计算）
3. 错误处理：除零错误、无效输入
4. 支持小数运算
5. 历史记录功能

## 非功能需求
1. 代码覆盖率 > 90%
2. 类型标注完整
3. 所有公开 API 有 docstring`,
      todo: `# 待办事项 API 需求

## 功能需求
1. 创建待办事项（标题、描述、优先级、截止日期）
2. 查询待办列表（支持筛选和排序）
3. 更新待办状态（完成/未完成）
4. 删除待办事项
5. RESTful API 设计

## 非功能需求
1. 输入验证和错误处理
2. pytest 测试套件
3. FastAPI 框架实现`,
      auth: `# 用户认证系统需求

## 功能需求
1. 用户注册（邮箱、密码）
2. 用户登录（JWT Token）
3. 密码加密存储（bcrypt）
4. Token 刷新机制
5. 权限角色管理

## 非功能需求
1. 安全漏洞防护
2. 完整测试覆盖
3. 数据库模型设计`,
      blog: `# 博客系统需求

## 功能需求
1. 文章 CRUD 操作
2. 文章分类和标签
3. 用户评论功能
4. Markdown 编辑器支持
5. 文章搜索功能

## 非功能需求
1. RESTful API 设计
2. 数据库索引优化
3. 分页查询支持`,
    };
    const input = document.getElementById('task-input');
    if (input) input.value = templates[name] || '';
  },
};
