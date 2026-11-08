/**
 * SearchTools Evidence Bridge - Zotero 7/8 Plugin (Phase 3 Full Implementation)
 * Connects SearchTools with Zotero:
 * 1. Jumps to exact PDF page & creates temporary evidence highlight with tag #evidence-temp
 * 2. One-click cleanup of all temporary evidence annotations
 * 3. Reverse creation of evidence cards from Reader text selection
 * 4. ItemPane Section: In-Zotero evidence cards management panel (highlight, cite, pin, writeback)
 * 5. Main window menu and Reader toolbar integrations
 */

var Zotero;
var pollIntervalId = null;
var chromeHandle = null;
var sectionRefreshCallback = null;
var pollBusy = false;
var lastSelectionPopup = null;
var lastCapture = null;

const SEARCHTOOLS_HOST = 'http://127.0.0.1:8765';
const BRIDGE_VERSION = '1.2.2';
const ADDON_ID = 'evidence-bridge@searchtools.org';
const PANE_ID = 'evidence-bridge-itempane-section';

function log(msg) {
  dump(`[Evidence Bridge] ${msg}\n`);
  if (typeof Zotero !== 'undefined' && Zotero.debug) {
    Zotero.debug(`[Evidence Bridge] ${msg}`);
  }
  try {
    httpPost(`${SEARCHTOOLS_HOST}/api/zotero/log`, { msg: String(msg) }).catch(() => {});
  } catch (e) {}
}

async function waitForZotero() {
  if (typeof Zotero !== 'undefined' && Zotero.initializationPromise) {
    await Zotero.initializationPromise;
    return Zotero;
  }
  let wm = Services.wm;
  let win = wm.getMostRecentWindow('navigator:browser');
  if (win && win.Zotero) {
    Zotero = win.Zotero;
    await Zotero.initializationPromise;
    return Zotero;
  }
  let windows = wm.getEnumerator('navigator:browser');
  var found = false;
  while (windows.hasMoreElements()) {
    let w = windows.getNext();
    if (w && w.Zotero) {
      Zotero = w.Zotero;
      found = true;
      break;
    }
  }
  if (!found) {
    await new Promise((resolve) => {
      let listener = {
        onOpenWindow(aWindow) {
          let domWindow = aWindow.QueryInterface(Ci.nsIInterfaceRequestor).getInterface(Ci.nsIDOMWindowInternal || Ci.nsIDOMWindow);
          domWindow.addEventListener('load', function onLoad() {
            domWindow.removeEventListener('load', onLoad, false);
            if (domWindow.Zotero) {
              wm.removeListener(listener);
              Zotero = domWindow.Zotero;
              resolve(void 0);
            }
          }, false);
        }
      };
      wm.addListener(listener);
    });
  }
  await Zotero.initializationPromise;
  return Zotero;
}

function showToast(title, msg, icon) {
  try {
    let pw = new Zotero.ProgressWindow({ closeOnClick: true });
    pw.changeHeadline(title);
    if (msg) {
      let iconUri = icon || 'chrome://zotero/skin/tick.png';
      let p = new pw.ItemProgress(iconUri, msg);
      p.setProgress(100);
    }
    pw.show();
    pw.startCloseTimer(3500);
  } catch (e) {
    dump(`[Evidence Bridge] Toast: ${title} - ${msg}\n`);
  }
}

function copyToClipboard(text) {
  try {
    let clipboardHelper = Cc['@mozilla.org/widget/clipboardhelper;1'].getService(Ci.nsIClipboardHelper);
    clipboardHelper.copyString(text);
    return true;
  } catch (e) {
    log(`Error copying to clipboard: ${e}`);
    return false;
  }
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

async function httpGet(url) {
  if (typeof fetch !== 'undefined') {
    let res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  }
  let res = await Zotero.HTTP.request('GET', url, { responseType: 'json' });
  return typeof res.response === 'object' ? res.response : JSON.parse(res.responseText);
}

async function httpPost(url, payload) {
  let headers = {
    'Content-Type': 'application/json',
    'X-SearchTools-Token': 'local-mcp'
  };
  if (typeof fetch !== 'undefined') {
    let res = await fetch(url, {
      method: 'POST',
      headers: headers,
      body: JSON.stringify(payload)
    });
    let data = await res.json();
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
  }
  let res = await Zotero.HTTP.request('POST', url, {
    headers: headers,
    body: JSON.stringify(payload),
    responseType: 'json'
  });
  return typeof res.response === 'object' ? res.response : JSON.parse(res.responseText);
}

async function handleCardOpen(cardId) {
  try {
    log(`Handling card open request for: ${cardId}`);
    let url = `${SEARCHTOOLS_HOST}/api/evidence-cards/locate?id=${encodeURIComponent(cardId)}`;
    let data = await httpGet(url);
    return await highlightAndOpenCard(data);
  } catch (e) {
    log(`Error in handleCardOpen: ${e}`);
    if (typeof Zotero !== 'undefined' && Zotero.logError) {
      Zotero.logError(e);
    }
    throw e;
  }
}

async function waitForValue(check, message, timeout = 15000) {
  let deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    let value = check();
    if (value) return value;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error(message);
}

async function getReadyReader(attachment) {
  let reader = await waitForValue(
    () => Zotero.Reader._readers.find(r => r.itemID === attachment.id && r._internalReader),
    'Zotero 阅读器未能初始化');
  let timer;
  try {
    await Promise.race([reader._initPromise, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('阅读器初始化超时')), 15000);
    })]);
  } finally {
    clearTimeout(timer);
  }
  return reader;
}

function inspectReader(reader, annotationKey) {
  let internal = reader._internalReader;
  let view = internal?._primaryView;
  let pdfWindow = view?._iframeWindow;
  let app = (pdfWindow?.wrappedJSObject || pdfWindow)?.PDFViewerApplication;
  let pageNumber = app?.pdfViewer?.currentPageNumber;
  let pageIndex = Number.isInteger(pageNumber) ? pageNumber - 1 : internal?._state?.primaryViewState?.pageIndex;
  let annotation = internal?._state?.annotations?.find(a => a.id === annotationKey);
  let pageView = Number.isInteger(pageIndex) ? app?.pdfViewer?.getPageView(pageIndex) : null;
  return {bridge_version: BRIDGE_VERSION, attachment_key: reader._item.key,
    page_index: pageIndex, annotation_key: annotationKey,
    annotation_in_reader: !!annotation, page_rendered: pageView?.renderingState === 3,
    selected_annotation: internal?._state?.selectedAnnotationIDs?.includes(annotationKey) || false};
}

