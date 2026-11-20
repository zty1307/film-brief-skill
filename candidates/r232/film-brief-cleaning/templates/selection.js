// Offline selection is a material handoff, never an automatic model call.
const selectionPayload = DATA.selection;
const chosenSamples = new Set();
const selectionKey = selectionPayload ? 'film-selection-'+selectionPayload.origin_dataset_sha256 : '';
const selectionBar = document.getElementById('selectionBar');
const selectionSummary = document.getElementById('selectionSummary');
const exportSelection = document.getElementById('exportSelection');
const selectionMessage = document.getElementById('selectionMessage');
if (selectionPayload) {
  selectionBar.hidden = false;
  try {
    const stored = JSON.parse(localStorage.getItem(selectionKey)||'[]');
    const allowed = new Set(selectionPayload.groups.flatMap(g=>g.samples.map(s=>s.candidate_id)));
    if(Array.isArray(stored)) stored.filter(id=>allowed.has(id)).forEach(id=>chosenSamples.add(id));
  } catch (_) { /* File pages may disallow local storage. Export still works. */ }
}
function syncSelection() {
  if (!selectionPayload) return;
  const groups = selectionPayload.groups.filter(g=>g.samples.some(s=>chosenSamples.has(s.candidate_id)));
  selectionSummary.textContent = `已选 ${groups.length} 个观点 · ${chosenSamples.size} 条样本`;
  exportSelection.disabled = !groups.length;
  document.querySelectorAll('[data-select-sample]').forEach(input=>{
    input.checked = chosenSamples.has(input.dataset.selectSample);
  });
  document.querySelectorAll('[data-select-group]').forEach(input=>{
    const group = selectionPayload.groups.find(g=>g.original_index===Number(input.dataset.selectGroup));
    const count = group.samples.filter(s=>chosenSamples.has(s.candidate_id)).length;
    input.checked = count === group.samples.length;
    input.indeterminate = count > 0 && count < group.samples.length;
  });
  try {localStorage.setItem(selectionKey,JSON.stringify([...chosenSamples]));} catch (_) {}
}
function selectedPackage() {
  if(!selectionPayload || !chosenSamples.size) throw new Error('请先勾选观点和样本');
  const groups = selectionPayload.groups.map(g=>({...g,samples:g.samples.filter(s=>chosenSamples.has(s.candidate_id))})).filter(g=>g.samples.length);
  return {...selectionPayload, groups, exported_at:new Date().toISOString()};
}
content.addEventListener('change',e=>{
  const input = e.target;
  if(input.matches('[data-select-group]')) {
    const group = selectionPayload.groups.find(g=>g.original_index===Number(input.dataset.selectGroup));
    group.samples.forEach(s=>input.checked?chosenSamples.add(s.candidate_id):chosenSamples.delete(s.candidate_id));
  } else if(input.matches('[data-select-sample]')) {
    input.checked?chosenSamples.add(input.dataset.selectSample):chosenSamples.delete(input.dataset.selectSample);
  } else return;
  selectionMessage.textContent=''; syncSelection();
});
document.getElementById('clearSelection').addEventListener('click',()=>{
  chosenSamples.clear(); selectionMessage.textContent=''; syncSelection();
});
exportSelection.addEventListener('click',()=>{
  try {
    const packet = selectedPackage();
    const url = URL.createObjectURL(new Blob([JSON.stringify(packet,null,2)],{type:'application/json;charset=utf-8'}));
    const link = document.createElement('a');
    link.href=url; link.download=DATA.batches[current].name.replace(/[<>:"/\\|?*]/g,'_')+'-写稿选材.json';
    document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
    const sparse=packet.groups.filter(g=>g.samples.length<2).length;
    selectionMessage.textContent=sparse?`已导出。${sparse} 个观点只选了1条样本，写稿时可能不足以独立成段。`:'已导出。请把文件发送回 WorkBuddy，我收到后会直接写稿。也可在已安装本skill的新对话中发送文件继续。';
  } catch(error) {selectionMessage.textContent=error.message;}
});
syncSelection();
