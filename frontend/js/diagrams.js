/* ============================================================
   DevAgent Frontend — Diagram Rendering Engine v3
   Strategy: Server (Kroki) first → Browser Mermaid fallback → Show code
   ============================================================ */

const Diagrams = {
  initialized: false,
  counter: 0,
  _renderCache: {},  // { code_hash: svg_string }

  init() {
    if (this.initialized) return;
    if (typeof mermaid !== 'undefined') {
      mermaid.initialize({
        startOnLoad: false, theme: 'default',
        themeVariables: {
          fontFamily: '-apple-system,BlinkMacSystemFont,sans-serif',
          fontSize: '14px', primaryColor: '#e8f5e9', primaryTextColor: '#1b5e20',
          primaryBorderColor: '#4caf50', lineColor: '#546e7a', secondaryColor: '#e3f2fd',
        },
        flowchart: { useMaxWidth: true, htmlLabels: true, curve: 'basis', padding: 15 },
        sequence: { useMaxWidth: true, showSequenceNumbers: true },
        class: { useMaxWidth: true }, state: { useMaxWidth: true }, er: { useMaxWidth: true },
      });
      this.initialized = true;
    }
  },

  /** Simple hash for caching */
  _hash(s) { let h = 0; for (let i = 0; i < s.length; i++) { h = ((h << 5) - h) + s.charCodeAt(i); h |= 0; } return String(h); },

  _stripFences(code) {
    let c = code.trim();
    const m = c.match(/```(?:mermaid|mmd|plantuml)?\s*\n([\s\S]*?)```/);
    return m ? m[1].trim() : c;
  },

  /** Clean Mermaid code — fix common LLM errors */
  _cleanCode(code) {
    let c = code;

    // Strip Mermaid markdown fences (inner ones that survived _stripFences)
    c = c.replace(/^```mermaid\s*\n?/gm, '').replace(/^```\s*$/gm, '');

    // Fix 1: Chinese/smart quotes to ASCII
    c = c.replace(/“|”/g, '"').replace(/‘|’/g, "'");

    // Fix 2: Remove invisible/control characters except newlines
    c = c.replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]/g, '');

    // Fix 3: classDiagram — class names with spaces need backtick quotes
    if (/^\s*classDiagram/m.test(c)) {
      c = c.replace(/^(\s*class\s+)(?!`)([^{ \n]+ [^{]*?)(\s*\{)/gm, (_, pfx, name, br) => {
        if (!name.trim().startsWith('`')) return `${pfx}\`${name.trim()}\`${br}`;
        return _;
      });
    }

    // Fix 4: flowchart — unquoted node labels with special chars
    // e.g. Node[Some Label] → Node[“Some Label”]
    const lines = c.split('\n');
    const out = [];
    for (const line of lines) {
      let l = line.trimStart();
      const indent = line.substring(0, line.length - l.length);

      // Fix node IDs with dots/slashes in flowcharts (but not in classDiagram or erDiagram)
      const isFlow = /^\s*(flowchart|graph\s+[TBLR])/im.test(c);
      if (isFlow) {
        // Only fix standalone node definitions (not inside subgraphs or class definitions)
        const nodeDef = l.match(/^([\w./-]+)(\[(?![“'])[^\]]*\]|\{(?![“'])[^}]*\}|\((?![“'()]|”[^”]*”\))[^)]*\))/);
        if (nodeDef) {
          const nid = nodeDef[1];
          if (/[./]/.test(nid)) {
            const safe = nid.replace(/[./]/g, '_');
            l = l.replace(new RegExp('^' + nid.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')), safe);
          }
        }
      }

      // Fix: node labels without quotes that contain non-ASCII chars
      // Node[中文标签] → Node[“中文标签”]
      if (isFlow) {
        l = l.replace(/^(\s*)([\w_-]+)(\[)([^”\]]*[一-鿿][^”\]]*)(\])/,
          '$1$2$3”$4”$5');
        l = l.replace(/^(\s*)([\w_-]+)(\()([^”)]*[一-鿿][^”)]*)(\))/,
          '$1$2$3”$4”$5');
      }

      out.push(indent + l);
    }
    c = out.join('\n');

    // Fix 5: erDiagram — entity names with spaces
    c = c.replace(/^(\s*)([A-Za-z][\w ]+ [A-Za-z][\w ]+)(\s*\{)/gm, (_, pfx, name, rest) => {
      return `${pfx}${name.replace(/\s+/g, '_')}${rest}`;
    });

    // Fix 6: sequenceDiagram — participant names with special chars
    if (/^\s*sequenceDiagram/m.test(c)) {
      c = c.replace(/^(\s*participant\s+)([^\s:]+)(\s+as\s+)?/gm, (_, pfx, id, asKwd) => {
        const clean = id.replace(/[^a-zA-Z0-9_-]/g, '_');
        return `${pfx}${clean}${asKwd || ''}`;
      });
    }

    // Fix 7: Edge case — spurious empty parentheses in labels
    c = c.replace(/\|\s*\(\)\s*\|/g, '|');

    return c;
  },

  /** Inject pre-rendered SVG into container */
  _injectSVG(el, svg, opts) {
    this._renderToolbar(el, opts);
    const w = this._makeWrapper(el);
    w.innerHTML = svg;
    this._styleSVG(w);
  },

  /**
   * Render a Mermaid diagram.
   * Strategy: 1) Fix syntax  2) Browser Mermaid (fast)  3) Kroki fallback  4) Show code
   */
  async render(containerId, code, opts = {}) {
    const el = document.getElementById(containerId);
    if (!el) return;

    code = this._stripFences(code);
    if (!code) { this._showError(el, 'Empty diagram code'); return; }

    // Don't try to render PlantUML as Mermaid
    if (code.startsWith('@startuml')) {
      this._showPlantUML(el, code);
      return;
    }

    code = this._cleanCode(code);

    // Check cache
    const cacheKey = this._hash(code);
    if (this._renderCache[cacheKey]) {
      this._injectSVG(el, this._renderCache[cacheKey], opts);
      return;
    }

    this._renderToolbar(el, opts);
    const w = this._makeWrapper(el);
    w.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-muted);">⏳ 渲染中...</div>';

    // Strategy 1: Browser Mermaid.js (fast, no network needed)
    if (typeof mermaid !== 'undefined') {
      this.init();
      try {
        const rid = `mmd${++this.counter}`;
        const { svg } = await mermaid.render(`${rid}s`, code);
        this._renderCache[cacheKey] = svg;
        w.innerHTML = svg;
        this._styleSVG(w);
        return;
      } catch (e1) {
        console.debug('Browser Mermaid failed:', e1.message.substring(0, 80));
        // Fall through to Kroki
      }
    }

    // Strategy 2: Server-side Kroki (more robust parser, needs network)
    w.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-muted);">⏳ 服务端渲染中...</div>';
    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 8000);
      const resp = await fetch(`${API.baseURL}/api/v1/diagrams/render`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code, format: 'mermaid' }),
        signal: controller.signal,
      });
      clearTimeout(timeoutId);
      if (resp.ok) {
        const result = await resp.json();
        if (result && result.svg) {
          this._renderCache[cacheKey] = result.svg;
          w.innerHTML = result.svg;
          this._styleSVG(w);
          return;
        }
      }
    } catch (e2) {
      console.debug('Kroki failed:', e2.message.substring(0, 80));
    }

    // Strategy 3: Show code
    w.innerHTML = this._errorHTML('图表渲染失败', '浏览器和服务端渲染均失败，请检查 Mermaid 语法', code);
  },

  /** Batch render: each diagram gets its own render() call with full fallback chain */
  async batchRender(containerPrefix, diagrams, opts = {}) {
    if (!diagrams || !diagrams.length) return;

    const promises = diagrams.map((d, i) => {
      const elId = `${containerPrefix}-${d.name || i}`;
      return this.render(elId, d.code || '', { ...opts, title: d.name, toolbar: false });
    });
    await Promise.allSettled(promises);
  },

  /** Deprecated: use _injectSVG */
  _render(el, svg, opts) { return this._injectSVG(el, svg, opts); },

  _styleSVG(wrapper) {
    const svg = wrapper.querySelector('svg');
    if (svg) { svg.style.maxWidth = '100%'; svg.style.height = 'auto'; }
  },

  _renderToolbar(el, opts) {
    if (opts.toolbar === false) return;
    // Avoid double toolbar
    if (el.querySelector('.diagram-toolbar')) return;

    const tb = document.createElement('div');
    tb.className = 'diagram-toolbar';
    if (opts.title) {
      const t = document.createElement('span');
      t.style.cssText = 'font-weight:600;font-size:0.85rem;color:var(--text-secondary);';
      t.textContent = opts.title;
      tb.appendChild(t);
    }
    const sp = document.createElement('span'); sp.style.flex = '1'; tb.appendChild(sp);
    const b1 = document.createElement('button'); b1.className = 'btn btn-sm';
    b1.textContent = '⬇ SVG'; b1.onclick = () => this._dl(el, opts.title || 'diagram'); tb.appendChild(b1);
    const b2 = document.createElement('button'); b2.className = 'btn btn-sm';
    b2.textContent = '⛶ 全屏'; b2.onclick = () => this._fs(el); tb.appendChild(b2);
    el.appendChild(tb);
  },

  _makeWrapper(el) {
    let w = el.querySelector('.diagram-container');
    if (!w) {
      w = document.createElement('div');
      w.className = 'diagram-container';
      el.appendChild(w);
    }
    return w;
  },

  _showError(el, msg) {
    this._makeWrapper(el).innerHTML = this._errorHTML(msg, '', '');
  },

  _errorHTML(title, detail, code) {
    let html = `<div style="padding:16px;font-size:0.82rem;border:1px solid var(--red);border-radius:8px;background:var(--red-bg);">
      <strong style="color:var(--red);">⚠ ${Utils.escapeHtml(title)}</strong>
      ${detail ? `<div style="margin-top:4px;color:var(--text-secondary);">${Utils.escapeHtml(detail)}</div>` : ''}`;
    if (code) {
      html += `<details style="margin-top:8px;"><summary style="cursor:pointer;">查看源码</summary>
        <pre style="font-size:0.72rem;margin-top:4px;max-height:150px;overflow:auto;background:var(--bg-code);padding:8px;border-radius:4px;">${Utils.escapeHtml(code.substring(0, 500))}</pre></details>`;
    }
    html += '</div>';
    return html;
  },

  _codeOnlyHTML(code, note) {
    return `<div style="padding:16px;font-size:0.82rem;border:1px solid var(--border);border-radius:8px;">
      <div style="color:var(--text-muted);margin-bottom:8px;">${Utils.escapeHtml(note)}</div>
      <pre style="font-size:0.72rem;max-height:150px;overflow:auto;background:var(--bg-code);padding:8px;border-radius:4px;">${Utils.escapeHtml(code.substring(0, 500))}</pre></div>`;
  },

  _showPlantUML(el, code) {
    this._makeWrapper(el).innerHTML = `
      <div style="text-align:center;padding:20px;color:var(--text-muted);font-size:0.85rem;">
        <p>🌿 PlantUML 图表</p>
        <pre style="text-align:left;max-height:200px;overflow:auto;margin-top:8px;font-size:0.72rem;background:var(--bg-code);padding:8px;border-radius:4px;">${Utils.escapeHtml(code.substring(0, 500))}</pre>
      </div>`;
  },

  // ===== Diagram Generators (structured data → Mermaid code) =====
  renderData(elId, type, data, opts = {}) {
    let code;
    switch (type) {
      case 'class': case 'er': case 'dfd0': case 'dfd1': case 'sequence':
      case 'state': case 'component': case 'deploy': case 'deployment':
      case 'flow': case 'usecase': case 'activity': case 'raw':
        code = this['_' + (type === 'deploy' ? 'deployment' : type)](data); break;
      default: code = typeof data === 'string' ? data : JSON.stringify(data);
    }
    return this.render(elId, code, opts);
  },

  // Include minimal generators for demo/fallback scenarios
  _dfd0(d) {
    const L = ['flowchart LR', `    SYS(("${d.systemName || 'System'}"))`];
    (d.externalEntities || []).forEach((e, i) => {
      L.push(`    E${i}["${e.name}"]`);
    });
    (d.dataFlows || []).forEach(f => {
      const src = f.from === 'System' ? 'SYS' : `E${(d.externalEntities||[]).findIndex(e=>e.name===f.from)}`;
      const tgt = f.to === 'System' ? 'SYS' : `E${(d.externalEntities||[]).findIndex(e=>e.name===f.to)}`;
      L.push(`    ${src} -->|"${f.label||''}"| ${tgt}`);
    });
    return L.join('\n');
  },

  _class(d) {
    const L = ['classDiagram'];
    for (const mod of (d.modules || [])) {
      for (const cls of (mod.classes || mod.key_classes || [])) {
        const n = typeof cls === 'string' ? cls : cls.name;
        L.push(`    class \`${n}\` {`);
        const attrs = typeof cls === 'string' ? [] : (cls.attributes || []);
        const methods = typeof cls === 'string' ? [] : (cls.methods || []);
        for (const a of attrs) { const [an, at] = Array.isArray(a) ? a : [a.name||a, a.type||'']; L.push(`        +${at ? at + ' ' : ''}${an}`); }
        for (const m of methods) { const [mn, mr] = Array.isArray(m) ? m : [m.name||m, m.returns||'']; L.push(`        +${mn}()${mr ? ' ' + mr : ''}`); }
        L.push('    }');
      }
    }
    if (d.relationships && d.relationships.length) {
      const rm = { 'one-to-one': '"1" --> "1"', 'one-to-many': '"1" --> "*"', 'many-to-one': '"*" --> "1"', 'many-to-many': '"*" --> "*"', inheritance: ' <|-- ', composition: ' *-- ', aggregation: ' o-- ', dependency: ' ..> ' };
      for (const r of d.relationships) L.push(`    \`${r.from}\`${rm[r.type]||' --> '}\`${r.to}\`${r.label?' : '+r.label:''}`);
    }
    return L.join('\n');
  },

  _er(d) {
    const L = ['erDiagram'];
    for (const t of (d.tables || [])) {
      const n = (t.table || t.name || 'TABLE').toUpperCase();
      L.push(`    ${n.replace(/[^A-Z0-9_]/g,'_')} {`);
      for (const c of (t.columns || [])) {
        let tags = ''; if ((c.constraints||'').toUpperCase().includes('PRIMARY')) tags += ' PK'; if ((c.constraints||'').toUpperCase().includes('FOREIGN')) tags += ' FK';
        L.push(`        ${c.type||'VARCHAR'} ${c.name||'?'}${tags}`);
      }
      L.push('    }');
    }
    return L.join('\n');
  },

  _sequence(d) {
    const L = ['sequenceDiagram'];
    if (d.title) L.push(`    title ${d.title}`);
    const pm = {};
    (d.participants || []).forEach(p => { const id = p.replace(/[^a-zA-Z0-9_]/g,'_'); pm[p] = id; L.push(`    participant ${id} as ${p}`); });
    for (const s of (d.steps || [])) {
      const from = pm[s.from||s.actor] || (s.from||'').replace(/[^a-zA-Z0-9_]/g,'_');
      const to = pm[s.to||s.target] || (s.to||'').replace(/[^a-zA-Z0-9_]/g,'_');
      if ((s.type||'')==='response') L.push(`    ${to} -->> ${from}: ${s.action||s.message||''}`);
      else if ((s.type||'')==='note') L.push(`    Note over ${from},${to}: ${s.action||''}`);
      else L.push(`    ${from} ->> ${to}: ${s.action||s.message||''}`);
    }
    return L.join('\n');
  },

  _state(d) {
    const L = ['stateDiagram-v2'];
    for (const t of (d.transitions || [])) {
      const from = (!t.from||t.from==='*') ? '[*]' : t.from.replace(/[^a-zA-Z0-9_]/g,'_');
      const to = (!t.to||t.to==='*') ? '[*]' : t.to.replace(/[^a-zA-Z0-9_]/g,'_');
      L.push(`    ${from} --> ${to}${(t.trigger||t.guard)?': '+(t.trigger||'')+(t.guard?' ['+t.guard+']':'') :''}`);
    }
    return L.join('\n');
  },

  _component(d) {
    const L = ['flowchart TD']; const mm = {};
    (d.modules || []).forEach((m, i) => {
      const id = `M${i}`; mm[m.name] = id;
      if ((m.interfaces||[]).length) { L.push(`    subgraph ${id}["${m.name}"]`); (m.interfaces||[]).forEach((ifc,j)=>L.push(`        ${id}I${j}["${ifc}"]`)); L.push('    end'); }
      else L.push(`    ${id}["${m.name}"]`);
    });
    for (const m of (d.modules||[])) for (const dep of (m.dependencies||[])) L.push(`    ${mm[m.name]} --> ${mm[dep]||dep}`);
    return L.join('\n');
  },

  _deployment(d) {
    const L = ['flowchart TD']; const nm = {};
    (d.nodes || []).forEach((n, i) => {
      const id = `N${i}`; nm[n.name] = id;
      if (n.contains && n.contains.length) { L.push(`    subgraph ${id}["${n.name}"]`); n.contains.forEach((c,j)=>L.push(`        ${id}_${j}["${c}"]`)); L.push('    end'); }
      else if (n.type==='datastore') L.push(`    ${id}[("${n.name}")]`);
      else if (n.type==='external') L.push(`    ${id}["${n.name}"]`);
      else L.push(`    ${id}["${n.name}"]`);
    });
    for (const n of (d.nodes||[])) for (const c of (n.connects_to||[])) L.push(`    ${nm[n.name]} -->|"${c.protocol||c.label||''}"| ${nm[c.target||c]||c.target}`);
    return L.join('\n');
  },

  _flowchart(d) {
    const L = ['flowchart TD'];
    for (const n of (d.nodes||[])) {
      const shapes = { box: `["${n.label}"]`, rounded: `("${n.label}")`, diamond: `{"${n.label}"}`, circle: `(("${n.label}"))` };
      L.push(`    ${n.id}${shapes[n.shape||'box']||shapes.box}`);
    }
    for (const e of (d.edges||[])) L.push(`    ${e.from} -->${e.label?'|"'+e.label+'"|':''} ${e.to}`);
    return L.join('\n');
  },

  _usecase(d) {
    const L = ['flowchart LR'];
    (d.actors||[]).forEach((a,i) => L.push(`    A${i}["👤 ${a.name||a.id}"]`));
    L.push(`    subgraph SYS["${d.systemName||'System'}"]`);
    const um = {}; (d.useCases||[]).forEach((uc,i) => { const id=`U${i}`; um[uc.id||uc.name]=id; L.push(`    ${id}["${uc.name}"]`); });
    L.push('    end');
    if ((d.relationships||[]).length) {
      for (const r of d.relationships) {
        const aIdx = (d.actors||[]).findIndex(a=>(a.id||a.name)===r.from);
        const src = aIdx>=0 ? `A${aIdx}` : (um[r.from]||r.from);
        const tgt = um[r.to]||r.to;
        if (r.type==='include') L.push(`    ${tgt} -.->|"«include»"| ${src}`);
        else if (r.type==='extend') L.push(`    ${src} -.->|"«extend»"| ${tgt}`);
        else L.push(`    ${src} --> ${tgt}`);
      }
    } else { for (let ai=0;ai<(d.actors||[]).length;ai++) for (let ui=0;ui<(d.useCases||[]).length;ui++) L.push(`    A${ai} --> U${ui}`); }
    return L.join('\n');
  },

  _activity(d) {
    const L = ['flowchart TD'];
    if (d.title) L.push(`    START(("▶"))`);
    let prev = d.title ? 'START' : null;
    (d.nodes||[]).forEach((n,i) => {
      const id = `A${i}`;
      if (n.type==='action') { L.push(`    ${id}["${n.label}"]`); if (prev) L.push(`    ${prev} --> ${id}`); prev = id; }
      else if (n.type==='decision') { L.push(`    ${id}{"${n.label}"}`); if(prev) L.push(`    ${prev} --> ${id}`); L.push(`    ${id} -->|"✓ ${n.yes||'是'}"| ${id}Y["${n.yes||'是'}"]`); L.push(`    ${id} -->|"✗ ${n.no||'否'}"| ${id}N["${n.no||'否'}"]`); prev=null; }
      else { L.push(`    ${id}["${n.label||n.id||''}"]`); if(prev) L.push(`    ${prev} --> ${id}`); prev=id; }
    });
    return L.join('\n');
  },

  _dfd1(d) {
    const L = ['flowchart TD'];
    const em={}, pm={}, dm={};
    (d.externalEntities||[]).forEach((e,i)=>{em[e.name]=`E${i}`; L.push(`    E${i}["${e.name}"]`);});
    (d.processes||[]).forEach((p,i)=>{pm[p.id||p.name]=`P${i}`; L.push(`    P${i}["${p.name}"]`);});
    (d.dataStores||[]).forEach((ds,i)=>{dm[ds.name]=`D${i}`; L.push(`    D${i}[("${ds.name}")]`);});
    for (const e of (d.externalEntities||[])) { const ts=Array.isArray(e.connects_to)?e.connects_to:[e.connects_to]; for(const t of ts) L.push(`    ${em[e.name]} --> ${pm[t]||t}`); }
    for (const p of (d.processes||[])) for (const o of (p.outputs||[])) for (const o2 of (d.processes||[])) if(o2!==p&&(o2.inputs||[]).includes(o)) L.push(`    ${pm[p.id||p.name]} -->|"${o}"| ${pm[o2.id||o2.name]}`);
    for (const ds of (d.dataStores||[])) for (const pn of (ds.processes||[])) L.push(`    ${pm[pn]||pn} --> ${dm[ds.name]}`);
    return L.join('\n');
  },

  // ===== Utilities =====
  _dl(el, name) {
    const svg = el.querySelector('svg');
    if (svg) {
      const data = new XMLSerializer().serializeToString(svg.cloneNode(true));
      const blob = new Blob([data], { type: 'image/svg+xml' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob); a.download = `${name.replace(/[^a-zA-Z0-9一-鿿_-]/g,'_')}.svg`;
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
      return;
    }
    // Fallback: look for inline SVG text
    const dc = el.querySelector('.diagram-container');
    if (dc && dc.innerHTML.includes('<svg')) {
      const blob = new Blob([dc.innerHTML], { type: 'image/svg+xml' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob); a.download = `${name.replace(/[^a-zA-Z0-9一-鿿_-]/g,'_')}.svg`;
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
    }
  },

  _fs(el) {
    const de = el.querySelector('.diagram-container');
    if (!de) return;
    const div = document.createElement('div');
    div.style.cssText = 'position:fixed;inset:0;z-index:9999;background:#fff;padding:40px;overflow:auto;';
    div.innerHTML = de.innerHTML;
    const btn = document.createElement('button');
    btn.textContent = '✕'; btn.style.cssText = 'position:fixed;top:16px;right:16px;font-size:24px;border:none;background:none;cursor:pointer;color:#333;z-index:1;';
    btn.onclick = () => document.body.removeChild(div);
    div.appendChild(btn);
    div.onclick = e => { if (e.target === div) document.body.removeChild(div); };
    document.body.appendChild(div);
  },
};

if (typeof module !== 'undefined' && module.exports) { module.exports = Diagrams; }