async function highlightAndOpenCard(cardData) {
  if (!cardData || cardData.status !== 'exact') {
    throw new Error('完整引文未通过唯一出处验证，拒绝生成高亮');
  }
  if (cardData.spans && cardData.spans.length > 1) {
    let results = [];
    for (let i = cardData.spans.length - 1; i >= 0; i--) {
      results.push(await highlightAndOpenCard({ ...cardData, ...cardData.spans[i], spans: null,
        card_id: (cardData.card_id || cardData.id) + '-span-' + i }));
    }
    return {bridge_version: BRIDGE_VERSION, spans: results};
  }
  if (!cardData) return;
  let cardId = cardData.card_id || cardData.id;
  if (!cardId) return;

  await waitForZotero();
  let userLibraryID = Zotero.Libraries.userLibraryID;
  let attKey = cardData.attachment_key;
  let zotKey = cardData.zotero_key;

  let attachment = null;
  if (attKey) {
    attachment = await Zotero.Items.getByLibraryAndKeyAsync(userLibraryID, attKey);
  }
  if (!attKey && !attachment && zotKey) {
    let parentItem = await Zotero.Items.getByLibraryAndKeyAsync(userLibraryID, zotKey);
    if (parentItem) {
      let attIds = parentItem.getAttachments();
      if (attIds && attIds.length) {
        attachment = await Zotero.Items.getAsync(parseInt(attIds[0]));
      }
    }
  }

  if (!attachment || !attachment.isFileAttachment()) {
    log(`Attachment item not found for card ${cardId} (att: ${attKey}, zot: ${zotKey})`);
    showToast('⚠️ 未找到 PDF 附件', `卡片 ${cardId} 关联的附件不存在`);
    throw new Error('PDF attachment not found');
  }

  let pageIndex = parseInt(cardData.page_index != null ? cardData.page_index : 0);
  let pageLabel = String(cardData.page_label || (pageIndex + 1));
  let rects = cardData.rects || [];
  if ((!rects || rects.length === 0) && cardData.rects_json) {
    try {
      rects = typeof cardData.rects_json === 'string' ? JSON.parse(cardData.rects_json) : cardData.rects_json;
    } catch (e) {}
  }

  // Check if temporary annotation already exists for this card
  let cardTag = '#card-' + cardId;
  let annots = attachment.getAnnotations ? attachment.getAnnotations() : [];
  let targetAnnot = null;
  for (let a of annots) {
    if (a.hasTag('#evidence-temp') && a.hasTag(cardTag)) {
      targetAnnot = a;
      break;
    }
  }

  if (rects.length > 0) {
    try {
      let firstRect = rects[0] || [0, 0, 0, 0];
      let sortIndex = String(pageIndex).padStart(5, '0') + '|' +
        String(Math.max(0, Math.round(1000 - (firstRect[1] || 0)))).padStart(6, '0') + '|' +
        String(Math.max(0, Math.round(firstRect[0] || 0))).padStart(5, '0');

      targetAnnot = targetAnnot || new Zotero.Item('annotation');
      targetAnnot.libraryID = attachment.libraryID;
      targetAnnot.parentID = attachment.id;
      targetAnnot.annotationType = 'highlight';
      targetAnnot.annotationText = cardData.quote || '';
      targetAnnot.annotationComment =
        `【论证支撑证据卡 ${cardId}】\n要点：${cardData.summary || ''}\n检索相关性：${cardData.score || 0}/10`;
      targetAnnot.annotationColor = '#ffd400'; // Gold yellow
      targetAnnot.annotationPageLabel = pageLabel;
      targetAnnot.annotationSortIndex = sortIndex;
      targetAnnot.annotationPosition = JSON.stringify({
        pageIndex: pageIndex,
        rects: rects
      });
      targetAnnot.setTags([
        { tag: '#evidence-temp' },
        { tag: cardTag }
      ]);
      await targetAnnot.saveTx();
      log(`Created temporary evidence highlight annotation: ${targetAnnot.key}`);
    } catch (err) {
      log(`Could not create annotation: ${err}`);
      throw err;
    }
  }

  let location = {
    pageIndex: pageIndex
  };
  if (targetAnnot) {
    location.annotationID = targetAnnot.key;
  }

  log(`Opening file attachment ${attachment.key} (id: ${attachment.id}) at pageIndex ${pageIndex}, annot: ${location.annotationID || 'none'}`);
  await Zotero.Reader.open(attachment.id, location);
  let reader = await getReadyReader(attachment);
  if (!targetAnnot) throw new Error('没有可显示的已验证高亮');
  // Reader.setAnnotations accepts Zotero.Item objects, not reader JSON.
  await reader.setAnnotations([targetAnnot]);
  await reader.navigate(location);
  let receipt = await waitForValue(() => {
    let state = inspectReader(reader, targetAnnot.key);
    return state.page_index === pageIndex && state.annotation_in_reader && state.page_rendered ? state : null;
  }, '阅读器未确认目标页渲染或批注加载');
  // Prefer the running reader's own sort-index algorithm over guessed PDF height.
  let nativeMeta = reader._internalReader._primaryView.getAnnotationMeta?.(Components.utils.cloneInto({pageIndex, rects}, reader._iframeWindow));
  if (nativeMeta?.sortIndex && targetAnnot.annotationSortIndex !== nativeMeta.sortIndex) {
    targetAnnot.annotationSortIndex = nativeMeta.sortIndex;
    await targetAnnot.saveTx();
  }
  log('Reader verified: ' + JSON.stringify(receipt));

  showToast('🎯 论据定位完成', `第 ${pageLabel} 页 · ${cardId}`);

  let win = Zotero.getMainWindow();
  if (win && win.focus) {
    win.focus();
  }
  return receipt;
}

async function cleanupTempAnnotations() {
  try {
    await waitForZotero();
    log('cleanupTempAnnotations started');
    let count = 0;

    // Direct and reliable: inspect annotations in library
    try {
      let annotIds = await Zotero.DB.columnQueryAsync('SELECT itemID FROM itemAnnotations');
      log(`cleanupTempAnnotations: inspecting ${annotIds ? annotIds.length : 0} total annotations in library`);
      for (let rawId of (annotIds || [])) {
        let intId = parseInt(rawId);
        try {
          let it = await Zotero.Items.getAsync(intId);
          if (it && it.hasTag && (it.hasTag('#evidence-temp') || it.hasTag('evidence-temp'))) {
            log(`cleanupTempAnnotations: found temporary annotation ${it.id} (${it.key}), erasing...`);
            await it.eraseTx();
            count++;
          }
        } catch (itemErr) {
          log(`Error inspecting annotation item ${intId}: ${itemErr}`);
        }
      }
    } catch (annotErr) {
      log(`Error querying itemAnnotations: ${annotErr}`);
    }

    log(`Cleaned up ${count} temporary evidence annotations`);
    if (count > 0) {
      showToast('🧹 临时高亮已清理', `已成功清除 ${count} 条证据高亮标注，文库已恢复整洁`);
    } else {
      showToast('🧹 临时高亮已清理', '当前没有需要清理的临时高亮标注');
    }
    return count;
  } catch (e) {
    log(`Error in cleanupTempAnnotations: ${e}`);
    return 0;
  }
}

