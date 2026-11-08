const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

async function run() {
  let section;
  const calls = [];
  const context = vm.createContext({console, dump(){}, fetch:async()=>({ok:true,json:async()=>({})}),
    Zotero:{ItemPaneManager:{registerSection(value){section=value}}}});
  vm.runInContext(fs.readFileSync('zotero-plugin/bootstrap.js','utf8'), context);
  context.renderEvidenceSectionAsync = async (...args) => calls.push(args);
  context.registerItemPaneSection('file:///test/');
  section.onInit({paneID:'test', refresh(){throw Error('Cached refresh must not be used')}});
  const first = {isConnected:true};
  const second = {isConnected:true};
  const item = {key:'TESTITEM'};
  await section.onAsyncRender({body:first,item,doc:{},setSectionSummary(){}});
  await section.onAsyncRender({body:second,item,doc:{},setSectionSummary(){}});
  calls.length = 0;
  await vm.runInContext('sectionRefreshCallback()',context);
  assert.equal(calls.length,2);
  first.isConnected=false;
  section.onDestroy({paneID:'first'});
  calls.length=0;
  await vm.runInContext('sectionRefreshCallback()',context);
  assert.equal(calls.length,1);
  assert.equal(calls[0][1],second);
  console.log('Panel refresh: direct async reload, multiple panels and detached panel cleanup passed');
}
run().catch(error=>{console.error(error);process.exitCode=1});
