const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('zotero-plugin/bootstrap.js', 'utf8');

async function run() {
  const item = {id:11, key:'ATTACHAA', libraryID:1, isFileAttachment:()=>true,
    getAnnotations:()=>[annotation]};
  const annotation = {key:'ANNOTAAA', hasTag:()=>true, setTags:()=>{}, saveTx:async()=>{}};
  const state = {annotations:[], primaryViewState:{pageIndex:0}, selectedAnnotationIDs:[]};
  const viewer = {currentPageNumber:1, getPageView:()=>({renderingState:3})};
  const reader = {itemID:11, _item:item, _initPromise:Promise.resolve(),
    _internalReader:{_state:state, _primaryView:{_iframeWindow:{PDFViewerApplication:{pdfViewer:viewer}},
      getAnnotationMeta:position=>{assert.equal(position.cloned,true); return {sortIndex:'native-sort-index'}}}},
    _getAnnotation:async a=>({id:a.key}),
    setAnnotations:async data=>{assert.equal(data[0],annotation); state.annotations=data.map(a=>({id:a.key}))},
    navigate:async location=>{viewer.currentPageNumber=location.pageIndex+1}};
  const zotero = {initializationPromise:Promise.resolve(), Libraries:{userLibraryID:1},
    Items:{getByLibraryAndKeyAsync:async()=>item},
    Reader:{_readers:[], open:async()=>{setTimeout(()=>zotero.Reader._readers.push(reader),20)}},
    getMainWindow:()=>({focus(){}})};
  const context = vm.createContext({console, setTimeout, clearTimeout, setInterval, clearInterval,
    dump(){}, Components:{utils:{cloneInto:value=>({...value,cloned:true})}}, Zotero:zotero, Services:{}, fetch:async()=>({ok:true,json:async()=>({})})});
  vm.runInContext(source, context);
  const result = await context.highlightAndOpenCard({status:'exact', card_id:'accept-1',
    attachment_key:'ATTACHAA', page_index:6, page_label:'7',rects:[[10,10,20,20]],quote:'real quote'});
  assert.equal(result.page_index,6);
  assert.equal(result.annotation_in_reader,true);
  assert.equal(result.page_rendered,true);
  assert.equal(annotation.annotationSortIndex,'native-sort-index');
  await assert.rejects(context.highlightAndOpenCard({status:'ambiguous'}), /唯一出处/);
  const waitOriginal = context.waitForValue;
  context.waitForValue = (check,message)=>waitOriginal(check,message,200);
  reader.navigate=async()=>{};
  viewer.currentPageNumber=1;
  await assert.rejects(context.highlightAndOpenCard({status:'exact',card_id:'accept-2',attachment_key:'ATTACHAA',
    page_index:6,rects:[[10,10,20,20]]}), /未确认目标页/);
  console.log('Reader bridge contract: delayed initialization, page verification, geometry refresh and wrong-page rejection passed');
}
run().catch(error=>{console.error(error); process.exitCode=1});