async function createChildNoteForItem(parentItem, card, bibEntry) {
  await waitForZotero();
  let cardId = card.id || card.card_id;
  let pageStr = card.page_label || (card.page_index != null ? card.page_index + 1 : card.page || '');
  let scoreStr = card.score != null ? `${card.score}/10` : '8.5/10';
  let claimStr = card.claim || '未命名论点';
  let quoteStr = card.quote || '';

  // Check if a child note already exists for this card
  let cardTag = '#card-' + cardId;
  let childNoteIds = parentItem.getNotes ? parentItem.getNotes() : [];
  let existingNote = null;
  for (let nId of childNoteIds) {
    let noteItem = await Zotero.Items.getAsync(parseInt(nId));
    if (noteItem && noteItem.hasTag && noteItem.hasTag(cardTag)) {
      existingNote = noteItem;
      break;
    }
  }

  let noteItem = existingNote || new Zotero.Item('note');
  noteItem.libraryID = parentItem.libraryID;
  noteItem.parentID = parentItem.id;

  let html = `
    <p><strong>【学术实证卡 ${escapeHtml(cardId)}】</strong></p>
    <p><strong>💡 支撑论点：</strong>${escapeHtml(claimStr)}</p>
    <p><strong>📜 论据原文：</strong></p>
    <blockquote style="margin: 6px 0 6px 12px; border-left: 3px solid #f59e0b; padding-left: 8px; color: #475569; font-style: italic;">
      ${escapeHtml(quoteStr)}
    </blockquote>
    <p><strong>📍 出处页码：</strong>第 ${escapeHtml(pageStr)} 页 | <strong>检索相关性：</strong>${escapeHtml(scoreStr)}</p>
    ${bibEntry ? `<p><strong>📚 GB/T 7714 规范引用：</strong><br>${escapeHtml(bibEntry)}</p>` : ''}
    <p><a href="zotero://evidence/open?card=${encodeURIComponent(cardId)}">👉 点击在 Zotero 阅读器中跳转并高亮该论据</a></p>
  `;

  noteItem.setNote(html);
  noteItem.setTags([
    { tag: '#evidence-card' },
    { tag: cardTag }
  ]);
  await noteItem.saveTx();
  log(`Saved child note for card ${cardId} (note ID: ${noteItem.id})`);
  return noteItem;
}

async function batchCreateNotesForItem(parentItem, cards) {
  if (!cards || !cards.length) return 0;
  showToast('📝 正在生成实证卡笔记...', `共 ${cards.length} 张卡片`);

  let cardIds = cards.map(c => c.id || c.card_id);
  let bibMap = {};
  try {
    let fRes = await httpPost(`${SEARCHTOOLS_HOST}/api/evidence-cards/format`, {
      card_ids: cardIds,
      style: 'gbt7714'
    });
    if (fRes && fRes.formatted) {
      let parsed = typeof fRes.formatted === 'string' ? JSON.parse(fRes.formatted) : fRes.formatted;
      if (parsed.bibliography) {
        bibMap['all'] = parsed.bibliography;
      }
    }
  } catch (e) {}

  let createdCount = 0;
  for (let c of cards) {
    try {
      await createChildNoteForItem(parentItem, c, bibMap['all'] || '');
      createdCount++;
    } catch (err) {
      log('Error creating child note for card: ' + err);
    }
  }
  showToast('✅ 实证卡笔记生成完成', `已写回 ${createdCount} 条子笔记`);
  return createdCount;
}

async function batchCopyCitations(cards) {
  if (!cards || !cards.length) return;
  let cardIds = cards.map(c => c.id || c.card_id);
  showToast('📋 正在生成引用...', `共 ${cardIds.length} 张卡片`);
  try {
    let fRes = await httpPost(`${SEARCHTOOLS_HOST}/api/evidence-cards/format`, {
      card_ids: cardIds,
      style: 'gbt7714'
    });
    let formatted = fRes && fRes.formatted ? (typeof fRes.formatted === 'string' ? JSON.parse(fRes.formatted) : fRes.formatted) : null;
    let bib = formatted && formatted.bibliography ? formatted.bibliography : '';
    let inlines = formatted && formatted.inline ? formatted.inline : '';
    let textToCopy = `【正文引用标注】\n${inlines}\n\n【GB/T 7714 参考文献列表】\n${bib}`;
    copyToClipboard(textToCopy);
    showToast('📋 引用已复制到剪贴板！', `已生成 ${cardIds.length} 张实证卡的规范引用`);
  } catch (err) {
    log('Error in batchCopyCitations: ' + err);
    showToast('❌ 生成引用失败', err.message);
  }
}

async function createCardFromSelection(reader, annotation, claim = '', options = {}) {
  let requestId = options.request_id || Services.uuid.generateUUID().toString();
  try {
    let text = String(annotation?.text || '').trim();
    if (!text || !annotation?.position?.rects?.length) throw new Error('缺少真实阅读器选文或坐标');
    let attachment = reader._item || reader.item;
    let parentItem = attachment?.parentID ? await Zotero.Items.getAsync(attachment.parentID) : null;
    if (!parentItem) throw new Error('选文附件没有对应的文献条目');
    let payload = {
      zotero_key: parentItem.key, attachment_key: attachment.key,
      quote: text, claim: claim.trim(), project: options.project || '', request_id: requestId,
      reader_selection: {position: JSON.parse(JSON.stringify(annotation.position)),
        pageLabel: annotation.pageLabel || '', sortIndex: annotation.sortIndex || '',
        capture_method: 'reader_selection_popup', bridge_version: BRIDGE_VERSION}
    };
    let data = await httpPost(`${SEARCHTOOLS_HOST}/api/evidence-cards/create-from-zotero`, payload);
    if (!data?.ok || !data.card) throw new Error(data?.error || '后端未确认建卡');
    lastCapture = {request_id: requestId, card_id: data.card.id, card: data.card};
    log('Reader capture verified: ' + JSON.stringify({card_id: data.card.id, page: data.card.page, request_id: requestId}));
    showToast('📌 已保存并核验选文', `${data.card.id} · p.${data.card.page}`);
    if (sectionRefreshCallback) sectionRefreshCallback();
    return data.card;
  } catch (error) {
    lastCapture = {request_id: requestId, error: String(error)};
    log('Reader capture failed: ' + error);
    showToast('❌ 保存证据卡失败', String(error));
    throw error;
  }
}


