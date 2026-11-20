// Offline selection is a material handoff, never an automatic model call.
const selectionPayload = DATA.selection;
const chosenSamples = new Set();
const selectionKey = selectionPayload ? 'film-selection-'+selectionPayload.origin_dataset_sha256 : '';
const selectionBar = document.getElementById('selectionBar');
const selectionSummary = document.getElementById('selectionSummary');
const exportSelection = document.getElementById('exportSelection');
const clearSelection = document.getElementById('clearSelection');
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
  clearSelection.disabled = !groups.length;
  document.querySelectorAll('[data-select-sample]').forEach(input=>{
    input.checked = chosenSamples.has(input.dataset.selectSample);
  });
  try {localStorage.setItem(selectionKey,JSON.stringify([...chosenSamples]));} catch (_) {}
}
function selectedPackage() {
  if(!selectionPayload || !chosenSamples.size) throw new Error('请先勾选样本');
  const groups = selectionPayload.groups.map(g=>({...g,samples:g.samples.filter(s=>chosenSamples.has(s.candidate_id))})).filter(g=>g.samples.length);
  return {...selectionPayload, groups, exported_at:new Date().toISOString()};
}
content.addEventListener('change',e=>{
  const input = e.target;
  if (!input.matches('[data-select-sample]')) return;
  if (input.checked) chosenSamples.add(input.dataset.selectSample);
  else chosenSamples.delete(input.dataset.selectSample);
  selectionMessage.textContent=''; syncSelection();
});
clearSelection.addEventListener('click',()=>{
  chosenSamples.clear(); selectionMessage.textContent=''; syncSelection();
});
exportSelection.addEventListener('click',()=>{
  try {
    const packet = selectedPackage();
    const url = URL.createObjectURL(new Blob([JSON.stringify(packet,null,2)],{type:'application/json;charset=utf-8'}));
    const link = document.createElement('a');
    link.href=url; link.download=DATA.batches[current].name.replace(/[<>:"/\\|?*]/g,'_')+'-写稿选材.json';
    document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
    selectionMessage.textContent = '';
  } catch(error) {selectionMessage.textContent=error.message;}
});
syncSelection();
