const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
async function run(){
  const removed=[];
  const annotations=[
    {key:'own',annotationType:'highlight',annotationComment:'【论证支撑证据卡 card1】'},
    {key:'span',annotationType:'highlight',annotationComment:'【论证支撑证据卡 card1-span-2】'},
    {key:'other',annotationType:'highlight',annotationComment:'【论证支撑证据卡 card10】'},
    {key:'manual',annotationType:'highlight',annotationComment:'My highlight'},
    {key:'legacy',annotationType:'highlight',getTags:()=>[{tag:'#card-card1'}]}
  ];
  for(const a of annotations)a.eraseTx=async()=>removed.push(a.key);
  const context=vm.createContext({console,dump(){},setTimeout,clearTimeout,
    Zotero:{DB:{columnQueryAsync:async()=>annotations.map((_,i)=>i)},Items:{getAsync:async i=>annotations[i]},Reader:{_readers:[]}}});
  vm.runInContext(fs.readFileSync('zotero-plugin/bootstrap.js','utf8'),context);
  const result=await context.removeDeletedCardHighlights(['card1']);
  assert.equal(result.removed,3);
  assert.deepEqual(removed,['own','span','legacy']);
  assert.equal(context.annotationBelongsToDeletedCard(annotations[2],new Set(['card1'])),false);
  console.log('Highlight cleanup: exact card, cross-page spans, legacy tags and manual/other-card preservation passed');
}
run().catch(error=>{console.error(error);process.exitCode=1});