function renderEvidenceSectionSync(doc, body, item) {
  body.innerHTML = '';
  doc = body.ownerDocument || doc;

  let container = doc.createElement('div');
  container.className = 'evidence-bridge-section-container';
  container.style.cssText = 'padding: 6px 4px; font-family: system-ui, -apple-system, sans-serif; font-size: 12px;';

  // Section Header Action Bar
  let headerBar = doc.createElement('div');
  headerBar.style.cssText = 'display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; border-bottom: 1px solid #e2e8f0; padding-bottom: 6px;';

  let titleBadge = doc.createElement('span');
  titleBadge.id = 'evidence-bridge-title-badge';
  titleBadge.textContent = '🔬 实证卡库';
  titleBadge.style.cssText = 'font-weight: 600; color: #1e40af; background: #eff6ff; padding: 2px 8px; border-radius: 12px; font-size: 11px; border: 1px solid #bfdbfe;';
  headerBar.appendChild(titleBadge);

  let headerActions = doc.createElement('div');
  headerActions.style.cssText = 'display: flex; gap: 4px; align-items: center;';

  let btnWb = doc.createElement('button');
  btnWb.textContent = '🌐 工作台';
  btnWb.title = '在独立浏览器中打开完整实证卡工作台';
  btnWb.style.cssText = 'background: #f8fafc; border: 1px solid #cbd5e1; border-radius: 4px; padding: 2px 6px; font-size: 11px; cursor: pointer; color: #334155;';
  btnWb.onclick = () => {
    let targetKey = item ? (item.isAttachment && item.isAttachment() && item.parentID ? item.parentItem?.key : item.key) : '';
    Zotero.launchURL(`${SEARCHTOOLS_HOST}/workbench.html${targetKey ? '?q=' + encodeURIComponent(targetKey) : ''}`);
  };
  headerActions.appendChild(btnWb);

  let btnClean = doc.createElement('button');
  btnClean.textContent = '🧹 清理高亮';
  btnClean.title = '清理文库中所有临时生成的证据高亮标注';
  btnClean.style.cssText = 'background: #f8fafc; border: 1px solid #cbd5e1; border-radius: 4px; padding: 2px 6px; font-size: 11px; cursor: pointer; color: #334155;';
  btnClean.onclick = async () => {
    await cleanupTempAnnotations();
  };
  headerActions.appendChild(btnClean);

  let btnRefresh = doc.createElement('button');
  btnRefresh.textContent = '🔄';
  btnRefresh.title = '重新拉取本文证据卡';
  btnRefresh.style.cssText = 'background: #f8fafc; border: 1px solid #cbd5e1; border-radius: 4px; padding: 2px 6px; font-size: 11px; cursor: pointer; color: #334155;';
  btnRefresh.onclick = () => {
    if (sectionRefreshCallback) sectionRefreshCallback();
  };
  headerActions.appendChild(btnRefresh);

  headerBar.appendChild(headerActions);
  container.appendChild(headerBar);

  // Cards List Container with Loading State
  let listContainer = doc.createElement('div');
  listContainer.id = 'evidence-bridge-cards-list';
  listContainer.innerHTML = '<div style="padding: 12px; text-align: center; color: #64748b; font-size: 11.5px;">⏳ 正在读取本文实证卡数据...</div>';
  container.appendChild(listContainer);

  body.appendChild(container);
}

