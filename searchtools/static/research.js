// Research evidence audits use the same paragraph-opening endpoint as cards.
window.addEventListener('DOMContentLoaded', () => {
  const panel = document.createElement('section');
  const heading = document.createElement('h2');
  heading.textContent = '研究论点与上层二审';
  const refresh = document.createElement('button');
  refresh.textContent = '加载研究记录';
  const output = document.createElement('div');
  panel.append(heading, refresh, output);
  document.body.append(panel);
  async function call(operation, args) {
    const response = await fetch('/api/research', {method:'POST', headers:{'Content-Type':'application/json','X-SearchTools-Token':window.__SEARCHTOOLS_TOKEN__ || 'local-mcp'}, body:JSON.stringify({operation, arguments:args})});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || response.statusText);
    return result;
  }
  let sequence = 0;
  refresh.onclick = async () => {
    const request = ++sequence;
    output.replaceChildren();
    try {
      for (const run of await call('list', {})) {
        const section = document.createElement('details');
        const title = document.createElement('summary');
        title.textContent = run.topic;
        section.append(title);
        const audit = await call('audit', {run_id:run.id, refresh_sources:false});
        for (const binding of audit.bindings) {
          const row = document.createElement('div');
          const atom = audit.atoms.find(a => a.id === binding.atom_id);
          const state = audit.review_states?.find(s => s.binding_id === binding.id);
          const review = audit.reviews.find(r => r.id === state?.review_id);
          const label = document.createElement('p');
          label.textContent = (atom?.text || '') + ' — ' + (state?.source_deleted ? '证据卡已删除' : review ? review.decision + ' / ' + review.relation + '：' + review.reason : '等待上层二审');
          const open = document.createElement('button');
          open.textContent = state?.source_deleted ? '证据卡已删除' : '打开原文段落';
          open.disabled = Boolean(state?.source_deleted);
          open.onclick = async () => {
            try {
              const response = await fetch('/api/open-zotero', {method:'POST',headers:{'Content-Type':'application/json','X-SearchTools-Token':'local-mcp'},body:JSON.stringify({card_id:binding.card_id})});
              const result = await response.json();
              if (!response.ok) throw new Error(result.error);
              open.textContent = '已排队，等待阅读器';
              for (let i=0;i<15;i++) {
                await new Promise(resolve => setTimeout(resolve,1000));
                const state = await (await fetch('/api/zotero/action-status?id='+encodeURIComponent(result.action_id))).json();
                if (state.status==='done') {open.textContent='阅读器已定位'; return;}
                if (state.status==='failed') throw new Error(state.error);
              }
              throw new Error('阅读器回执超时');
            } catch (error) {open.textContent=error.message;}
          };
          row.append(label,open); section.append(row);
        }
        if (request === sequence) output.append(section);
      }
    } catch (error) {output.textContent=error.message;}
  };
  window.addEventListener('evidence-cards-changed', () => refresh.click());
});