// ItemPane Section Asynchronous Render
async function renderEvidenceSectionAsync(doc, body, item, setSectionSummary) {
  let listContainer = body.querySelector('#evidence-bridge-cards-list');
  let titleBadge = body.querySelector('#evidence-bridge-title-badge');
  if (!listContainer) return;
  doc = body.ownerDocument || doc;

  let targetItem = item;
  if (item && item.isAttachment && item.isAttachment() && item.parentID) {
    try {
      targetItem = await Zotero.Items.getAsync(parseInt(item.parentID));
    } catch (e) {}
  }

  if (!targetItem || (targetItem.isRegularItem && !targetItem.isRegularItem())) {
    listContainer.innerHTML = '<div style="padding: 10px; color: #64748b; text-align: center;">请选中常规文献条目以查看其关联的实证卡。</div>';
    if (setSectionSummary) setSectionSummary('未选中文献');
    return;
  }

  let zotKey = targetItem.key;
  let cards = [];
  try {
    let data = await httpGet(`${SEARCHTOOLS_HOST}/api/evidence-cards/by-item?item=${encodeURIComponent(zotKey)}`);
    cards = (data && data.cards) || [];
  } catch (err) {
    listContainer.innerHTML = `
      <div style="padding: 12px; background: #fef2f2; border: 1px solid #fecaca; border-radius: 6px; color: #991b1b; text-align: center; font-size: 11.5px;">
        ⚠️ 无法连接至 SearchTools 服务<br>
        <span style="font-size: 10.5px; color: #b91c1c;">(http://127.0.0.1:8765)</span><br>
        <button id="evidence-bridge-retry-btn" style="margin-top: 6px; background: #fff; border: 1px solid #f87171; border-radius: 4px; padding: 2px 8px; font-size: 11px; cursor: pointer; color: #991b1b;">🔄 重试连接</button>
      </div>
    `;
    let retryBtn = listContainer.querySelector('#evidence-bridge-retry-btn');
    if (retryBtn) {
      retryBtn.onclick = () => {
        if (sectionRefreshCallback) sectionRefreshCallback();
      };
    }
    if (setSectionSummary) setSectionSummary('服务离线');
    return;
  }

  if (titleBadge) {
    titleBadge.textContent = `🔬 实证卡 (${cards.length})`;
  }
  if (setSectionSummary) {
    setSectionSummary(`${cards.length} 张`);
  }

  if (cards.length === 0) {
    listContainer.innerHTML = `
      <div style="padding: 14px 10px; background: #f8fafc; border: 1px dashed #cbd5e1; border-radius: 6px; text-align: center; color: #64748b; font-size: 11.5px;">
        📄 本文献尚未抽取实证卡片<br><br>
        <span style="font-size: 11px; color: #475569; display: block; line-height: 1.5;">
          💡 <strong>快速收录方法：</strong><br>
          在 PDF 阅读器中划选论据文本，点击弹出菜单中的 <strong>「📌 存为证据卡」</strong>，即可实时收录并自动建立反向关联！
        </span>
      </div>
    `;
    return;
  }

  listContainer.innerHTML = '';

  // Batch Toolbar
  let batchBar = doc.createElement('div');
  batchBar.style.cssText = 'display: flex; gap: 6px; margin-bottom: 8px; padding: 4px 6px; background: #f1f5f9; border-radius: 4px; align-items: center; justify-content: space-between;';

  let batchLeft = doc.createElement('span');
  batchLeft.textContent = `共 ${cards.length} 条论据支撑：`;
  batchLeft.style.cssText = 'font-size: 11px; color: #475569; font-weight: 500;';
  batchBar.appendChild(batchLeft);

  let batchRight = doc.createElement('div');
  batchRight.style.cssText = 'display: flex; gap: 4px;';

  let btnBatchCopy = doc.createElement('button');
  btnBatchCopy.textContent = '📋 批量复制引用';
  btnBatchCopy.title = '复制本文所有论据的 GB/T 7714 规范引用与正文标注';
  btnBatchCopy.style.cssText = 'background: #fff; border: 1px solid #cbd5e1; border-radius: 4px; padding: 2px 6px; font-size: 10.5px; cursor: pointer; color: #1e293b; font-weight: 500;';
  btnBatchCopy.onclick = async () => {
    await batchCopyCitations(cards);
  };
  batchRight.appendChild(btnBatchCopy);

  let btnBatchNote = doc.createElement('button');
  btnBatchNote.textContent = '📝 批量写回笔记';
  btnBatchNote.title = '将本文所有实证卡写回为 Zotero 子笔记';
  btnBatchNote.style.cssText = 'background: #fff; border: 1px solid #cbd5e1; border-radius: 4px; padding: 2px 6px; font-size: 10.5px; cursor: pointer; color: #1e293b; font-weight: 500;';
  btnBatchNote.onclick = async () => {
    await batchCreateNotesForItem(targetItem, cards);
  };
  batchRight.appendChild(btnBatchNote);

  batchBar.appendChild(batchRight);
  listContainer.appendChild(batchBar);

  // Render Card Items
  for (let c of cards) {
    let cardEl = doc.createElement('div');
    cardEl.className = 'evidence-card-box';
    cardEl.style.cssText = 'border: 1px solid #e2e8f0; border-radius: 6px; padding: 8px 10px; margin-bottom: 8px; background: #ffffff; box-shadow: 0 1px 2px rgba(0,0,0,0.03);';

    // Header: Badges & Pin
    let cHead = doc.createElement('div');
    cHead.style.cssText = 'display: flex; justify-content: space-between; align-items: center; margin-bottom: 5px;';

    let badges = doc.createElement('div');
    badges.style.cssText = 'display: flex; gap: 4px; align-items: center; flex-wrap: wrap;';

    let idBadge = doc.createElement('span');
    idBadge.textContent = c.id;
    idBadge.style.cssText = 'font-family: monospace; font-size: 10px; background: #f1f5f9; color: #334155; padding: 1px 5px; border-radius: 3px; border: 1px solid #cbd5e1;';
    badges.appendChild(idBadge);

    let scoreBadge = doc.createElement('span');
    scoreBadge.textContent = `⭐ ${c.score != null ? c.score : 8.5}/10`;
    scoreBadge.style.cssText = 'font-size: 10px; background: #f0fdf4; color: #166534; padding: 1px 5px; border-radius: 3px; border: 1px solid #bbf7d0; font-weight: 600;';
    badges.appendChild(scoreBadge);

    let pageLabel = String(c.page_label || (c.page_index != null ? c.page_index + 1 : c.page || '?'));
    let pageBadge = doc.createElement('span');
    pageBadge.textContent = `🎯 p.${pageLabel}`;
    pageBadge.style.cssText = 'font-size: 10px; background: #eff6ff; color: #1d4ed8; padding: 1px 5px; border-radius: 3px; border: 1px solid #bfdbfe; font-weight: 500;';
    badges.appendChild(pageBadge);

    cHead.appendChild(badges);

    // Pin Star Button
    let pinBtn = doc.createElement('button');
    let isPinned = Boolean(c.pinned);
    pinBtn.textContent = isPinned ? '★ 已收藏' : '☆ 收藏';
    pinBtn.style.cssText = isPinned
      ? 'background: #fef3c7; border: 1px solid #fde68a; color: #b45309; border-radius: 3px; padding: 1px 6px; font-size: 10px; cursor: pointer; font-weight: bold;'
      : 'background: #f8fafc; border: 1px solid #cbd5e1; color: #64748b; border-radius: 3px; padding: 1px 6px; font-size: 10px; cursor: pointer;';
    pinBtn.onclick = async (e) => {
      e.stopPropagation();
      try {
        let res = await httpPost(`${SEARCHTOOLS_HOST}/api/evidence-cards/pin`, { id: c.id, project: c.project });
        if (res && res.ok) {
          c.pinned = res.pinned;
          let pinnedNow = Boolean(res.pinned);
          pinBtn.textContent = pinnedNow ? '★ 已收藏' : '☆ 收藏';
          pinBtn.style.cssText = pinnedNow
            ? 'background: #fef3c7; border: 1px solid #fde68a; color: #b45309; border-radius: 3px; padding: 1px 6px; font-size: 10px; cursor: pointer; font-weight: bold;'
            : 'background: #f8fafc; border: 1px solid #cbd5e1; color: #64748b; border-radius: 3px; padding: 1px 6px; font-size: 10px; cursor: pointer;';
          showToast(pinnedNow ? '⭐ 已收藏实证卡' : '☆ 已取消收藏', c.id);
        }
      } catch (pinErr) {
        log(`Error toggling pin: ${pinErr}`);
      }
    };
    cHead.appendChild(pinBtn);
    cardEl.appendChild(cHead);

    // Claim
    let claimEl = doc.createElement('div');
    claimEl.style.cssText = 'font-weight: 600; font-size: 11.5px; color: #0f172a; margin: 4px 0 3px; line-height: 1.4;';
    claimEl.innerHTML = `💡 <span style="color:#0284c7;">论点：</span>${escapeHtml(c.claim || '未命名论点')}`;
    cardEl.appendChild(claimEl);

    // Quote
    let quoteEl = doc.createElement('div');
    quoteEl.style.cssText = 'font-size: 11px; line-height: 1.45; color: #334155; background: #f8fafc; border-left: 3px solid #f59e0b; padding: 5px 8px; border-radius: 3px; margin: 4px 0 6px; max-height: 80px; overflow-y: auto; word-break: break-word; font-style: italic;';
    quoteEl.textContent = `"${c.quote || ''}"`;
    cardEl.appendChild(quoteEl);

    // Functional Action Buttons
    let actRow = doc.createElement('div');
    actRow.style.cssText = 'display: flex; gap: 4px; align-items: center; justify-content: flex-end; margin-top: 6px;';

    // 1. Highlight and Jump
    let btnOpen = doc.createElement('button');
    btnOpen.textContent = '📖 原文高亮';
    btnOpen.title = '在 Zotero 阅读器中跳转到原句真实页码并自动高亮';
    btnOpen.style.cssText = 'background: #2563eb; color: #ffffff; border: none; border-radius: 4px; padding: 3px 8px; font-size: 11px; cursor: pointer; font-weight: 500; display: inline-flex; align-items: center;';
    btnOpen.onclick = async () => {
      showToast('🔍 正在跳转定位...', `第 ${pageLabel} 页`);
      if (c.locate && c.locate.rects) {
        await highlightAndOpenCard(c.locate);
      } else {
        await handleCardOpen(c.id);
      }
    };
    actRow.appendChild(btnOpen);

    // 2. Copy Citation (GB/T 7714)
    let btnCopy = doc.createElement('button');
    btnCopy.textContent = '📋 复制引用';
    btnCopy.title = '复制该卡片的 GB/T 7714 参考文献与正文引用格式';
    btnCopy.style.cssText = 'background: #f8fafc; border: 1px solid #cbd5e1; border-radius: 4px; padding: 3px 8px; font-size: 11px; cursor: pointer; color: #334155;';
    btnCopy.onclick = async () => {
      try {
        let fRes = await httpPost(`${SEARCHTOOLS_HOST}/api/evidence-cards/format`, {
          card_ids: [c.id],
          style: 'gbt7714'
        });
        let formatted = fRes && fRes.formatted ? (typeof fRes.formatted === 'string' ? JSON.parse(fRes.formatted) : fRes.formatted) : null;
        let bib = formatted && formatted.bibliography ? formatted.bibliography : '';
        let inline = formatted && formatted.inline ? formatted.inline : `[${c.id}]`;
        let textToCopy = `【正文引用】${inline}\n【参考文献】${bib}`;
        copyToClipboard(textToCopy);
        showToast('📋 已复制 GB/T 7714 引用！', inline);
      } catch (cpyErr) {
        copyToClipboard(`[${c.id}, p.${pageLabel}]`);
        showToast('📋 已复制卡片引用', `[${c.id}]`);
      }
    };
    actRow.appendChild(btnCopy);

    // 3. Writeback to Note
    let btnNote = doc.createElement('button');
    btnNote.textContent = '📝 写回笔记';
    btnNote.title = '将该实证卡写回为该条目的 Zotero 子笔记';
    btnNote.style.cssText = 'background: #f8fafc; border: 1px solid #cbd5e1; border-radius: 4px; padding: 3px 8px; font-size: 11px; cursor: pointer; color: #334155;';
    btnNote.onclick = async () => {
      btnNote.textContent = '⏳ 保存中...';
      btnNote.disabled = true;
      try {
        let fRes = await httpPost(`${SEARCHTOOLS_HOST}/api/evidence-cards/format`, {
          card_ids: [c.id],
          style: 'gbt7714'
        }).catch(() => null);
        let bib = '';
        if (fRes && fRes.formatted) {
          let parsed = typeof fRes.formatted === 'string' ? JSON.parse(fRes.formatted) : fRes.formatted;
          bib = parsed.bibliography || '';
        }
        await createChildNoteForItem(targetItem, c, bib);
        btnNote.textContent = '✅ 已写回';
        showToast('📝 论据子笔记已生成！', `卡片 ${c.id}`);
        setTimeout(() => { btnNote.textContent = '📝 写回笔记'; btnNote.disabled = false; }, 2000);
      } catch (noteErr) {
        log(`Error writing note: ${noteErr}`);
        btnNote.textContent = '❌ 失败';
        setTimeout(() => { btnNote.textContent = '📝 写回笔记'; btnNote.disabled = false; }, 2000);
      }
    };
    actRow.appendChild(btnNote);

    cardEl.appendChild(actRow);
    listContainer.appendChild(cardEl);
  }
}

function registerItemPaneSection(rootURI) {
  if (!Zotero.ItemPaneManager || !Zotero.ItemPaneManager.registerSection) {
    log('ItemPaneManager.registerSection not available');
    return;
  }

  try {
    Zotero.ItemPaneManager.registerSection({
      paneID: PANE_ID,
      pluginID: ADDON_ID,
      header: {
        l10nID: 'evidence-bridge-pane-header',
        icon: rootURI + 'icon.png'
      },
      sidenav: {
        l10nID: 'evidence-bridge-pane-sidenav',
        icon: rootURI + 'icon.png'
      },
      onInit: ({ paneID, doc, body, refresh }) => {
        log(`ItemPane section initialized: ${paneID}`);
        sectionRefreshCallback = refresh;
      },
      onDestroy: ({ paneID }) => {
        log(`ItemPane section destroyed: ${paneID}`);
        sectionRefreshCallback = null;
      },
      onItemChange: ({ item, setEnabled }) => {
        let enabled = !!(item && (item.isRegularItem ? item.isRegularItem() : true));
        setEnabled(enabled);
      },
      onRender: ({ doc, body, item }) => {
        renderEvidenceSectionSync(doc, body, item);
      },
      onAsyncRender: async ({ doc, body, item, setSectionSummary }) => {
        await renderEvidenceSectionAsync(doc, body, item, setSectionSummary);
      }
    });
    log('Registered ItemPane Section successfully');
  } catch (ex) {
    log(`Error registering ItemPane Section: ${ex}`);
  }
}

function unregisterItemPaneSection() {
  if (Zotero.ItemPaneManager && Zotero.ItemPaneManager.unregisterSection) {
    try {
      Zotero.ItemPaneManager.unregisterSection(PANE_ID);
      log(`Unregistered ItemPane Section: ${PANE_ID}`);
    } catch (e) {
      log(`Error unregistering section: ${e}`);
    }
  }
}

// Bounded integration check: creates a real native Reader selection and clicks the
// same popup button as the user. It accepts only a card in an explicit acceptance scope.
async function acceptanceCapture(action) {
  let data = await httpGet(`${SEARCHTOOLS_HOST}/api/evidence-cards/locate?id=${encodeURIComponent(action.card_id)}`);
  let card = action.card;
  if (!card || !String(card.tags || '').split(',').includes('acceptance') || !card.project) {
    throw new Error('反向验收只允许显式标记的独立验收卡');
  }
  await highlightAndOpenCard(data);
  let attachment = await Zotero.Items.getByLibraryAndKeyAsync(Zotero.Libraries.userLibraryID, data.attachment_key);
  let reader = await getReadyReader(attachment);
  let view = reader._internalReader._primaryView;
  await view._ensureBasicPageData(data.page_index);
  let chars = JSON.parse(JSON.stringify(view._pdfPages[data.page_index].chars));
  log('Acceptance: native character layer loaded');
  let selected = chars.map((char, index) => ({char, index})).filter(({char}) => {
    let box = char.rect;
    if (!box) return false;
    let center = [(box[0]+box[2])/2, (box[1]+box[3])/2];
    return data.rects.some(r => center[0]>=r[0]-1 && center[0]<=r[2]+1 && center[1]>=r[1]-1 && center[1]<=r[3]+1);
  });
  if (!selected.length) throw new Error('无法从真实阅读器字符层建立选区');
  let start = selected[0].index, end = selected[selected.length-1].index + 1;
  let nativeChars = chars.slice(start, end);
  let text = nativeChars.filter(c => !c.ignorable).map(c => c.c + ((c.spaceAfter || c.lineBreakAfter || c.paragraphBreakAfter) ? ' ' : '')).join('').trim();
  let position = {pageIndex: data.page_index, rects: data.rects};
  let meta = view.getAnnotationMeta(Components.utils.cloneInto(position, reader._iframeWindow));
  let ranges = [{pageIndex: data.page_index, position, text, sortIndex: meta.sortIndex,
    anchorOffset: start, headOffset: end, anchor: true, head: true, collapsed: false}];
  lastSelectionPopup = null;
  view._setSelectionRanges(Components.utils.cloneInto(ranges, reader._iframeWindow));
  view._render();
  log('Acceptance: native selection created, waiting for popup');
  let popup = await waitForValue(() => lastSelectionPopup?.reader?.itemID === reader.itemID && lastSelectionPopup.button.isConnected ? lastSelectionPopup : null,
    '真实划选弹窗没有渲染插件建卡按钮');
  let requestId = 'acceptance-' + action.action_id;
  popup.options = {project: card.project, request_id: requestId};
  popup.input.value = card.claim || '';
  lastCapture = null;
  log('Acceptance: clicking rendered reader capture button');
  popup.button.click();
  let captured = await waitForValue(() => lastCapture?.request_id === requestId ? lastCapture : null,
    '阅读器按钮未返回建卡结果', 25000);
  if (captured.error) throw new Error(captured.error);
  return {bridge_version: BRIDGE_VERSION, card_id: captured.card_id, page_index: captured.card.locate?.page_index,
    popup_rendered: true, native_character_count: nativeChars.length,
    selection_quote: text, original_card_id: action.card_id};
}

function startActionPoller() {
  if (pollIntervalId) return;
  pollIntervalId = setInterval(async () => {
    if (pollBusy) return;
    pollBusy = true;
    try {
      let data = await httpGet(`${SEARCHTOOLS_HOST}/api/zotero/actions`);
      for (let action of data.actions || []) {
        try {
          let result;
          if (action.action === 'open_card' && action.card_id) {
            result = action.locate ? await highlightAndOpenCard(action.locate) : await handleCardOpen(action.card_id);
          } else if (action.action === 'acceptance_capture') {
            result = await acceptanceCapture(action);
          } else if (action.action === 'cleanup') {
            result = await cleanupTempAnnotations();
          } else {
            throw new Error('未知的阅读器动作：' + action.action);
          }
          if (action.action_id) await httpPost(`${SEARCHTOOLS_HOST}/api/zotero/action-ack`, {action_id: action.action_id, result});
        } catch (error) {
          log('Reader action failed: ' + error + '\n' + (error.stack || ''));
          if (action.action_id) await httpPost(`${SEARCHTOOLS_HOST}/api/zotero/action-ack`, {action_id: action.action_id, error: String(error), result: {bridge_version: BRIDGE_VERSION}});
        }
      }
    } catch (error) {
      // Offline backends are retried on the next tick; no concurrent polling.
    } finally {
      pollBusy = false;
    }
  }, 1000);
}

function stopActionPoller() {
  if (pollIntervalId) {
    clearInterval(pollIntervalId);
    pollIntervalId = null;
  }
}

function onMainWindowLoad({ window }) {
  try {
    let doc = window.document;

    // 1. Inject FTL localization file
    if (window.MozXULElement && window.MozXULElement.insertFTLIfNeeded) {
      window.MozXULElement.insertFTLIfNeeded('evidence-bridge.ftl');
    }

    // 2. Attach Tools Menu Item
    let menu = doc.getElementById('menu_ToolsPopup');
    if (menu && !doc.getElementById('evidence-bridge-tools-menu')) {
      let sep = doc.createElement('menuseparator');
      sep.id = 'evidence-bridge-tools-sep';
      menu.appendChild(sep);

      let itemWb = doc.createElement('menuitem');
      itemWb.id = 'evidence-bridge-tools-wb-menu';
      itemWb.setAttribute('label', '🔬 打开学术实证工作台 (SearchTools)');
      itemWb.addEventListener('command', () => {
        Zotero.launchURL(`${SEARCHTOOLS_HOST}/workbench.html`);
      });
      menu.appendChild(itemWb);

      let itemClean = doc.createElement('menuitem');
      itemClean.id = 'evidence-bridge-tools-menu';
      itemClean.setAttribute('label', '🧹 清除证据临时高亮 (SearchTools)');
      itemClean.addEventListener('command', async () => {
        await cleanupTempAnnotations();
      });
      menu.appendChild(itemClean);
      log('Attached menuitems to Tools popup');
    }

    // 3. Attach Item Context Menu Item (zotero-itemmenu)
    let itemMenu = doc.getElementById('zotero-itemmenu');
    if (itemMenu && !doc.getElementById('evidence-bridge-itemmenu-entry')) {
      let sep = doc.createElement('menuseparator');
      sep.id = 'evidence-bridge-itemmenu-sep';
      itemMenu.appendChild(sep);

      let itemEntry = doc.createElement('menuitem');
      itemEntry.id = 'evidence-bridge-itemmenu-entry';
      itemEntry.setAttribute('label', '🔬 查看此文献的实证卡 (SearchTools)');
      itemEntry.addEventListener('command', () => {
        let selectedItems = Zotero.getActiveZoteroPane()?.getSelectedItems() || [];
        let item = selectedItems[0];
        let key = item ? (item.isAttachment() && item.parentID ? item.parentItem?.key : item.key) : '';
        Zotero.launchURL(`${SEARCHTOOLS_HOST}/workbench.html${key ? '?q=' + encodeURIComponent(key) : ''}`);
      });
      itemMenu.appendChild(itemEntry);

      let itemExportNote = doc.createElement('menuitem');
      itemExportNote.id = 'evidence-bridge-itemmenu-export-note';
      itemExportNote.setAttribute('label', '📝 导出此文献实证卡为 Zotero 笔记');
      itemExportNote.addEventListener('command', async () => {
        let selectedItems = Zotero.getActiveZoteroPane()?.getSelectedItems() || [];
        let target = selectedItems[0];
        if (!target) return;
        let parentTarget = target.isAttachment() && target.parentID ? target.parentItem : target;
        try {
          let data = await httpGet(`${SEARCHTOOLS_HOST}/api/evidence-cards/by-item?item=${encodeURIComponent(parentTarget.key)}`);
          let cards = (data && data.cards) || [];
          if (cards.length > 0) {
            await batchCreateNotesForItem(parentTarget, cards);
          } else {
            showToast('📄 无可用实证卡', '本文献在当前课题库中尚未抽取实证卡片');
          }
        } catch (e) {
          showToast('❌ 导出失败', String(e));
        }
      });
      itemMenu.appendChild(itemExportNote);
      log('Attached entries to zotero-itemmenu');
    }
  } catch (e) {
    log(`Error in onMainWindowLoad: ${e}`);
  }
}

function onMainWindowUnload({ window }) {
  try {
    let doc = window.document;
    if (window.MozXULElement && window.MozXULElement.removeFTLIfNeeded) {
      window.MozXULElement.removeFTLIfNeeded('evidence-bridge.ftl');
    }
    doc.getElementById('evidence-bridge-tools-menu')?.remove();
    doc.getElementById('evidence-bridge-tools-wb-menu')?.remove();
    doc.getElementById('evidence-bridge-tools-sep')?.remove();
    doc.getElementById('evidence-bridge-itemmenu-entry')?.remove();
    doc.getElementById('evidence-bridge-itemmenu-export-note')?.remove();
    doc.getElementById('evidence-bridge-itemmenu-sep')?.remove();
  } catch (e) {}
}

// Repair stale addon metadata through Mozilla's supported installer, rather than
// editing extensions.json or registering a fabricated addon record.
function reconcileRegisteredVersion(rootURI, registeredVersion) {
  if (registeredVersion === BRIDGE_VERSION || !String(rootURI).startsWith('jar:file:')) return;
  setTimeout(async () => {
    let temporary;
    try {
      let { AddonManager } = ChromeUtils.importESModule('resource://gre/modules/AddonManager.sys.mjs');
      let uri = Services.io.newURI(rootURI).QueryInterface(Ci.nsIJARURI);
      let source = uri.JARFile.QueryInterface(Ci.nsIFileURL).file;
      let directory = Services.dirsvc.get('TmpD', Ci.nsIFile);
      let name = 'evidence-bridge-' + Services.uuid.generateUUID().toString() + '.xpi';
      source.copyTo(directory, name);
      temporary = directory.clone();
      temporary.append(name);
      let install = await AddonManager.getInstallForFile(temporary);
      if (!install || install.addon.id !== ADDON_ID || install.addon.version !== BRIDGE_VERSION) {
        throw new Error('本地插件包身份或版本不匹配');
      }
      log('Refreshing registered plugin version via AddonManager: ' + BRIDGE_VERSION);
      await install.install();
    } catch (error) {
      log('Plugin version registration failed: ' + error);
    } finally {
      if (temporary?.exists()) temporary.remove(false);
    }
  }, 2000);
}

// Zotero 7/8 Plugin Lifecycle Hooks
async function startup(data, reason) {
  let rootURI = (data && data.rootURI) || 'chrome://evidence-bridge/';
  let version = (data && data.version) || '1.0.0';
  dump(`[Evidence Bridge] Starting SearchTools Evidence Bridge v${version}...\n`);

  await waitForZotero();
  log(`Zotero initialized, DataDir: ${Zotero.DataDirectory ? Zotero.DataDirectory.dir : 'unknown'}, registering bridge hooks...`);

  // Register Chrome locale resources for FTL
  try {
    let aomStartup = Cc['@mozilla.org/addons/addon-manager-startup;1'].getService(Ci.amIAddonManagerStartup);
    let manifestURI = Services.io.newURI(rootURI + 'manifest.json');
    chromeHandle = aomStartup.registerChrome(manifestURI, [
      ['locale', 'evidence-bridge', 'zh-CN', rootURI + 'locale/zh-CN/'],
      ['locale', 'evidence-bridge', 'en-US', rootURI + 'locale/en-US/']
    ]);
    log('Registered Chrome locale successfully');
  } catch (e) {
    log(`Notice on registerChrome: ${e}`);
  }

  // 1. Register Custom Protocol: zotero://evidence/open?card=...
  try {
    let handler = Services.io.getProtocolHandler('zotero').wrappedJSObject;
    if (handler && handler._extensions) {
      handler._extensions['zotero://evidence'] = {
        noContent: true,
        doAction: async function (uri) {
          try {
            let spec = uri.spec || uri.pathQueryRef || '';
            let m = spec.match(/[?&]card=([^&]+)/);
            let cardId = m ? decodeURIComponent(m[1]) : '';
            if (!cardId) {
              let parts = spec.split('?')[0].split('/').filter(Boolean);
              cardId = parts[parts.length - 1];
            }
            if (cardId) {
              await handleCardOpen(cardId);
            }
          } catch (e) {
            log(`Error in protocol handler: ${e}`);
          }
        },
        newChannel: function (uri) {
          this.doAction(uri);
        }
      };
      log('Registered zotero://evidence protocol extension');
    }
  } catch (ex) {
    log(`Error registering protocol handler: ${ex}`);
  }

  // 2. Start Action Poller with SearchTools
  startActionPoller();

  // 3. Register Reader UI Listeners (Selection popup + Reader toolbar)
  if (Zotero.Reader && Zotero.Reader.registerEventListener) {
    try {
      // Selection Popup: Add "📌 存为证据卡"
      Zotero.Reader.registerEventListener(
        'renderTextSelectionPopup',
        (event) => {
          let { reader, doc, params, append } = event;
          let container = doc.createElement('div');
          container.style.cssText = 'display:flex;gap:6px;flex-wrap:wrap;padding:4px;';
          let input = doc.createElement('input');
          input.className = 'evidence-bridge-claim-input';
          input.placeholder = '一个明确论点（留空则保存待整理摘录）';
          input.setAttribute('aria-label', '这段原文支撑的论点');
          input.style.cssText = 'min-width:230px;flex:1;';
          let btn = doc.createElement('button');
          btn.className = 'evidence-bridge-popup-btn';
          btn.textContent = '📌 存为证据卡';
          btn.title = '保存真实选文、坐标与论点，并回验 PDF 原文';
          let popup = {reader, doc, annotation: params.annotation, button: btn, input, options: {}};
          lastSelectionPopup = popup;
          btn.addEventListener('click', async event => {
            event.stopPropagation();
            if (btn.disabled) return;
            btn.disabled = true;
            try {
              await createCardFromSelection(reader, params.annotation, input.value, popup.options);
              btn.textContent = '已核验保存';
            } catch (error) {
              btn.disabled = false;
              btn.textContent = '保存失败，重试';
            }
          });
          container.append(input, btn);
          append(container);
        },
        ADDON_ID
      );

      // Reader Toolbar: Add "🔬 实证工作台" & "🧹 清除临时高亮"
      Zotero.Reader.registerEventListener(
        'renderToolbar',
        (event) => {
          let { doc, append, reader } = event;

          let btnWb = doc.createElement('button');
          btnWb.className = 'evidence-bridge-toolbar-wb-btn';
          btnWb.textContent = '🔬 实证工作台';
          btnWb.title = '打开 SearchTools 实证工作台查看与检索证据卡';
          btnWb.style.cssText =
            'background:none;border:1px solid #3b82f6;color:#2563eb;border-radius:4px;padding:2px 8px;font-size:11px;cursor:pointer;margin:0 4px;font-weight:500;';
          btnWb.addEventListener('click', () => {
            let att = reader._item || reader.item;
            let q = att ? (att.parentID ? att.parentItem?.key : att.key) : '';
            let url = `${SEARCHTOOLS_HOST}/workbench.html${q ? '?q=' + encodeURIComponent(q) : ''}`;
            Zotero.launchURL(url);
          });
          append(btnWb);

          let btnClean = doc.createElement('button');
          btnClean.className = 'evidence-bridge-toolbar-btn';
          btnClean.textContent = '🧹 清除证据临时高亮';
          btnClean.title = '一键清理所有临时生成的论据高亮标注，保持文库整洁';
          btnClean.style.cssText =
            'background:none;border:1px solid #94a3b8;color:#475569;border-radius:4px;padding:2px 8px;font-size:11px;cursor:pointer;margin:0 4px;';
          btnClean.addEventListener('click', async () => {
            await cleanupTempAnnotations();
          });
          append(btnClean);
        },
        ADDON_ID
      );
      log('Registered Reader event listeners successfully');
    } catch (e) {
      log(`Error registering Reader event listeners: ${e}`);
    }
  }

  // 4. Register ItemPane Section
  registerItemPaneSection(rootURI);

  // 5. Attach Menus in Main Window
  try {
    let win = Zotero.getMainWindow();
    if (win) {
      onMainWindowLoad({ window: win });
    }
  } catch (e) {
    log(`Error on attaching menus: ${e}`);
  }

  log('SearchTools Evidence Bridge build ' + BRIDGE_VERSION + ' started (registered version ' + version + ')');
  reconcileRegisteredVersion(rootURI, version);
}

function shutdown(data, reason) {
  log('Shutting down SearchTools Evidence Bridge...');
  stopActionPoller();
  Zotero?.Reader?._unregisterEventListenerByPluginID?.(ADDON_ID);
  unregisterItemPaneSection();

  let win = Zotero ? Zotero.getMainWindow() : null;
  if (win) {
    onMainWindowUnload({ window: win });
  }

  if (chromeHandle) {
    try {
      chromeHandle.destruct();
      chromeHandle = null;
    } catch (e) {}
  }
}

function install() {}
function uninstall() {}
